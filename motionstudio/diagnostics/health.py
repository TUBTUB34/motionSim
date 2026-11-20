"""Arm health summary built from analysed incidents: findings, joint dashboard, hot spots.

Every finding is a group of logged fault bursts that share an event class and a joint,
so a recurring warning is reported once with its count, dates and trend instead of
once per burst. Only codes with a documented meaning are classified; the rest are
grouped by code family and never given an invented meaning. Severity and trend use
the fixed rules stated in ``METHOD`` and printed in the report.
"""
from collections import Counter
from dataclasses import dataclass, field
import re

from motionstudio.core.kinematics import JOINTS
from motionstudio.diagnostics.analysis import _date
from motionstudio.diagnostics.guidance import ERROR_BASE, ERROR_DIRECTORY, PROTECTIVE_STOP

SEVERITIES = ('Critical', 'High', 'Medium', 'Low', 'Info')
UR_DOCUMENTED = 'UR documented recommendation'
INFERENCE = 'Engineering best practice / inference'
NO_RECORDING = 'Not identified (no recording)'

# class: (severity once, severity when recurring, likely cause, why it matters,
#         program fix, hardware action, application check, reference title, url)
CLASSES = {
    'Collision detection': (
        'Medium', 'High',
        'Contact, a payload or centre of gravity that differs from the real tool and part, or a demanding move.',
        'Each detection is a protective stop: the cycle is interrupted, and a load model that is wrong '
        'makes the joints work harder than the controller expects.',
        'Check Set Payload at every pick and place against the real tool and part, and review acceleration '
        'and blends on the moves leading into the stop.',
        '',
        'Inspect the cell where the stops occur for contact with fixtures, parts or the robot\'s own cables.',
        'UR: Understanding protective stops', PROTECTIVE_STOP),
    'Position deviation': (
        'Medium', 'High',
        'The joint could not follow the planned path: contact, an inaccurate load model or more motion demand than the joint can deliver.',
        'Path-following stops interrupt the cycle; repeated deviations on one joint point at load or motion demand on that joint.',
        'Reduce acceleration on the affected moves and confirm the active payload and TCP where the stops occur.',
        '',
        'Check the path for contact and for hoses or cables that drag on the arm.',
        'UR error code C153', ERROR_BASE + 'CODE_153.html'),
    'Payload / load model': (
        'Medium', 'High',
        'The configured payload (mass or centre of gravity) does not match what the robot is carrying.',
        'An incorrect load model is a leading cause of protective stops and adds wear to every joint.',
        'Set the payload explicitly at pick and place (Set Payload / set_payload) using measured tool and part values.',
        '',
        'Weigh the tool and the heaviest part, and measure the centre of gravity, before changing the program.',
        'UR error code C162', ERROR_BASE + 'CODE_162.html'),
    'Repeated protective stops': (
        'Critical', 'Critical',
        'An application problem that is not resolved by resetting, or a joint fault.',
        'UR asks for the cause of repeated protective stops to be resolved before routine operation continues.',
        'Find the program stage where the stops repeat and correct its load or motion settings.',
        'If the application checks do not explain the stops, send the support files to qualified UR service.',
        'Verify contact, payload and centre of gravity at the stopping stage.',
        'UR: Understanding protective stops', PROTECTIVE_STOP),
    'Torque demand': (
        'Medium', 'High',
        'An abrupt change of commanded torque, usually a hard transition between moves or a sudden load change.',
        'Torque jumps load the gearbox and can end in protective stops.',
        'Smooth the transition into the affected move: lower acceleration, review blends and remove unnecessary stops.',
        '',
        'Check for sudden load changes at that stage (part release, contact).',
        'UR error code C174', ERROR_BASE + 'CODE_174.html'),
    'Blend / waypoint': (
        'Low', 'Medium',
        'Blend radii that overlap or are too small for the waypoint spacing, so the controller skips or shortens a blend.',
        'Skipped waypoints change the executed path from the one that was taught.',
        'Reduce blend radii to less than half the distance to neighbouring waypoints, or space the waypoints further apart.',
        '',
        '',
        'UR error code C171', ERROR_BASE + 'CODE_171.html'),
    'Safety limit proximity': (
        'Medium', 'Medium',
        'The path runs close to a configured joint, tool-orientation or safety-plane limit.',
        'Little margin to a safety limit causes stops when the path varies slightly.',
        'Move the affected waypoints to keep margin from the configured limits; do not widen limits set by the risk assessment.',
        '',
        '',
        'UR error codes C150–C152', ERROR_DIRECTORY),
    'Brake': (
        'Critical', 'Critical',
        'A brake that does not release, engage or hold as expected.',
        'Brake faults are a safety concern and can damage the joint.',
        '',
        'Have the joint\'s brake checked by qualified service before further production use.',
        '',
        'UR error codes (brake)', ERROR_DIRECTORY),
    'Gear torsion': (
        'Critical', 'Critical',
        'Excessive twist between the motor and joint output.',
        'Excessive gear torsion indicates overload or damage in the drive train.',
        '',
        'Have the joint inspected by qualified service; send the support files.',
        'Check for collisions or overloads around the time of the warning.',
        'UR error code C361', ERROR_DIRECTORY),
    'Encoder': (
        'High', 'High',
        'Encoder signal, calibration or mounting problems.',
        'Encoder errors stop the robot and can indicate a hardware fault in the joint.',
        '',
        'Have the joint encoder checked by qualified service if the warning recurs.',
        '',
        'UR error codes (encoder)', ERROR_DIRECTORY),
    'Temperature': (
        'High', 'High',
        'Joint temperature above the controller\'s limit.',
        'Sustained high temperature shortens joint life.',
        'Reduce duty on the affected joint (acceleration, dwell under load) if the cell runs hot.',
        'Check ventilation and joint condition with qualified service if it recurs.',
        'Check ambient temperature and air flow around the arm.',
        'UR error codes (thermal)', ERROR_DIRECTORY),
    'Motor current': (
        'High', 'High',
        'Motor current above the controller limit.',
        'Overcurrent stops indicate overload or a drive problem.',
        'Review payload and acceleration on the affected moves.',
        'If it recurs with correct payload and motion, have the joint checked by qualified service.',
        '',
        'UR error code C306', ERROR_DIRECTORY),
}

METHOD = [
    'Events are fault bursts: log entries within 2 seconds of each other count once. A finding groups the bursts that share an event class and a joint.',
    'Severity: brake, gear-torsion and repeated-protective-stop codes are Critical; encoder, temperature and motor-current codes are High. '
    'Collision, position-deviation, payload and torque-demand warnings are Medium once and High when they recur. '
    'Blend warnings are Low once and Medium when they recur. Codes without a documented meaning are Info. '
    'A Low or Medium finding with a Rising trend moves up one level and is then marked as engineering inference.',
    'Joint status follows its most severe finding: Critical → Critical, High → Attention required, Medium → Monitor, Low/Info or none → Good. '
    '“Limited evidence” and “Not assessed” mean the records cannot support a status.',
    'Trend compares the first and second half of the logged period: Rising or Falling when one half has at least 1.5 times the events of the other; '
    'fewer than 4 events are not trended. Counts are not normalised by operating hours.',
]
# Program Check topics that bear on each event class (programs.check.CATEGORIES names).
RELATED_PROGRAM_CHECKS = {
    'Collision detection': ('Initial payload / CoG / inertia', 'Dynamic payload changes', 'Payload transition and inertia', 'Acceleration recommendations'),
    'Position deviation': ('Initial payload / CoG / inertia', 'Dynamic payload changes', 'Acceleration recommendations', 'Initial TCP', 'Dynamic TCP changes'),
    'Payload / load model': ('Initial payload / CoG / inertia', 'Dynamic payload changes', 'Payload transition and inertia'),
    'Repeated protective stops': ('Initial payload / CoG / inertia', 'Dynamic payload changes', 'Acceleration recommendations'),
    'Torque demand': ('Acceleration recommendations', 'Blend radius overlap', 'Blended motion continuation'),
    'Blend / waypoint': ('Blend radius overlap', 'Blended motion continuation'),
    'Safety limit proximity': ('Velocity specifications',),
    'Motor current': ('Initial payload / CoG / inertia', 'Acceleration recommendations'),
}
STATUS_BY_SEVERITY = {'Critical': 'Critical', 'High': 'Attention required', 'Medium': 'Monitor', 'Low': 'Good', 'Info': 'Good'}
CONCERN = ('Critical', 'Attention required', 'Monitor')


def event_class(code):
    """The documented class of a controller code, or '' when its meaning is not established."""
    match = re.match(r'C(\d+)(?:A(\d+))?', str(code))
    if not match:
        return ''
    number, sub = int(match.group(1)), match.group(2)
    if number == 157:
        return 'Collision detection'
    if number == 153:
        return 'Position deviation'
    if number in (158, 159, 162):
        return 'Payload / load model'
    if number == 163:
        return 'Repeated protective stops'
    if number in (173, 174):
        return 'Torque demand'
    if number == 171:
        return 'Blend / waypoint'
    if number in (150, 151, 152):
        return 'Safety limit proximity'
    if number in (284, 350) or (number == 306 and sub in ('1', '9')):
        return 'Brake'
    if number == 361:
        return 'Gear torsion'
    if number in (263, 265, 75, 46):
        return 'Encoder'
    if (number == 261 and sub == '4') or number == 62:
        return 'Temperature'
    if number == 306 and sub == '10':
        return 'Motor current'
    return ''


def severity_of(name, bursts):
    once, recurring = CLASSES[name][:2]
    return recurring if bursts > 1 else once


def _when(text):
    stamp = _date(text)
    return stamp.replace(tzinfo=None) if stamp is not None else None


def trend(times, start, end):
    """'Rising', 'Falling', 'Stable' or 'Too few' over the logged period (see METHOD)."""
    times = [t for t in times if t is not None]
    if len(times) < 4 or start is None or end is None or end <= start:
        return 'Too few', 0, 0
    middle = start + (end - start) / 2
    first = sum(t < middle for t in times)
    second = len(times) - first
    if second >= 1.5 * max(first, 1):
        return 'Rising', first, second
    if first >= 1.5 * max(second, 1):
        return 'Falling', first, second
    return 'Stable', first, second


def day(stamp):
    return stamp.strftime('%Y-%m-%d') if stamp else 'date unknown'


def span_text(first, last):
    if not first:
        return 'date unknown'
    return day(first) if day(first) == day(last) else f'{day(first)} to {day(last)}'


@dataclass
class Finding:
    id: str = ''
    severity: str = 'Info'
    category: str = ''
    joint: str = ''
    count: int = 0
    first: object = None
    last: object = None
    trend: str = 'Too few'
    trend_counts: tuple = (0, 0)
    codes: list[str] = field(default_factory=list)
    locations: Counter = field(default_factory=Counter)
    recordings: list[str] = field(default_factory=list)
    incident_ids: list[str] = field(default_factory=list)
    basis: str = UR_DOCUMENTED

    @property
    def title(self):
        return f'{self.category} · {self.joint}' if self.joint else self.category

    @property
    def where(self):
        """Program locations with counts; events without a recording are counted, not named."""
        parts = [f'{location} ×{count}' for location, count in self.locations.most_common() if location != NO_RECORDING]
        unplaced = self.locations.get(NO_RECORDING, 0)
        if unplaced:
            parts.append(f'{unplaced} not placed' if parts else 'Not placed (no recording)')
        return ', '.join(parts) or '–'

    @property
    def period(self):
        return span_text(self.first, self.last)

    @property
    def what(self):
        if not self.category or self.category == 'Uninterpreted controller codes':
            return f'{self.count} event(s) with codes {", ".join(self.codes)}, {span_text(self.first, self.last)}.'
        if self.category == 'Payload / load model' and all(code.startswith('C162') for code in self.codes):
            # C162 is the controller's note on a stop, not a separate stop.
            return (f'In {self.count} logged stop(s) the controller named the payload or centre of gravity as a likely cause '
                    f'({", ".join(self.codes)}), {span_text(self.first, self.last)}.')
        subject = f' on {self.joint}' if self.joint and self.joint != 'Controller' else ''
        return f'{self.count} {self.category.lower()} event(s){subject}, {span_text(self.first, self.last)}.'

    @property
    def info(self):
        return CLASSES.get(self.category)


@dataclass
class JointHealth:
    joint: str
    status: str = 'Not assessed'
    events: int = 0
    trend: str = 'Too few'
    metrics: list[str] = field(default_factory=list)
    notes: str = ''
    findings: list[str] = field(default_factory=list)


@dataclass
class HotSpot:
    rank: int
    location: str
    count: int
    types: str
    cause: str
    severity: str
    findings: list[str] = field(default_factory=list)
    program_refs: list[str] = field(default_factory=list)  # Program Check finding IDs of that program


@dataclass
class ArmHealth:
    overall: str = 'Not assessed'
    start: object = None
    end: object = None
    bursts: int = 0
    classified: int = 0
    findings: list[Finding] = field(default_factory=list)
    joints: list[JointHealth] = field(default_factory=list)
    hot_spots: list[HotSpot] = field(default_factory=list)
    timeline: list[tuple[str, str, str, str]] = field(default_factory=list)
    monthly: dict[str, Counter] = field(default_factory=dict)
    program: list[str] = field(default_factory=list)
    maintenance: list[str] = field(default_factory=list)
    application: list[str] = field(default_factory=list)
    data_needed: list[str] = field(default_factory=list)
    monitoring: list[str] = field(default_factory=list)
    matched_recordings: int = 0
    programs: list[str] = field(default_factory=list)
    program_checks: list[dict] = field(default_factory=list)

    def counts(self):
        counted = Counter(f.severity for f in self.findings)
        return [(name, counted.get(name, 0)) for name in SEVERITIES]

    @property
    def period(self):
        return span_text(self.start, self.end)


def _metrics(incidents):
    """Largest recorded values per joint across reviewed recordings."""
    values = {}
    for incident in incidents:
        for measured in incident.joint_evidence:
            joint = values.setdefault(measured.joint, {})
            for key, value in measured.metrics.items():
                if value is not None:
                    joint[key] = max(joint.get(key, value), value)
    shown = {}
    for joint, metrics in values.items():
        lines = []
        if 'peak_velocity' in metrics:
            lines.append(f'Speed {metrics["peak_velocity"]:.2f} rad/s')
        if 'position_tracking_error' in metrics:
            lines.append(f'Tracking {metrics["position_tracking_error"] * 1000:.1f} mrad')
        if 'current_residual' in metrics:
            lines.append(f'Current Δ {metrics["current_residual"]:.2f} A')
        if 'peak_temperature' in metrics:
            lines.append(f'Temp {metrics["peak_temperature"]:.1f} °C')
        shown[joint] = lines
    return shown


def build_arm_health(report, statuses=None):
    """Group the report's incidents into findings and a per-joint dashboard.

    ``statuses`` (joint -> evidence status from the joint review) keeps joints that
    the records cannot assess from being shown as Good.
    """
    health = ArmHealth(bursts=len([i for i in report.incidents if i.codes]))
    groups = {}
    unknown = {}
    times = []
    for incident in report.incidents:
        if not incident.codes:
            continue
        when = _when(incident.time)
        times.append(when)
        programs = [p.program for p in incident.program_evidence if p.program]
        location = programs[0] if programs else NO_RECORDING
        if incident.flight_names:
            health.matched_recordings += 1
        joint_of = {code: joint for joint, codes in incident.joint_codes.items() for code in codes}
        keys, families = set(), set()
        for code in incident.codes:
            name = event_class(code)
            if name:
                keys.add((name, joint_of.get(code, 'Controller' if name in ('Blend / waypoint', 'Safety limit proximity') else '')))
            else:
                family = re.match(r'C\d+', code)
                families.add(family.group() if family else code)
        for family in families:
            unknown.setdefault(family, []).append((incident, when))
        if keys:
            health.classified += 1
        month = when.strftime('%Y-%m') if when else 'Unknown'
        for name in {name for name, _ in keys} or {'Uninterpreted'}:
            health.monthly.setdefault(month, Counter())[name] += 1
        for key in keys:
            groups.setdefault(key, []).append((incident, when, location))
    known_times = [t for t in times if t is not None]
    health.start, health.end = (min(known_times), max(known_times)) if known_times else (None, None)
    findings = []
    for (name, joint), items in groups.items():
        stamps = sorted(t for _, t, _ in items if t is not None)
        finding = Finding(category=name, joint=joint, count=len(items), severity=severity_of(name, len(items)),
                          first=stamps[0] if stamps else None, last=stamps[-1] if stamps else None,
                          codes=sorted({c for i, _, _ in items for c in i.codes if event_class(c) == name}),
                          locations=Counter(location for _, _, location in items),
                          recordings=list(dict.fromkeys(n for i, _, _ in items for n in i.flight_names)),
                          incident_ids=[i.id for i, _, _ in items])
        finding.trend, *counts = trend([t for _, t, _ in items], health.start, health.end)
        finding.trend_counts = tuple(counts)
        if finding.trend == 'Rising' and finding.severity in ('Low', 'Medium'):
            finding.basis = INFERENCE  # escalated on trend, not on a documented rule
            finding.severity = SEVERITIES[SEVERITIES.index(finding.severity) - 1]
        findings.append(finding)
    if unknown:
        items = [(family, entries) for family, entries in sorted(unknown.items(), key=lambda kv: -len(kv[1]))]
        incidents = {id(i): (i, t) for _, entries in items for i, t in entries}
        stamps = sorted(t for _, t in incidents.values() if t is not None)
        findings.append(Finding(category='Uninterpreted controller codes', severity='Info', count=len(incidents),
                                first=stamps[0] if stamps else None, last=stamps[-1] if stamps else None,
                                codes=[f'{family} ×{len(entries)}' for family, entries in items],
                                incident_ids=[i.id for i, _ in incidents.values()], basis=INFERENCE, trend='–'))
    findings.sort(key=lambda f: (SEVERITIES.index(f.severity), -f.count, f.category, JOINTS.index(f.joint) if f.joint in JOINTS else 9))
    for number, finding in enumerate(findings, 1):
        finding.id = f'F-{number:02d}'
    health.findings = findings
    metrics = _metrics(report.incidents)
    statuses = statuses or {}
    for joint in JOINTS:
        own = [f for f in findings if f.joint == joint]
        row = JointHealth(joint=joint, events=len({i for f in own for i in f.incident_ids}),
                          metrics=metrics.get(joint, []), findings=[f.id for f in own])
        joint_times = [_when(i.time) for i in report.incidents if i.id in {x for f in own for x in f.incident_ids}]
        row.trend = trend(joint_times, health.start, health.end)[0] if own else '–'
        if own:
            worst = min(own, key=lambda f: SEVERITIES.index(f.severity))
            row.status = STATUS_BY_SEVERITY[worst.severity]
            kinds = Counter()
            for f in own:
                kinds[f.category] += f.count
            row.notes = ', '.join(f'{name.lower()} ×{count}' for name, count in kinds.most_common())
        elif statuses.get(joint) in ('Limited evidence', 'Not assessed'):
            row.status = statuses[joint]
            row.notes = 'No joint warnings logged; the records are too limited to assess it.' if report.log_source_count else 'No logs or measurements for this joint.'
        elif statuses.get(joint) == 'Monitor':
            row.status = 'Monitor'
            row.notes = 'Recorded current crossed the recorded bounds; no logged joint warning.'
        else:
            row.status = 'Good' if report.log_source_count else 'Not assessed'
            row.notes = 'No joint warnings in the logged period.' if report.log_source_count else 'No logs for this joint.'
        health.joints.append(row)
    for status in ('Critical', 'Attention required', 'Monitor', 'Good'):
        if any(row.status == status for row in health.joints):
            health.overall = status
            break
    health.programs = sorted({p.program for i in report.incidents for p in i.program_evidence if p.program})
    health.program_checks = list(getattr(report, 'program_checks', []) or [])
    _hot_spots(health)
    _timeline(health, report)
    _recommendations(health, report)
    return health


def _hot_spots(health):
    spots = {}
    for finding in health.findings:
        if finding.category == 'Uninterpreted controller codes':
            continue
        for location, count in finding.locations.items():
            spot = spots.setdefault((location, finding.category), dict(count=0, joints=Counter(), findings=[], severity='Info'))
            spot['count'] += count
            if finding.joint:
                spot['joints'][finding.joint] += count
            spot['findings'].append(finding.id)
            if SEVERITIES.index(finding.severity) < SEVERITIES.index(spot['severity']):
                spot['severity'] = finding.severity
    ranked = sorted(spots.items(), key=lambda kv: (SEVERITIES.index(kv[1]['severity']), -kv[1]['count']))
    for rank, ((location, name), spot) in enumerate(ranked[:10], 1):
        joints = ', '.join(f'{joint} {count}' for joint, count in spot['joints'].most_common())
        types = f'{name} ({joints})' if joints else (f'{name} (controller note on the stops)' if name == 'Payload / load model' else name)
        check = next((c for c in health.program_checks if c['program'] == location), None)
        refs = [f['id'] for f in check['findings'] if f['category'] in RELATED_PROGRAM_CHECKS.get(name, ())] if check else []
        health.hot_spots.append(HotSpot(rank, location, spot['count'], types, CLASSES[name][2],
                                        spot['severity'], spot['findings'], refs))


def _timeline(health, report):
    """Recorded incidents, plus first and latest occurrence of each Critical/High finding."""
    rows = {}
    by_id = {i.id: i for i in report.incidents}
    for finding in health.findings:
        if finding.severity not in ('Critical', 'High'):
            continue
        ids = finding.incident_ids
        for label, incident_id in (('First', ids[0]), ('Latest', ids[-1])):
            incident = by_id[incident_id]
            rows.setdefault(incident.id, (incident, f'{label} {finding.category.lower()}' + (f' on {finding.joint}' if finding.joint else ''), finding.id))
    for incident in report.incidents:
        if incident.codes and incident.flight_names:
            ids = [f.id for f in health.findings if incident.id in f.incident_ids]
            rows[incident.id] = (incident, 'Flight recording captured', ', '.join(ids))
    ordered = sorted(rows.values(), key=lambda row: (_when(row[0].time) is None, _when(row[0].time) or 0))
    for incident, what, finding in ordered[-18:]:
        stamp = _when(incident.time)
        joints = ', '.join(j for j in incident.joints if j != 'Unknown') or 'Controller'
        program = next((p.program for p in incident.program_evidence if p.program), '')
        detail = f'{", ".join(incident.codes[:4])}{" …" if len(incident.codes) > 4 else ""} · {joints}'
        if program:
            detail += f' · {program}'
        health.timeline.append((stamp.strftime('%Y-%m-%d %H:%M:%S') if stamp else incident.time or 'Unknown',
                                what, detail, finding))


def _recommendations(health, report):
    present = [f for f in health.findings if f.info and f.severity in ('Critical', 'High', 'Medium')]
    by_class = {}
    for finding in present:
        by_class.setdefault(finding.category, []).append(finding)
    for name, findings in by_class.items():
        _severity1, _severity2, _cause, _why, program, hardware, application, _ref, _url = CLASSES[name]
        joints = [f.joint for f in findings if f.joint and f.joint != 'Controller']
        ids = ', '.join(f.id for f in findings)
        subject = f' ({", ".join(joints)})' if joints else ''
        if program:
            health.program.append(f'{name}{subject}: {program} [{ids}]')
        if hardware:
            health.maintenance.append(f'{name}{subject}: {hardware} [{ids}]')
        if application:
            health.application.append(f'{name}{subject}: {application} [{ids}]')
    for check in health.program_checks:
        related = {category for name in by_class for category in RELATED_PROGRAM_CHECKS.get(name, ())}
        hits = [f for f in check['findings'] if f['category'] in related]
        if hits:
            health.program.insert(0, f'Program Check of {check["program"]} (saved in {check["recording"]}) found '
                                     + '; '.join(f'{f["id"]} {f["category"].lower()} at {f["location"].lower()}' for f in hits)
                                     + '. Correct these first: they bear on the stops above.')
    concern = [row.joint for row in health.joints if row.status in ('Critical', 'Attention required')]
    if concern and not health.maintenance:
        health.maintenance.append(f'If warnings on {", ".join(concern)} continue after the program and payload corrections, '
                                  'have the joint(s) inspected by qualified UR service and include the support files.')
    unmatched = health.bursts - health.matched_recordings
    if health.bursts and unmatched:
        health.data_needed.append(f'{unmatched} of {health.bursts} events have no flight recording, so their program location is unknown. '
                                  'Save a flight report (or keep the robot\'s automatic ones) when the stops happen.')
    uninterpreted = next((f for f in health.findings if f.category == 'Uninterpreted controller codes'), None)
    if uninterpreted:
        health.data_needed.append('Look up the uninterpreted code families (' + ', '.join(uninterpreted.codes[:6]) +
                                  ') in the UR error-code directory for this software version; they are not classified in this report.')
    if not report.log_source_count:
        health.data_needed.append('Collect log_history.txt and its rotated .bak from the controller.')
    if any(f.recordings for f in health.findings) and not any(
            c.marker_elapsed is not None for i in report.incidents for c in i.charts):
        health.data_needed.append('The recordings do not mark the fault sample, so the exact program line at the stop is not known; '
                                  'record the program line (or a marker) at the stop to locate it.')
    if health.findings:
        health.monitoring.append('After changing the program or payload, collect again after 2–4 weeks of normal production and compare the event counts per finding.')
        health.monitoring.append('Recheck the joints marked Attention required or Critical first; a falling count after the change confirms the cause.')
    else:
        health.monitoring.append('Keep this report as a baseline and compare it with the next scheduled collection.')
