"""Recorded UR flight telemetry, independent of plotting and playback.

CSV header units are authoritative. Timestamps are controller uptime, not UTC.
Archives are read in place; no member is extracted or executed.
"""
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path
import ast
import csv
import io
import json
import re
import zipfile

import numpy as np

from kinematics import MODELS

TELEMETRY_MEMBER = "files/realtimedata.csv"
MAX_TELEMETRY_BYTES = 256 * 1024 * 1024
MAX_CONTEXT_BYTES = 4 * 1024 * 1024
MAX_SAMPLES = 250_000
MAX_COLUMNS = 256
JOINTS = ("Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3")
UNITS = {"position": "rad", "velocity": "rad/s", "acceleration": "rad/s^2",
         "current": "A", "torque": "Nm", "temperature": "C", "voltage": "V",
         "control_mode": "code", "current_upper": "A", "current_lower": "A"}


@dataclass
class FlightReport:
    path: Path
    name: str
    metadata: dict[str, str]
    timestamps: np.ndarray
    time: np.ndarray
    columns: dict[str, np.ndarray]
    warnings: tuple[str, ...] = ()
    model: str | None = None
    script: str = ""
    break_before: np.ndarray | None = None
    _derived: dict = field(default_factory=dict, repr=False)

    @property
    def duration(self):
        return float(self.time[-1]) if len(self.time) else 0.0

    @property
    def sample_count(self):
        return len(self.time)

    @cached_property
    def gap_threshold(self):
        """Three nominal sample intervals; do not bridge absent samples."""
        return float(np.median(np.diff(self.time)) * 3) if len(self.time) > 1 else 0.0

    def valid_segments(self, values):
        """Yield contiguous finite index slices, splitting at timestamp gaps."""
        values = np.asarray(values)
        valid = np.isfinite(values)
        if valid.ndim > 1:
            valid = valid.all(axis=tuple(range(1, valid.ndim)))
        gaps = np.r_[False, np.diff(self.time) > self.gap_threshold]
        if self.break_before is not None:
            gaps = gaps | self.break_before
        starts = np.flatnonzero(valid & (np.r_[True, ~valid[:-1]] | gaps))
        ends = np.flatnonzero(valid & (np.r_[~valid[1:], True] | np.r_[gaps[1:], False])) + 1
        yield from (slice(int(start), int(end)) for start, end in zip(starts, ends))

    def source_header(self, metric, joint, target=False):
        """Resolve explicitly recorded channels without conflating actual/target."""
        if metric not in UNITS or joint not in range(6):
            raise ValueError("Unknown metric or joint index.")
        prefix = "Target" if target else "Actual"
        if metric == "torque":
            candidates = [f"{prefix} moment j{joint} [Nm]", f"{prefix} torque j{joint} [Nm]"]
        elif metric in ("temperature", "voltage", "control_mode"):
            suffix = {"temperature": f"temperature j{joint} [C]",
                      "voltage": f"voltage j{joint} [V]", "control_mode": f"control mode j{joint}"}[metric]
            candidates = [f"{prefix} {suffix}"]
            if not target:
                candidates.insert(0, suffix[0].upper() + suffix[1:])
        else:
            label = {"current_upper": "Current upper", "current_lower": "Current lower"}.get(metric)
            candidates = [f"{label} j{joint} [A]" if label else f"{prefix} {metric} j{joint} [{UNITS[metric]}]"]
        return next((header for header in candidates if header in self.columns), None)

    def series(self, metric, joint, target=False):
        """Return source-unit samples; absent channels remain NaN.

        Actual acceleration is derived using finite differences of measured
        velocity within each finite continuous segment. It is never a target.
        Current envelopes are returned only if explicitly present in the CSV.
        """
        header = self.source_header(metric, joint, target)
        if header is not None:
            return self.columns[header]
        key = (metric, joint, target)
        if key not in self._derived:
            values = np.full(self.sample_count, np.nan)
            if metric == "acceleration" and not target:
                velocity = self.series("velocity", joint)
                for segment in self.valid_segments(velocity):
                    if segment.stop - segment.start >= 2:
                        values[segment] = np.gradient(velocity[segment], self.time[segment])
            values.flags.writeable = False
            self._derived[key] = values
        return self._derived[key]


def discover_flights(directory):
    """Find telemetry ZIPs/CSVs, including files ignored by Git."""
    paths = []
    for path in sorted(Path(directory).glob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".csv":
            paths.append(path)
        elif path.suffix.lower() == ".zip":
            try:
                with zipfile.ZipFile(path) as archive:
                    if TELEMETRY_MEMBER in archive.namelist():
                        paths.append(path)
            except (OSError, zipfile.BadZipFile):
                continue
    return paths


def load_flight(path):
    """Load and cache at most four unchanged flight reports."""
    path = Path(path).resolve()
    stat = path.stat()
    if stat.st_size > MAX_TELEMETRY_BYTES:
        raise ValueError("Flight file exceeds the 256 MiB size limit.")
    return _load_cached(str(path), stat.st_mtime_ns, stat.st_size)


def _read_context(archive, name):
    try:
        info = archive.getinfo(name)
    except KeyError:
        return ""
    if info.file_size > MAX_CONTEXT_BYTES:
        raise ValueError(f"Flight context member exceeds 4 MiB: {name}")
    return archive.read(info).decode("utf-8", errors="replace")


def _metadata(summary):
    labels = {"Incident Time": "incident_time", "Reporting Time": "reporting_time",
              "Serial number": "serial", "Version": "software_version",
              "Current Program Name": "program", "Current Program State": "program_state",
              "Current Installation Name": "installation", "TCP offset [m, rad]": "tcp_offset",
              "Payload [kg]": "payload_kg", "Center of gravity [m]": "center_of_gravity"}
    data = {"time_basis": "Controller uptime seconds; elapsed time begins at first sample"}
    for line in summary.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in labels:
            data[labels[key.strip()]] = value.strip()
    return data


def _model_from_dh(config):
    """Match recorded dimensions; legacy hardware comments omit e-Series."""
    values = {}
    for name in ("a", "d"):
        match = re.search(rf"^\s*{name}\s*=\s*(\[[^\]\n]+\])", config, re.MULTILINE)
        if match:
            try:
                values[name] = np.asarray(ast.literal_eval(match.group(1)), dtype=float)
            except (ValueError, SyntaxError, TypeError):
                return None
    if any(name not in values or values[name].shape != (6,) for name in ("a", "d")):
        return None
    a, d = values["a"], values["d"]
    dimensions = (a[1], a[2], d[0], d[3], d[4], d[5])
    return next((name for name, expected in MODELS.items()
                 if np.allclose(dimensions, expected, atol=1e-6, rtol=0)), None)


def _read_csv(stream):
    reader = csv.reader(stream)
    try:
        headers = [h.strip().lstrip("\ufeff") for h in next(reader)]
    except StopIteration:
        raise ValueError("Flight telemetry CSV is empty.") from None
    if "Timestamp [s]" not in headers:
        raise ValueError("Expected a 'Timestamp [s]' controller-uptime column.")
    if len(headers) > MAX_COLUMNS:
        raise ValueError("Telemetry has too many columns (maximum 256).")
    selected = [(i, h) for i, h in enumerate(headers) if h and h != "reserved"]
    names = [h for _, h in selected]
    if len(set(names)) != len(names):
        raise ValueError("Telemetry contains duplicate named columns.")
    timestamp_index = headers.index("Timestamp [s]")
    rows, timestamps, breaks = [], [], []
    malformed = invalid_times = invalid_values = 0
    previous = -np.inf
    interrupted = False
    for line_number, row in enumerate(reader, start=2):
        if line_number > MAX_SAMPLES + 1:
            raise ValueError("Telemetry exceeds the 250,000 sample limit.")
        try:
            timestamp = float(row[timestamp_index])
        except (IndexError, ValueError):
            invalid_times += 1
            interrupted = True
            continue
        if not np.isfinite(timestamp) or timestamp <= previous:
            invalid_times += 1
            interrupted = True
            continue
        previous = timestamp
        timestamps.append(timestamp)
        breaks.append(interrupted)
        interrupted = False
        values = []
        if len(row) != len(headers):
            malformed += 1
            values = [np.nan] * len(selected)
        else:
            for index, _ in selected:
                try:
                    value = float(row[index])
                except ValueError:
                    value = np.nan
                if not np.isfinite(value):
                    invalid_values += 1
                    value = np.nan
                values.append(value)
        rows.append(values)
    if not timestamps:
        raise ValueError("Flight telemetry contains no valid increasing timestamps.")
    data = np.asarray(rows, dtype=float)
    times = np.asarray(timestamps, dtype=float)
    columns = {name: data[:, i] for i, (_, name) in enumerate(selected)}
    columns["Timestamp [s]"] = times
    warnings = []
    if malformed:
        warnings.append(f"{malformed} malformed records retained as missing measurements.")
    if invalid_times:
        warnings.append(f"{invalid_times} invalid or non-increasing timestamp records omitted.")
    if invalid_values:
        warnings.append(f"{invalid_values} missing or invalid measurements retained as gaps.")
    return times, columns, warnings, np.asarray(breaks, dtype=bool)


@lru_cache(maxsize=4)
def _load_cached(path_string, _modified, _size):
    path = Path(path_string)
    summary = script = config = ""
    archive_metadata = {}
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as archive:
                try:
                    info = archive.getinfo(TELEMETRY_MEMBER)
                except KeyError:
                    raise ValueError("This archive has no files/realtimedata.csv flight recording.") from None
                if info.file_size > MAX_TELEMETRY_BYTES:
                    raise ValueError("Uncompressed telemetry exceeds 256 MiB.")
                with archive.open(info) as raw, io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="") as stream:
                    timestamps, columns, warnings, breaks = _read_csv(stream)
                summary = _read_context(archive, "summary.log")
                script = _read_context(archive, "files/polyscope.in.memory.program.script")
                config = _read_context(archive, "files/urcontrol/urcontrol.conf")
                text = _read_context(archive, "metadata.json")
                if text:
                    try:
                        archive_metadata = json.loads(text)
                        if not isinstance(archive_metadata, dict):
                            raise ValueError()
                    except (ValueError, TypeError):
                        warnings.append("Archive metadata.json is malformed; telemetry remains available.")
                        archive_metadata = {}
        except (zipfile.BadZipFile, RuntimeError) as error:
            raise ValueError(f"Cannot read flight ZIP: {error}") from error
    elif path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as stream:
            timestamps, columns, warnings, breaks = _read_csv(stream)
    else:
        raise ValueError("Choose a recording ZIP or telemetry CSV. URA files contain program statistics, not flight motion.")
    metadata = _metadata(summary)
    metadata["report_id"] = str(archive_metadata.get("reportId", path.stem))
    metadata["report_type"] = str(archive_metadata.get("reportType", "Recorded telemetry"))
    model = _model_from_dh(config)
    if model:
        metadata["model"] = model
        metadata["model_basis"] = "Recorded nominal DH dimensions; factory calibration not applied"
    else:
        warnings.append("Robot model is unknown; nominal arm geometry cannot be inferred safely.")
    elapsed = timestamps - timestamps[0]
    if len(elapsed) > 1:
        interval = float(np.median(np.diff(elapsed)))
        metadata["sample_rate_hz"] = f"{1 / interval:.6g}"
        gaps = int(np.sum(np.diff(elapsed) > 3 * interval))
        if gaps:
            warnings.append(f"{gaps} timestamp gaps exceed three nominal sample intervals.")
    derived_joints = [j for j in range(6) if f"Actual acceleration j{j} [rad/s^2]" not in columns
                      and f"Actual velocity j{j} [rad/s]" in columns]
    recorded_joints = [j for j in range(6) if f"Actual acceleration j{j} [rad/s^2]" in columns]
    if derived_joints:
        metadata["acceleration_note"] = "Actual acceleration derived from recorded velocity (finite differences) for " + ", ".join(JOINTS[j] for j in derived_joints)
        warnings.append(metadata["acceleration_note"] + ".")
    elif recorded_joints:
        metadata["acceleration_note"] = "Actual acceleration supplied directly by recorded channels"
    else:
        metadata["acceleration_note"] = "Actual acceleration unavailable (no recorded acceleration or velocity)"
    if not any(f"Current {bound} j{j} [A]" in columns for j in range(6) for bound in ("upper", "lower")):
        warnings.append("Current-limit envelopes are not recorded.")
    for values in (*columns.values(), timestamps, elapsed, breaks):
        values.flags.writeable = False
    return FlightReport(path, path.name, metadata, timestamps, elapsed, columns,
                        tuple(warnings), model, script, break_before=breaks)
