"""Program Check findings in report form: severity, location, fix, observations and a score.

Only checks the source can decide ('Review recommended') become findings; checks that
need runtime evidence or a specification become observations to verify. Severity is
fixed per category (``SEVERITY_RULE``); the score uses ``SCORE_RULE``. Both are printed
in the report. IDs follow the report order, so Robot Status can cite them.
"""
from dataclasses import dataclass, field
import re

from motionstudio.programs.check import CATEGORIES

ESTIMATE = re.compile(r'Source-definition estimate using (.*?)\. Initialization order, runtime changes and execution are not proven\.', re.S)
# Shared qualifications appear once in the interpretation section. Numeric
# values, comparisons and specific caveats stay intact in each result.
SHARED_TEXT = (
    'This is not a safety limit or measured acceleration.',
    'Configured source value only; runtime motion is not certified.',
    'Source geometry does not prove a skipped waypoint; controller planning and execution require verification.',
    'First source occurrence; execution order and physical correctness are not proven.',
    'Additional source occurrence; execution order and physical correctness are not proven.',
    'Confirm tool plus workpiece mass, centre of gravity and inertia against the real tool; update payload when the workpiece changes.',
    'Confirm the configured TCP against the actual tool and each tool change.',
)


@dataclass(frozen=True)
class FindingGroup:
    category: str
    status: str
    detail: str
    estimated: bool
    assumptions: tuple
    findings: tuple

    @property
    def lines(self):
        return tuple(sorted({finding.line for finding in self.findings if finding.line}))

    @property
    def references(self):
        return tuple(sorted({finding.reference + 1 for finding in self.findings}))


def group_findings(findings):
    """Collapse equivalent results, retaining distinct evidence and assumptions.

    Values, units, limits, status and literal/estimate basis are part of identity.
    A different expression giving the same estimated result retains its own
    initializer evidence. No numeric rounding or fuzzy text matching is used.
    """
    grouped = {}
    for finding in findings:
        assumptions = tuple(dict.fromkeys(' '.join(m.group(1).split()) for m in ESTIMATE.finditer(finding.detail)))
        detail = ESTIMATE.sub('', finding.detail)
        for phrase in SHARED_TEXT:
            detail = detail.replace(phrase, '')
        detail = ' '.join(detail.split())
        category = 'Source files' if detail.startswith('No matching generated .script file.') else finding.category
        key = (category, finding.status, detail, bool(assumptions), bool(finding.line))
        members, notes = grouped.setdefault(key, ({}, {}))
        members.setdefault((finding.line, finding.evidence, finding.detail, finding.reference), finding)
        notes.update(dict.fromkeys(assumptions))
    return [FindingGroup(category, status, detail, estimated, tuple(notes), tuple(members.values()))
            for (category, status, detail, estimated, _), (members, notes) in grouped.items()]


def line_ranges(lines):
    """Compress consecutive lines without concealing gaps."""
    runs = []
    for number in sorted(set(lines)):
        if runs and number == runs[-1][1] + 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return ', '.join(str(first) if first == last else f'{first}–{last}' for first, last in runs)


SEVERITIES = ('Critical', 'High', 'Medium', 'Low', 'Info')
PENALTY = {'Critical': 25, 'High': 10, 'Medium': 5, 'Low': 1, 'Info': 0}
SCORE_RULE = ('Program health score: 100 minus 25 per Critical, 10 per High, 5 per Medium and 1 per Low finding '
              '(never below 0). It ranks programs for review; it is not a pass mark or a safety rating.')
SEVERITY_RULE = ('Severity: invalid payload or TCP settings are High (a mismatched load model is a leading cause of protective stops '
                 'and joint wear); motion values outside UR guidance or the entered specification, blend overlaps and payload '
                 'transitions are Medium; move-distance and efficiency items are Low.')
UR_DOCUMENTED = 'UR documented recommendation'
INFERENCE = 'Engineering best practice / inference'

# category: (severity, why it matters)
CATEGORY_RULES = {
    CATEGORIES[0]: ('High', 'The payload mass and centre of gravity tell the controller what it carries. A mismatch is a leading cause of protective stops and adds wear to every joint.'),
    CATEGORIES[1]: ('High', 'The payload must change when a part is picked or placed; otherwise the controller works with the wrong load for part of the cycle.'),
    CATEGORIES[2]: ('High', 'A wrong TCP moves every linear path, blend and speed limit to the wrong point on the tool.'),
    CATEGORIES[3]: ('High', 'The active TCP must change with the tool; a stale TCP shifts every following linear move.'),
    CATEGORIES[4]: ('Medium', 'Acceleration above the UR starting value raises joint torque and current, which causes protective stops and wear, most of all with a heavy payload at long reach.'),
    CATEGORIES[5]: ('Medium', 'Overlapping or oversized blends make the controller shrink or skip blends, so the executed path differs from the taught one.'),
    CATEGORIES[6]: ('Medium', 'A blend interrupted by a wait or branch becomes a stop-start, adding cycle time and torque peaks.'),
    CATEGORIES[7]: ('Medium', 'Speeds above the applicable specification can trigger safety-limit stops.'),
    CATEGORIES[8]: ('Low', 'Very short or zero-length moves cause unnecessary stop-start motion.'),
    CATEGORIES[9]: ('Medium', 'An instantaneous payload change on a heavy or vacuum-held part gives the controller a sudden load step.'),
    CATEGORIES[10]: ('Medium', 'Waits and blocking calls inside blended process motion interrupt the path.'),
    CATEGORIES[11]: ('Low', 'Busy loops and repeated connections load the controller and can cause "runtime too much behind" stops.'),
}
LIMIT = re.compile(r'Exceeds (UR suggested starting value|user-supplied specification) ≤([0-9.eE+-]+) ([^.]+)\.')


def fix_for(category, detail):
    """A specific correction, with the target value when the check states one."""
    limit = LIMIT.search(detail)
    name = 'a' if category == CATEGORIES[4] else 'v'
    if limit and category in (CATEGORIES[4], CATEGORIES[7]):
        basis, value, unit = limit.groups()
        if basis.startswith('UR'):
            return (f'Lower {name} to {float(value):g} {unit} or less (UR suggested starting value), or validate the higher value '
                    'on the robot with the real payload before production.')
        return f'Lower {name} to {float(value):g} {unit} or less, or update the specification if it is out of date.'
    if 'Non-positive' in detail:
        return 'Correct the non-positive value in the move command.'
    if 'OptiMove fraction' in detail:
        return 'Use an OptiMove fraction between 0 and 1.'
    if category == CATEGORIES[5]:
        return ('Reduce the blend radius to less than half of each neighbouring move distance shown, or move the waypoints '
                'apart; use r=0 at pick and place points that must be reached exactly.')
    if category in (CATEGORIES[0], CATEGORIES[1]):
        return 'Correct the payload call: measured mass in kg, centre of gravity in m from the flange, and inertia where needed (set_target_payload).'
    if category in (CATEGORIES[2], CATEGORIES[3]):
        return 'Correct the TCP from the measured tool (x, y, z in m; rotation vector in rad) and switch it with set_tcp at tool changes.'
    return 'Correct the value on the listed line and re-run Program Check.'


@dataclass
class ProgramFinding:
    id: str
    severity: str
    category: str
    lines: tuple
    issue: str
    fix: str
    why: str
    evidence: str
    basis: str
    references: tuple = ()

    @property
    def location(self):
        if not self.lines:
            return 'Whole program'
        return ('Script line ' if len(self.lines) == 1 else 'Script lines ') + line_ranges(self.lines)


@dataclass
class Observation:
    category: str
    items: list[str]
    lines: tuple

    @property
    def location(self):
        if not self.lines:
            return 'Whole program'
        if len(self.lines) <= 12:
            return 'Script lines ' + line_ranges(self.lines)
        return f'{len(self.lines)} script lines, from {self.lines[0]} to {self.lines[-1]}'


@dataclass
class ProgramReport:
    findings: list[ProgramFinding] = field(default_factory=list)
    observations: list[Observation] = field(default_factory=list)
    values: list = field(default_factory=list)  # FindingGroups that are source values or within guidance
    score: int = 100

    def counts(self):
        return [(name, sum(f.severity == name for f in self.findings)) for name in SEVERITIES]

    def actions(self):
        """Fixes in priority order, one per distinct fix, citing the findings they close."""
        steps = {}
        for finding in self.findings:
            steps.setdefault((finding.severity, finding.fix), []).append(finding.id)
        ordered = sorted(steps.items(), key=lambda kv: SEVERITIES.index(kv[0][0]))
        return [f'{fix} [{", ".join(ids)}]' for (_severity, fix), ids in ordered]


def program_report(review):
    """Findings, observations and score of one ProgramReview (deterministic order)."""
    report = ProgramReport()
    groups = group_findings(review.findings)
    issues = [g for g in groups if g.status == 'Review recommended']
    issues.sort(key=lambda g: (SEVERITIES.index(CATEGORY_RULES.get(g.category, ('Medium',))[0]),
                               CATEGORIES.index(g.category) if g.category in CATEGORIES else 99, g.lines))
    for number, group in enumerate(issues, 1):
        severity, why = CATEGORY_RULES.get(group.category, ('Medium', 'The source value departs from the guidance used by this check.'))
        example = next((f for f in group.findings if f.evidence), None)
        evidence = f'Line {example.line}: {example.evidence[:240]}' + ('…' if len(example.evidence) > 240 else '') if example else ''
        report.findings.append(ProgramFinding(
            f'P-{number:02d}', severity, group.category, group.lines, group.detail, fix_for(group.category, group.detail),
            why, evidence, UR_DOCUMENTED if any(f.reference or f.category == CATEGORIES[4] for f in group.findings) else INFERENCE,
            group.references))
    manual = {}
    for group in groups:
        if group.status == 'Manual review':
            items, lines = manual.setdefault(group.category, ([], set()))
            items.append(group.detail)
            lines.update(group.lines)
        elif group.status != 'Review recommended':
            report.values.append(group)
    order = {name: index for index, name in enumerate(CATEGORIES)}
    report.observations = [Observation(category, list(dict.fromkeys(items)), tuple(sorted(lines)))
                           for category, (items, lines) in sorted(manual.items(), key=lambda kv: order.get(kv[0], 99))]
    report.score = max(0, 100 - sum(PENALTY[f.severity] for f in report.findings))
    return report
