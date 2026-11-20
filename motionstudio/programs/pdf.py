"""Readable, grouped Program Check PDFs; analysis values are never recalculated."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
import tempfile
from matplotlib.backends.backend_pdf import PdfPages
from motionstudio.reporting.pdf import ReportDocument, INK, MUTED, LEFT, RIGHT, BOTTOM
from motionstudio.core.rendering import RENDER_LOCK
from motionstudio.programs.check import REFERENCES, CATEGORIES
from motionstudio.core.about import APP_NAME

NOTE = ('Independent source review. Not a UR certification or safety validation. '
        'Validate runtime behavior and physical setup on the robot.')
STATUS = {'Review recommended': 'Needs attention', 'Manual review': 'Needs verification',
          'Source observation': 'Source value', 'Within guideline': 'Within guideline'}
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


def _source_label(group):
    if not group.lines:
        return 'Whole program'
    return ('Script line ' if len(group.lines) == 1 else 'Script lines ') + line_ranges(group.lines)


def deployment_follow_up(document):
    """Keep physical checks separate from every computed finding and rating."""
    from motionstudio.commissioning.records import DEPLOYMENT_CHECKS
    physical_keys = {'mounting', 'environment', 'electrical', 'risk_assessment',
                     'safety_validation', 'inspection'}
    document.new_page('Physical installation follow-up')
    document.paragraph('Not assessed by this report. Source files and telemetry cannot verify these items. '
                       'Record the inspection evidence and outstanding actions in the deployment checklist.')
    document.y += 12
    rows = []
    for check in DEPLOYMENT_CHECKS:
        if check.key not in physical_keys:
            continue
        sources = '; '.join(check.sources).replace('Installation Guide v1.3.3, PDF ', 'Guide ')
        sources = sources.replace('Deployment Workshop 2024, PDF ', 'Workshop ')
        rows.append((check.title, [(check.guidance, INK, 'normal'), ('Source: ' + sources, MUTED, 'normal')]))
    document.rows('Manual checks · none assessed by this report', rows, label_width=125)
    document.paragraph('Guide: UR Installation Guide v1.3.3. Workshop: UR Application Deployment Workshop, May 2024. '
                       'Page numbers refer to the supplied PDFs. Apply the current model-specific manual; these prompts are not a complete safety assessment.', size=8.5, color=MUTED)


def export_program_pdf(review, destination):
    groups = group_findings(review.findings)
    issues = [group for group in groups if group.status == 'Review recommended']
    remaining = [group for group in groups if group.status != 'Review recommended']
    affected = {line for group in issues for line in group.lines}
    definitions = dict.fromkeys(note for group in groups for note in group.assumptions)
    definitions = {note: f'D{index}' for index, note in enumerate(definitions, 1)}
    definition_lines = {note: set() for note in definitions}
    for group in groups:
        for finding in group.findings:
            for match in ESTIMATE.finditer(finding.detail):
                if finding.line:
                    definition_lines[' '.join(match.group(1).split())].add(finding.line)

    def basis(group):
        refs = ('Reference ' if len(group.references) == 1 else 'References ') + ', '.join(f'[{n}]' for n in group.references)
        if group.estimated:
            return 'Source estimate using ' + ', '.join(definitions[note] for note in group.assumptions) + ' · ' + refs
        return refs

    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(suffix='.pdf', dir=destination.parent)
    os.close(fd)
    try:
        with RENDER_LOCK, PdfPages(temporary, metadata={'Title': f'Program Check — {review.pair.name}', 'Author': APP_NAME}) as pdf:
            page = ReportDocument(pdf, review.pair.name, subtitle='PROGRAM CHECK REPORT', note=NOTE)
            page.new_page('Program Check Report')
            page.paragraph(review.metadata.get('name', review.pair.name), size=14, weight='bold')
            page.y += 4
            count = len(issues)
            page.paragraph(f'{count} issue group' + ('' if count == 1 else 's') + f' · {len(affected)} affected source lines',
                           size=11, weight='bold', color='#9a4300' if issues else INK)
            page.paragraph('Repeated results are combined below. Every affected line is retained; different values, limits and calculation bases remain separate.')
            page.y += 12
            page.rows('Program at a glance', [
                ('Saved software', review.metadata.get('lastSavedIn', 'Unavailable')),
                ('Guidance', review.profile),
                ('Installation', review.metadata.get('reviewInstallation', 'Unavailable — installation settings omitted').replace('\\', '/').rsplit('/', 1)[-1]),
                ('Source coverage', f'{review.script_lines:,} script lines; ' + ', '.join(f'{n} {name}' for name, n in review.counts.items() if name in ('movej', 'movel', 'movep', 'movec', 'optimovej', 'optimovel', 'speedj', 'speedl', 'servoj'))),
                ('Needs verification', f'{sum(group.status == "Manual review" for group in remaining)} grouped checks need runtime evidence or additional information'),
            ], label_width=118)
            page.heading('Start here')
            if issues:
                page.paragraph('Review the grouped issues on the next pages. Each entry gives the comparison, affected script lines and any source-estimate assumptions.')
            else:
                page.paragraph('No explicit source-value warnings were found. This does not establish compliance: check the unresolved items and source coverage below.')
            page.y += 8
            page.paragraph('Where a value depends on a variable, the review uses the variable’s saved initial value and labels the result a source estimate; the value at run time is not proven. UR acceleration recommendations are starting points, not safety limits. Unknown values are not treated as zero or a pass.')

            if issues:
                page.new_page('Issues to review')
                for index, group in enumerate(issues, 1):
                    parts = [(f'{index}. {group.category}', 12, INK, 'bold'), (group.detail, 10, INK, 'normal'),
                             (_source_label(group), 9, INK, 'bold'), (basis(group), 8.5, MUTED, 'normal')]
                    example = next((finding for finding in group.findings if finding.evidence), None)
                    if example:
                        text = example.evidence
                        if len(text) > 240:
                            text = text[:240] + '…'
                        parts.append((f'Example, line {example.line}: {text}', 8.5, MUTED, 'normal'))
                    height = 24 + sum(len(page.lines(text, RIGHT-LEFT, size, weight))*size*1.45+3 for text, size, _, weight in parts)
                    if page.y + height > BOTTOM and height < BOTTOM-185:
                        page.new_page('Issues to review (continued)')
                    elif index > 1:
                        page.rule(page.y)
                        page.y += 12
                    for text, size, color, weight in parts:
                        page.paragraph(text, size=size, color=color, weight=weight)
                        page.y += 3
                    page.y += 9

            page.new_page('Checks and source values')
            page.paragraph('Everything else the review read from the program, grouped by topic. Issues already listed are not repeated here.')
            page.y += 6
            for label, meaning in (('Needs verification', 'the source alone cannot decide; runtime evidence or a specification is missing.'),
                                   ('Source value', 'what is saved in the program or installation, not proof that it is physically correct.'),
                                   ('Within guideline', 'the saved value is inside the selected guidance; this is not a safety approval.')):
                page.bullet(f'{label}: {meaning}', size=9)
            page.y += 10
            order = {'Manual review': 0, 'Source observation': 1}
            categories = list(dict.fromkeys([*CATEGORIES, *(group.category for group in remaining)]))
            for category in categories:
                selected = sorted((group for group in remaining if group.category == category),
                                  key=lambda group: order.get(group.status, 2))
                page.rows(category, [(STATUS.get(group.status, group.status),
                                      [(group.detail, INK, 'normal'),
                                       (_source_label(group) + ' · ' + basis(group), MUTED, 'normal')])
                                     for group in selected], label_width=120)

            deployment_follow_up(page)
            page.new_page('Source notes')
            page.heading('How to interpret the review')
            for note in ('This is a static review of the saved program; no URScript is executed.',
                         'Counts include definitions and branches, not how often a statement runs.',
                         'Source values do not establish measured speeds or accelerations.',
                         'Blends depend on controller planning; a geometric warning does not prove a skipped waypoint.',
                         'Validate the physical payload, centre of gravity, inertia and TCP on the robot, including each workpiece or tool change.'):
                page.bullet(note)
            if definitions:
                page.y += 6
                page.paragraph('Results labelled “Source estimate using D…” were calculated from the saved initial values listed here. They are conditional estimates, not proof that those values were set or unchanged when a move ran.')
                page.y += 10
                page.rows('Saved initial values used for estimates', [
                    (label, [(note, INK, 'normal')] + ([('Used at script line' + ('' if len(definition_lines[note]) == 1 else 's') + ' '
                                                        + line_ranges(definition_lines[note]), MUTED, 'normal')]
                                                      if definition_lines[note] else []))
                    for note, label in definitions.items()], label_width=42)
            page.heading('Reviewed source files')
            if review.metadata.get('reviewInstallationNote'):
                page.paragraph(review.metadata['reviewInstallationNote'], size=9)
                page.y += 8
            for identity, digest in review.hashes.items():
                # File or archive name only: the operator's local folder is not published.
                path, separator, member = identity.partition(' :: ')
                page.paragraph(Path(path).name + separator + member, size=9)
                page.paragraph('SHA-256: ' + digest, size=8, color=MUTED)
                page.y += 8
            used_refs = sorted({n for group in groups for n in group.references})
            page.heading('UR references')
            for number in used_refs:
                if not 1 <= number <= len(REFERENCES):
                    continue
                title, url = REFERENCES[number-1]
                if page.y + 50 > BOTTOM:
                    page.new_page('Source notes (continued)')
                page.paragraph(f'[{number}] {title}', size=9, weight='bold')
                page.paragraph(url, size=8, color=MUTED)
                page.y += 8
            page.close()
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination
