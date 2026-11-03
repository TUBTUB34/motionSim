"""Local, evidence-based troubleshooting of UR logs and flight recordings.

Wall-clock association and sample-level localization are deliberately separate.
Rules offer checks, never certify root cause or remaining component life.
"""
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import zipfile

import numpy as np

from flight_data import FlightReport, JOINTS, MAX_TELEMETRY_BYTES, TELEMETRY_MEMBER, load_flight
from log_data import LogData, MAX_LOG_BYTES
from log_parser import read_events

MAX_FILES = 500
MAX_FLIGHTS = 40
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_SCAN_ENTRIES = 20_000
MAX_ANALYSIS_BYTES = 128 * 1024 * 1024
MATCH_SECONDS = 2.0
BURST_SECONDS = 2.0


@dataclass
class ChartEvidence:
    flight_path: str
    flight_name: str
    marker_elapsed: float | None
    window_start: float
    window_end: float
    scope: str
    source_sha256: str = ""


@dataclass
class JointEvidence:
    joint: str = ""
    flight_name: str = ""
    scope: str = "Recording-level"
    sample_count: int = 0
    channels: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    current_comparable_samples: int = 0
    current_both_bound_samples: int = 0
    current_excursion_samples: int = 0
    has_gaps: bool = False


@dataclass
class ProgramEvidence:
    program: str = ""
    installation: str = ""
    flight_name: str = ""
    scope: str = "Recording-level"
    script_lines: list[int] = field(default_factory=list)
    marker_line: int | None = None
    command: str = ""


@dataclass
class Incident:
    id: str = ""
    title: str = ""
    time: str = ""
    primary_code: str = ""
    supporting_codes: list[str] = field(default_factory=list)
    joints: list[str] = field(default_factory=list)
    flight_names: list[str] = field(default_factory=list)
    confidence: str = "Limited"
    window_reason: str = "No flight recording associated."
    observations: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    counterevidence: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    provenance: list[str] = field(default_factory=list)
    charts: list[ChartEvidence] = field(default_factory=list)
    joint_codes: dict[str, list[str]] = field(default_factory=dict)
    joint_evidence: list[JointEvidence] = field(default_factory=list)
    program_evidence: list[ProgramEvidence] = field(default_factory=list)
    association_verified: bool = False

    @property
    def occurred_at(self):
        return self.time

    @property
    def codes(self):
        return [c for c in [self.primary_code, *self.supporting_codes] if c]

    @property
    def limitations(self):
        return self.missing_evidence


@dataclass
class DiagnosticReport:
    source: str = ""
    incidents: list[Incident] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    robot_context: list[dict[str, str]] = field(default_factory=list)
    log_source_count: int = 0
    recording_count: int = 0


def _unique(values):
    return list(dict.fromkeys(values))


def _date(value):
    value = str(value).strip()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        pass
    # UR summary.log uses Java Date.toString-style local wall time. Only
    # unambiguous fixed abbreviations are accepted; CST/IST etc. are ambiguous.
    match = re.fullmatch(r"[A-Za-z]{3} ([A-Za-z]{3}) +(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) (UTC|GMT|CET|CEST|[+-]\d{4}) (\d{4})", value)
    if not match:
        return None
    month, day, hour, minute, second, zone, year = match.groups()
    months = dict(zip(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), range(1, 13)))
    try:
        offset = {"UTC": 0, "GMT": 0, "CET": 60, "CEST": 120}.get(zone)
        if offset is None:
            offset = (1 if zone[0] == "+" else -1) * (int(zone[1:3]) * 60 + int(zone[3:5]))
            if int(zone[3:5]) >= 60:
                return None
        return datetime(int(year), months[month], int(day), int(hour), int(minute), int(second), tzinfo=timezone(timedelta(minutes=offset)))
    except (KeyError, ValueError):
        return None


def _log_zone(value):
    value = str(value).strip()
    if not value:
        return None
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match:
        raise ValueError("Log UTC offset must be blank or use +HH:MM / -HH:MM, for example +02:00.")
    sign, hours, minutes = match.groups()
    hours, minutes = int(hours), int(minutes)
    if hours > 14 or minutes > 59 or (hours == 14 and minutes):
        raise ValueError("Log UTC offset must be between -14:00 and +14:00.")
    return timezone(timedelta(minutes=(hours * 60 + minutes) * (1 if sign == "+" else -1)))


def _delta(a, b):
    if a is None or b is None or (a.tzinfo is None) != (b.tzinfo is None):
        return None
    return (a - b).total_seconds()


def _event_order(event):
    stamp = _date(event.get("time"))
    if stamp is None:
        return (2, str(event.get("time", "")))
    if stamp.tzinfo is None:
        return (1, stamp.isoformat())
    return (0, stamp.astimezone(timezone.utc).isoformat())


def _identity(metadata):
    return str(metadata.get("serial") or metadata.get("robot_id") or "").strip()


def _events(logs, log_zone=None):
    if isinstance(logs, LogData):
        logs = [logs]
    result = []
    warnings = []
    for log in logs:
        if isinstance(log, dict):
            result.append(dict(log))
        else:
            for event in log.events:
                result.append({**event, "robot_id": event.get("robot_id") or _identity(log.metadata),
                               "serial": event.get("serial") or str(log.metadata.get("serial", ""))})
            if log.skipped:
                warnings.append(f"{log.source}: {log.skipped} malformed log records omitted.")
    seen = {}
    for event in result:
        stamp = _date(event.get("time"))
        if stamp is not None and stamp.tzinfo is None and log_zone is not None:
            event["time"] = stamp.replace(tzinfo=log_zone).isoformat(sep=" ")
        # Session and identity prevent equal uptime values after a reboot from merging.
        key = (event.get("robot_id", ""), event.get("session", 0), event.get("time"),
               event.get("uptime"), event.get("code"), event.get("joint"), event.get("message", ""))
        provenance = f"{event.get('source_path', 'Provided log')}:{event.get('source_line', '?')}"
        if key not in seen:
            seen[key] = {**event, "_provenance": [provenance]}
        elif provenance not in seen[key]["_provenance"]:
            seen[key]["_provenance"].append(provenance)
    return sorted(seen.values(), key=_event_order), warnings


def _groups(events):
    groups = []
    for event in events:
        # Bound to first event, not a chain extending indefinitely through adjacent faults.
        previous = groups[-1][0] if groups else None
        delta = _delta(_date(event.get("time")), _date(previous.get("time"))) if previous else None
        if (previous and delta is not None and 0 <= delta <= BURST_SECONDS
                and event.get("session", 0) == previous.get("session", 0)
                and event.get("robot_id", "") == previous.get("robot_id", "")):
            groups[-1].append(event)
        else:
            groups.append([event])
    return groups


def _marker(flight):
    """Only explicit incident uptime or a narrowly named recorded flag is a marker."""
    raw = flight.metadata.get("incident_uptime")
    if raw is not None:
        try:
            value = float(str(raw).removesuffix("s").strip())
            if math.isfinite(value) and flight.timestamps[0] <= value <= flight.timestamps[-1]:
                following = int(np.searchsorted(flight.timestamps, value))
                if 0 < following < len(flight.timestamps) and flight.timestamps[following] != value:
                    if (flight.time[following] - flight.time[following - 1] > flight.gap_threshold
                            or (flight.break_before is not None and flight.break_before[following])):
                        return None, "Recorded incident uptime falls inside a telemetry gap; the fault sample is unavailable."
                return value - float(flight.timestamps[0]), flight.metadata.get("incident_uptime_basis", "Explicit incident controller uptime in recording metadata.")
        except (ValueError, TypeError, IndexError):
            pass
        return None, "Recorded incident uptime is invalid or outside the recording."
    # Safety mode enums and generic bitfields are not booleans and are not guessed.
    markers = []
    for header in ("Protective stop", "Protective stop active", "Fault active", "Collision detected"):
        values = flight.columns.get(header)
        if values is None:
            continue
        values = np.asarray(values)
        for i in range(1, len(values)):
            if (values[i - 1] == 0 and values[i] == 1
                    and flight.time[i] - flight.time[i - 1] <= flight.gap_threshold
                    and not (flight.break_before is not None and flight.break_before[i])):
                markers.append(float(flight.time[i]))
    markers = _unique(markers)
    if len(markers) == 1:
        return markers[0], "Recorded fault flag changes from inactive to active at this sample."
    if len(markers) > 1:
        return None, "Multiple recorded fault transitions; incident sample is ambiguous."
    return None, "No explicit incident uptime or unambiguous fault transition; measurements describe the whole recording."


def _log_uptime(value):
    """UR log format observed locally: days/hours/minutes/seconds, e.g. 0d1h2m3.4s.

    An explicit seconds-only field is also supported. Bare numbers and unknown
    units are intentionally rejected rather than guessed to be seconds.
    """
    text = str(value).strip()
    full = re.fullmatch(r"(\d+)d(\d+)h(\d+)m(\d+(?:\.\d+)?)s", text)
    if full:
        day, hour, minute, second = map(float, full.groups())
        if hour >= 24 or minute >= 60 or second >= 60:
            return None
        value = day * 86400 + hour * 3600 + minute * 60 + second
    elif re.fullmatch(r"\d+(?:\.\d+)?s", text):
        value = float(text[:-1])
    else:
        return None
    return value if math.isfinite(value) else None


def _log_anchor(flight, group, incident):
    """Use log uptime only after unique wall-time/identity/session agreement."""
    current_marker, reason = _marker(flight)
    if current_marker is not None or not reason.startswith("No explicit incident uptime"):
        return flight
    serial = str(flight.metadata.get("serial", "")).strip()
    if not serial or any(str(e.get("serial", "")).strip() != serial for e in group):
        incident.missing_evidence.append("Logged uptime was not used to locate the fault because both sources do not carry the same nonempty robot serial.")
        return flight
    if any(_date(e.get("time")) is None or _date(e.get("time")).tzinfo is None for e in group):
        incident.missing_evidence.append("Logged uptime was not used to locate the fault because the log wall-clock timezone is unconfirmed.")
        return flight
    uptimes = [_log_uptime(e.get("uptime")) for e in group]
    if any(value is None for value in uptimes) or len(set(uptimes)) != 1:
        incident.missing_evidence.append("Logged uptime is missing, unrecognized or differs between entries in this burst; no single sample marker was chosen.")
        return flight
    uptime = uptimes[0]
    if not flight.sample_count or not flight.timestamps[0] <= uptime <= flight.timestamps[-1]:
        incident.missing_evidence.append("Logged fault uptime is outside this recording's controller-uptime span; a boot or clock mismatch is possible, so no marker was inferred.")
        return flight
    basis = ("Explicit fault uptime from the log, after a unique zoned wall-clock match, equal robot serial, "
             "one non-conflicting log session and an uptime inside the recording. This locates the logged event, not a proven initiating cause.")
    incident.provenance.append("Fault marker: logged controller uptime " + group[0]["uptime"] + " from " + group[0]["_provenance"][0])
    return replace(flight, metadata={**flight.metadata, "incident_uptime": str(uptime), "incident_uptime_basis": basis})


def _peak(values, mask):
    values = values[mask & np.isfinite(values)]
    return float(np.max(np.abs(values))) if len(values) else None


def _measure(flight, incident):
    # Missing-channel/derivative caches are temporary for this analysis, rather
    # than accumulating dozens of NaN arrays on every retained source recording.
    flight = replace(flight, _derived={})
    marker, reason = _marker(flight)
    start, end = (max(0.0, marker - 2.0), min(flight.duration, marker + 1.0)) if marker is not None else (0.0, flight.duration)
    mask = (flight.time >= start) & (flight.time <= end)
    scope = "Incident window" if marker is not None else "Recording-level"
    incident.window_reason = reason
    incident.charts.append(ChartEvidence(str(flight.path), flight.name, marker, start, end, scope))
    incident.observations.append(f"{scope} evidence from {flight.name}: elapsed {start:.3f}–{end:.3f} s, {int(mask.sum())} samples. {reason}")
    if marker is None:
        incident.missing_evidence.append("An exact fault sample is not established; recording peaks cannot be attributed to the instant of the stop.")
    else:
        incident.confidence = "Located in recording; cause unconfirmed"
    count = 0
    tracking_peaks = []
    syntheses = []
    gaps = np.r_[False, np.diff(flight.time) > flight.gap_threshold]
    if flight.break_before is not None:
        gaps |= flight.break_before
    for j, joint in enumerate(JOINTS):
        parts = []
        measured = JointEvidence(joint=joint, flight_name=flight.name, scope=scope,
                                 sample_count=int(mask.sum()), has_gaps=bool((gaps & mask).any()))
        for metric in ('position', 'velocity', 'acceleration', 'current', 'temperature'):
            if flight.source_header(metric, j):
                value = _peak(flight.series(metric, j), mask)
                if value is not None:
                    measured.channels.append(metric)
                    measured.metrics['peak_' + metric] = value
        for metric, unit in (("velocity", "rad/s"), ("acceleration", "rad/s²")):
            value = _peak(flight.series(metric, j), mask)
            if value is not None:
                kind = "derived " if metric == "acceleration" and not flight.source_header(metric, j) else ""
                parts.append(f"peak |{kind}{metric}| {value:.4g} {unit}")
        for metric, unit, label in (("position", "rad", "position tracking error"), ("velocity", "rad/s", "velocity tracking error"), ("current", "A", "actual−target current residual")):
            value = _peak(flight.series(metric, j) - flight.series(metric, j, target=True), mask)
            if value is not None:
                parts.append(f"peak |{label}| {value:.4g} {unit}")
                measured.metrics[metric + '_tracking_error' if metric != 'current' else 'current_residual'] = value
        current = flight.series("current", j)
        upper, lower = flight.series("current_upper", j), flight.series("current_lower", j)
        valid_upper = mask & np.isfinite(current) & np.isfinite(upper)
        valid_lower = mask & np.isfinite(current) & np.isfinite(lower)
        inverted = np.isfinite(upper) & np.isfinite(lower) & (lower > upper)
        if (inverted & mask).any():
            incident.missing_evidence.append(f"{joint}: inverted recorded current envelopes were excluded from comparisons.")
        valid_upper &= ~inverted
        valid_lower &= ~inverted
        valid = valid_upper | valid_lower
        outside = (valid_upper & (current > upper)) | (valid_lower & (current < lower))
        measured.current_comparable_samples = int(valid.sum())
        measured.current_both_bound_samples = int((valid_upper & valid_lower).sum())
        measured.current_excursion_samples = int(outside.sum())
        if measured.channels:
            incident.joint_evidence.append(measured)
        if valid.any():
            both = valid_upper & valid_lower
            envelope_label = "recorded envelope" if np.array_equal(both, valid) else "recorded current bound(s) (one-sided where only one bound is available)"
            parts.append(f"current outside {envelope_label} at {int(outside.sum())}/{int(valid.sum())} comparable samples")
            if outside.any():
                incident.hypotheses.append(f"{joint} current exceeds the {envelope_label} in the {scope.lower()}; investigate load/contact and configuration, without inferring a failed joint from current alone.")
                incident.actions.insert(0, f"Prioritize {joint}'s load and clearance review against the recorded current-bound excursion: verify tool/workpiece mass and center of gravity, then inspect the corresponding motion stage and external contact/cabling.")
            elif both.any():
                syntheses.append(f"{joint} current stays within the recorded lower/upper envelope at all {int(both.sum())} comparable samples in this {scope.lower()}. This does not rule out contact or unrecorded transients.")
        tracking = np.abs(flight.series("velocity", j) - flight.series("velocity", j, target=True))
        peak_tracking = _peak(tracking, mask)
        if peak_tracking is not None:
            tracking_peaks.append((peak_tracking, joint))
        # Within-recording comparison is deliberately not labeled a UR threshold.
        finite_tracking = mask & np.isfinite(tracking)
        baseline = finite_tracking & (flight.time < marker) if marker is not None else finite_tracking
        if baseline.sum() >= 3 and finite_tracking.sum() >= 5 and outside.any():
            baseline_peak = float(np.percentile(tracking[baseline], 95 if marker is not None else 75))
            simultaneous = outside & finite_tracking & (tracking > baseline_peak)
            if simultaneous.any():
                reference = "pre-marker 95th percentile" if marker is not None else "recording's 75th percentile"
                syntheses.append(f"{joint}: current-bound excursions coincide with velocity tracking error above the {reference} ({baseline_peak:.4g} rad/s) at {int(simultaneous.sum())} samples. This links the load/current and tracking observations in time; the comparison is descriptive, not a UR fault threshold or proof of contact.")
                incident.actions.insert(0, f"Inspect {joint}'s motion and load state where the current excursion and tracking mismatch coincide, before considering a component explanation.")
        if marker is not None:
            velocity = flight.series("velocity", j)
            pre = _peak(velocity, mask & (flight.time < marker))
            post = _peak(velocity, mask & (flight.time >= marker))
            if pre is not None and post is not None:
                parts.append(f"pre/post peak |velocity| {pre:.4g}/{post:.4g} rad/s")
            pre_valid = mask & (flight.time < marker) & np.isfinite(velocity)
            post_valid = mask & (flight.time >= marker) & np.isfinite(velocity)
            if pre_valid.sum() >= 3 and post_valid.sum() >= 3:
                before = float(np.median(np.abs(velocity[pre_valid])))
                after = float(np.median(np.abs(velocity[post_valid])))
                if before > 0 and after < before * .5:
                    syntheses.append(f"{joint} median absolute speed falls from {before:.4g} to {after:.4g} rad/s across the recorded marker. That reduction is consistent with stopping behavior; it does not identify what initiated the stop.")
        if parts:
            incident.observations.append(f"{joint}: " + "; ".join(parts) + ".")
            count += 1
    if len(tracking_peaks) > 1:
        value, joint = max(tracking_peaks)
        syntheses.insert(0, f"Among {len(tracking_peaks)} joints with paired velocity channels, {joint} has the largest absolute velocity tracking mismatch ({value:.4g} rad/s) in this {scope.lower()}. Use that location to focus trace review; this comparison is not normalized to each joint's limits and does not identify a failed joint.")
    if syntheses:
        incident.observations[1:1] = syntheses
    if not count:
        incident.missing_evidence.append("No usable joint motion/current channels are present; telemetry cannot corroborate a mechanical explanation.")
    incident.counterevidence.append("Velocity, acceleration and current peaks are observations, not proof of a model-specific safety-limit violation. Current residuals are not calibrated contact force.")
    incident.counterevidence.append("Deceleration and tracking changes at or after a protective stop may be consequences of the stop, rather than its initiating cause.")
    if (gaps & mask).any():
        incident.missing_evidence.append("The analyzed window contains data gaps or discarded rows; derivative calculations do not bridge those breaks and the transient may be incompletely captured.")
    for key, label in (("program", "Program"), ("installation", "Installation"), ("payload_kg", "Configured payload (kg)"), ("center_of_gravity", "Configured center of gravity (m)"), ("tcp_offset", "TCP offset (m, rad)"), ("software_version", "Controller software")):
        if flight.metadata.get(key):
            incident.observations.append(f"{label}: {flight.metadata[key]}.")
    line_header = next((h for h in ("Script control line", "Script line", "Script line number", "Script line number [line]") if h in flight.columns), None)
    program = ProgramEvidence(program=flight.metadata.get('program', ''),
                              installation=flight.metadata.get('installation', ''),
                              flight_name=flight.name, scope=scope)
    if line_header:
        lines = _unique(int(v) for v in flight.columns[line_header][mask] if np.isfinite(v) and v > 0 and v == int(v))
        program.script_lines = sorted(lines)
        if lines:
            incident.observations.append(f"Script lines observed in this {scope.lower()}: " + ", ".join(map(str, lines[:20])) + (" (additional lines omitted)." if len(lines) > 20 else "."))
            if marker is not None:
                index = int(np.argmin(np.abs(flight.time - marker)))
                value = flight.columns[line_header][index]
                script = flight.script.splitlines()
                if np.isfinite(value) and value == int(value) and value > 0:
                    program.marker_line = int(value)
                if np.isfinite(value) and value == int(value) and 1 <= int(value) <= len(script):
                    program.command = script[int(value)-1].strip()[:400]
                    incident.observations.append(f"Recorded script line {int(value)} near marker: {script[int(value)-1].strip()[:400]}")
    else:
        incident.missing_evidence.append("Executed script-line channel is missing; source script text alone does not establish which command caused a stop.")
    incident.program_evidence.append(program)
    incident.missing_evidence.extend(flight.warnings)
    if not any(flight.source_header("current_upper", j) or flight.source_header("current_lower", j) for j in range(6)):
        incident.missing_evidence.append("No recorded current envelope is available; no current-limit exceedance is asserted.")


def _guidance(incident):
    from diagnostic_guidance import guidance_for_codes
    guide = guidance_for_codes(incident.codes)
    incident.title = guide["title"]
    for name in ("hypotheses", "actions", "counterevidence"):
        getattr(incident, name).extend(guide[name])
    return guide["references"]


def analyze_data(logs, flights, source="Provided data", *, log_utc_offset=""):
    """Analyze already loaded data without disk/network access or robot commands."""
    report = DiagnosticReport(source=source)
    logs = [logs] if isinstance(logs, LogData) else list(logs)
    report.log_source_count = sum(isinstance(log, LogData) for log in logs)
    if not report.log_source_count:
        report.log_source_count = len({log.get('source_path', 'Provided log') for log in logs if isinstance(log, dict)})
    events, report.warnings = _events(logs, _log_zone(log_utc_offset))
    groups = _groups(events)
    unique_flights = {}
    for flight in flights:
        # Loaded files from the same path are never analyzed twice.
        unique_flights.setdefault(str(flight.path), flight)
    flights = list(unique_flights.values())
    report.recording_count = len(flights)
    contexts = [log.metadata for log in logs if isinstance(log, LogData)] + [flight.metadata for flight in flights]
    report.robot_context = [dict(items) for items in dict.fromkeys(
        tuple((key, str(context[key])) for key in ('serial', 'robot_id', 'model', 'software_version') if context.get(key))
        for context in contexts) if items]
    candidates = {}
    ambiguous = set()
    for fi, flight in enumerate(flights):
        stamp = _date(flight.metadata.get("incident_time"))
        identity = _identity(flight.metadata)
        matches = []
        for gi, group in enumerate(groups):
            robot = group[0].get("robot_id", "")
            if identity and robot and identity != robot:
                continue
            deltas = [_delta(stamp, _date(e.get("time"))) for e in group]
            if any(delta is not None and abs(delta) <= MATCH_SECONDS for delta in deltas):
                matches.append(gi)
        if len(matches) == 1:
            candidates.setdefault(matches[0], []).append(fi)
        elif len(matches) > 1:
            ambiguous.add(fi)
    used = set()
    refs = []
    program_groups = Counter()
    for gi, group in enumerate(groups):
        codes = _unique(e.get("code", "") for e in group if e.get("code"))
        incident = Incident(id=f"INC-{len(report.incidents)+1:03d}", time=group[0].get("time", ""), primary_code=codes[0] if codes else "", supporting_codes=codes[1:], joints=_unique(e.get("joint", "Unknown") for e in group))
        incident.joint_codes = {joint: _unique(e['code'] for e in group if e.get('joint') == joint and e.get('code'))
                                for joint in incident.joints if joint in JOINTS}
        refs.extend(_guidance(incident))
        incident.observations.append(f"The log records {' followed by '.join(codes)} affecting {', '.join(incident.joints)}. The first recorded code is the reporting anchor, not a proven initiating cause.")
        if len(group) > 1:
            incident.observations.append(f"{len(group)} related log entries within {BURST_SECONDS:g} seconds of the first entry are treated as one fault burst; a cascade is possible, but causal order is not established.")
        incident.provenance = _unique(p for e in group for p in e["_provenance"])
        for e in group:
            if e.get("message"):
                incident.observations.append(f"Controller message ({e['code']}, {e.get('time', 'time unavailable')}, uptime {e.get('uptime', 'unavailable')}): {e['message'][:600]}")
        matched = candidates.get(gi, [])
        if len(matched) == 1:
            fi = matched[0]
            used.add(fi)
            flight = flights[fi]
            incident.flight_names.append(flight.name)
            incident.provenance.append(str(flight.path))
            incident.confidence = "Wall-clock association; cause unconfirmed"
            incident.association_verified = bool(_identity(flight.metadata) and group[0].get('robot_id')
                                                 and _identity(flight.metadata) == group[0]['robot_id'])
            incident.observations.append(f"Flight incident wall time agrees with this fault burst within {MATCH_SECONDS:g} seconds. This is an association, not a conversion of controller uptime to UTC.")
            if not (_identity(flight.metadata) and group[0].get("robot_id")):
                incident.missing_evidence.append("Robot identity is missing from one source; wall-clock association is provisional and requires operator confirmation that both files came from the same robot.")
            _measure(_log_anchor(flight, group, incident), incident)
            if flight.metadata.get("program"):
                program_groups[(re.sub(r"A\d+$", "", incident.primary_code), flight.metadata["program"])] += 1
        else:
            incident.missing_evidence.append("No unique compatible flight recording matches this log incident. " + ("Multiple recordings share the incident time." if matched else "Check robot identity, incident wall time, clock/timezone and recording availability."))
        report.incidents.append(incident)
    for fi, flight in enumerate(flights):
        if fi in used:
            continue
        incident = Incident(id=f"REC-{fi+1:03d}", title="Unmatched recording: evidence review", time=flight.metadata.get("incident_time", ""), flight_names=[flight.name], provenance=[str(flight.path)])
        incident.missing_evidence.append("Recording has no unique log incident association; do not attribute its measurements to an unrelated log fault.")
        if _date(flight.metadata.get("incident_time")) is None:
            incident.missing_evidence.append("Flight incident wall time is missing or its format/timezone is unrecognized; reporting time and filename were not substituted.")
        if fi in ambiguous or any(fi in values and len(values) > 1 for values in candidates.values()):
            incident.missing_evidence.append("The wall-clock match is ambiguous; recordings/log sessions are kept separate.")
        _measure(flight, incident)
        incident.actions.append("Confirm this recording's robot identity and incident time, then obtain the matching current and rotated controller logs.")
        report.incidents.append(incident)
    for incident in report.incidents:
        for name in ("observations", "hypotheses", "counterevidence", "actions", "missing_evidence", "provenance"):
            setattr(incident, name, _unique(getattr(incident, name)))
    report.sources = _unique([p for e in events for p in e["_provenance"]] + [str(f.path) for f in flights])
    report.references = [{"title": "Diagnostic guidance source", "url": url} for url in _unique(refs)]
    report.coverage = [f"{len(events)} unique diagnostic log events grouped into {len(groups)} controller-event bursts; {len(flights)} recordings reviewed, {len(used)} uniquely associated.", "Only supplied files are analyzed. Missing log history is not evidence of fault-free operation; counts are not normalized by operating hours. Unrecognized codes require manual classification and are not automatically proof of a fault."]
    if str(log_utc_offset).strip():
        report.coverage.append(f"Operator-supplied UTC offset {str(log_utc_offset).strip()} was applied only to log timestamps without a timezone. Already-zoned timestamps were preserved; confirm the offset for each recording date, including daylight-saving changes.")
    else:
        report.coverage.append("No log timezone was assumed. Zoned flight incident times cannot match unzoned log times; supply the historical log UTC offset in Advanced options when needed.")
    if events:
        report.coverage.append(f"Parsed diagnostic event span: {events[0].get('time', 'unknown')} to {events[-1].get('time', 'unknown')}. This is observed event coverage, not continuous operating time.")
    for flight in flights:
        report.coverage.append(f"{flight.name}: robot {_identity(flight.metadata) or 'identity unavailable'}; model {flight.model or flight.metadata.get('model') or 'unknown'}; software {flight.metadata.get('software_version') or 'unknown'}; incident time {flight.metadata.get('incident_time') or 'unavailable'}." + (f" Model basis: {flight.metadata['model_basis']}." if flight.metadata.get('model_basis') else ""))
    if report.incidents:
        families = {e.get("family") or re.sub(r"A\d+$", "", e.get("code", "")) for e in events}
        if families & {"C153", "C157", "C158", "C159"}:
            report.summary.append("The controller history reports collision detection or path deviation. Prioritize contact/clearance, active payload and center of gravity, then commanded motion; the error label alone cannot distinguish physical contact from a load-model or motion-demand mismatch.")
        if families & {"C173", "C174"}:
            report.summary.append("Torque-demand indications make the transition into the affected move important: compare acceleration and blend changes before the stop with deceleration afterward. A large post-stop transient is not evidence that the joint caused the stop.")
        if families & {"C163"}:
            report.summary.append("Repeated protective-stop indications call for resolving the application issue and escalating persistent unexplained stops with the original support files; repeated resets do not establish that the application is healthy.")
        report.summary.append("Review the evidence and checks for each incident before selecting a corrective action. The report separates controller messages, measured behavior and possible explanations; it does not certify a root cause.")
        if used:
            matched_label = 'fault burst has' if len(used) == 1 else 'fault bursts have'
            report.summary.append(f"{len(used)} {matched_label} a compatible flight recording. Sample-level fault localization is stated separately for each incident.")
        if len(groups) > len(used):
            report.summary.append("Some logged incidents lack a unique recording match, so their motion behavior cannot be reconstructed from the available files.")
    else:
        report.summary.append("No interpretable controller events or flight recordings were available. This does not demonstrate that the robot operated without faults.")
    repetitions = Counter(family for g in groups for family in {e.get("family") or re.sub(r"A\d+$", "", e.get("code", "")) for e in g})
    for family, count in repetitions.items():
        if count > 1:
            report.patterns.append(f"{family} recurs in the supplied history. Compare the affected programs, loads and motion stages across incidents before treating it as a component-specific problem; the files do not establish exposure or remaining joint life.")
    for (family, program), count in program_groups.items():
        if count > 1:
            report.patterns.append(f"{family} is associated with program {program} in {count} distinct logged bursts with uniquely matched recordings. Review the repeated process stage, payload changes and clearance before attributing recurrence to hardware; program exposure and unrecorded runs are unknown.")
    coincident = [incident for incident in report.incidents if any("current-bound excursions coincide" in text for text in incident.observations)]
    if coincident:
        report.patterns.append(f"{len(coincident)} recording review(s) show current-bound excursions and increased tracking error at the same samples. This combined evidence prioritizes load, contact and commanded-motion checks. Refer to each incident's time scope; a recording-level association does not locate the fault sample.")
    report.actions = _unique(action for incident in report.incidents for action in incident.actions)
    report.actions.sort(key=lambda action: not action.startswith(("Inspect ", "Prioritize ")))
    return report


def _digest(path, check_cancel):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if check_cancel:
                check_cancel()
            value.update(chunk)
    return value.hexdigest()


def analyze_directory(path, check_cancel=None, progress=None, *, log_utc_offset=""):
    """Bounded recursive local discovery; never unpack or execute supplied programs."""
    root = Path(path).expanduser().resolve()
    _log_zone(log_utc_offset)
    if not root.is_dir():
        raise ValueError("Choose a directory containing robot logs and flight reports.")
    warnings, coverage, log_paths, flights = [], [], [], []
    seen, total, scanned = set(), 0, 0
    paths = []
    source_hashes = {}
    manifest = {}
    manifest_path = root / "diagnostic_manifest.json"
    if manifest_path.is_file():
        try:
            if manifest_path.stat().st_size > 1024 * 1024:
                raise ValueError("manifest exceeds 1 MiB")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be an object")
            warnings.extend(str(w) for w in manifest.get("warnings", []) if isinstance(w, str))
            if manifest.get("status") != "complete":
                warnings.append("Robot collection was incomplete; this report covers only the saved files.")
        except (OSError, ValueError) as error:
            warnings.append(f"Collection manifest could not be read ({error}).")
            manifest = {}
    analysis_bytes = 0
    stop = False
    for directory, dirs, names in os.walk(root, followlinks=False):
        scanned += 1
        if scanned > MAX_SCAN_ENTRIES:
            warnings.append(f"Discovery stopped after {MAX_SCAN_ENTRIES} directory entries; coverage is partial.")
            break
        dirs[:] = sorted(d for d in dirs if not Path(directory, d).is_symlink() and d not in (".git", ".venv", "node_modules"))
        for name in sorted(names):
            if check_cancel:
                check_cancel()
            scanned += 1
            if scanned > MAX_SCAN_ENTRIES:
                warnings.append(f"Discovery stopped after {MAX_SCAN_ENTRIES} directory entries; coverage is partial.")
                stop = True
                break
            item = Path(directory, name)
            if item.is_symlink():
                continue
            if name.lower() in ("log_history.txt", "log_history.bak", "log_history.txt.bak") or item.suffix.lower() in (".zip", ".csv"):
                paths.append(item)
                if len(paths) >= MAX_FILES:
                    warnings.append(f"Discovery capped at {MAX_FILES} candidate files; coverage is partial.")
                    stop = True
                    break
        if stop:
            break
    for item in paths:
        if check_cancel:
            check_cancel()
        if progress:
            progress(f"Reading {item.name}")
        is_log = item.name.lower() in ("log_history.txt", "log_history.bak", "log_history.txt.bak")
        try:
            size = item.stat().st_size
            if size > (MAX_LOG_BYTES if is_log else MAX_TELEMETRY_BYTES):
                warnings.append(f"{item.name}: exceeds the per-file size bound; omitted.")
                continue
            if total + size > MAX_TOTAL_BYTES:
                warnings.append(f"{item.name}: exceeds the remaining 512 MiB input budget; omitted.")
                continue
            total += size
            signature = _digest(item, check_cancel)
            source_hashes[str(item.resolve())] = signature
            if signature in seen:
                coverage.append(f"Duplicate file content omitted: {item.relative_to(root)}.")
                continue
            seen.add(signature)
            if is_log:
                log_paths.append(item)
                continue
            if item.suffix.lower() == ".zip":
                with zipfile.ZipFile(item) as archive:
                    if TELEMETRY_MEMBER not in archive.namelist():
                        continue
                    telemetry_size = archive.getinfo(TELEMETRY_MEMBER).file_size
            else:
                telemetry_size = size
            if analysis_bytes + telemetry_size * 4 > MAX_ANALYSIS_BYTES:
                warnings.append(f"{item.name}: bounded telemetry memory budget exceeded; omitted.")
                continue
            if len(flights) >= MAX_FLIGHTS:
                warnings.append(f"{item.name}: {MAX_FLIGHTS}-recording analysis limit reached; omitted.")
                continue
            flight = load_flight(item)
            # Copy metadata so cached recordings are not mutated by a collection identity.
            context = dict(flight.metadata)
            if not _identity(context) and _identity(manifest):
                context["robot_id"] = _identity(manifest)
            flights.append(replace(flight, metadata=context))
            analysis_bytes += telemetry_size * 4
        except InterruptedError:
            raise
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
            warnings.append(f"{item.name}: could not analyze ({error}).")
    metadata = {}
    logs = []
    # Parse each folder's rotation pair together; unrelated folders do not share sessions.
    by_directory = {}
    for item in log_paths:
        by_directory.setdefault(item.parent, []).append(item)
    for directory, paths in by_directory.items():
        metadata = {}
        paths.sort(key=lambda p: (not p.name.endswith(".bak"), p.name))
        try:
            events, skipped = read_events(paths, metadata, check_cancel, include_unmapped=True)
            metadata.update({key: manifest[key] for key in ("serial", "robot_id") if manifest.get(key)})
            # Keep independent exported folders separate when session ids restart.
            for event in events:
                event["session"] = f"{directory}:{event.get('session', 0)}"
            logs.append(LogData(str(directory), events, skipped, metadata, {}))
        except InterruptedError:
            raise
        except OSError as error:
            warnings.append(f"{directory.name}: could not parse logs ({error}).")
    report = analyze_data(logs, flights, source=str(root), log_utc_offset=log_utc_offset)
    for incident in report.incidents:
        for chart in incident.charts:
            chart.source_sha256 = source_hashes.get(str(Path(chart.flight_path).resolve()), "")
    report.warnings.extend(warnings)
    report.coverage.extend(coverage)
    report.coverage.append(f"Input bounds: {MAX_FLIGHTS} recordings, {MAX_FILES} candidate files, 512 MiB total source bytes. {len(log_paths)} log files read.")
    report.coverage.append("Telemetry memory budget: 128 MiB using a conservative four-times-uncompressed-input estimate. Files beyond a bound are explicitly listed as omitted.")
    if not log_paths:
        report.warnings.append("No log_history.txt or rotated backup was found; flight-only evidence cannot reconstruct the controller fault sequence.")
    return report
