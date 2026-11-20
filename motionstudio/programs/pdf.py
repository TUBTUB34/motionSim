"""Readable, grouped Program Check PDFs; analysis values are never recalculated."""
from pathlib import Path
import os

import tempfile
from matplotlib.backends.backend_pdf import PdfPages
from motionstudio.reporting.pdf import Chip, ReportDocument, INK, MUTED, BOTTOM
from motionstudio.core.rendering import RENDER_LOCK
from motionstudio.programs.check import REFERENCES, CATEGORIES
from motionstudio.programs.findings import (ESTIMATE, FindingGroup, group_findings, line_ranges,  # noqa: F401 (re-exported)
                                            program_report, SCORE_RULE, SEVERITY_RULE)
from motionstudio.core.about import APP_NAME

NOTE = ('Independent source review. Not a UR certification or safety validation. '
        'Validate runtime behavior and physical setup on the robot.')
STATUS = {'Review recommended': 'Needs attention', 'Manual review': 'Needs verification',
          'Source observation': 'Source value', 'Within guideline': 'Within guideline'}
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
            report = program_report(review)
            name = review.metadata.get('name', review.pair.name)
            # 1. Executive summary
            page.new_page('Program Check Report')
            page.paragraph(name + ' · source review against UR guidance', size=10.5, color=MUTED)
            page.y += 10
            page.rows('Program at a glance', [
                ('Saved software', review.metadata.get('lastSavedIn', 'Unavailable')),
                ('Guidance', review.profile),
                ('Installation', review.metadata.get('reviewInstallation', 'Unavailable — installation settings omitted').replace('\\', '/').rsplit('/', 1)[-1]),
                ('Source coverage', f'{review.script_lines:,} script lines; ' + ', '.join(f'{n} {name}' for name, n in review.counts.items() if name in ('movej', 'movel', 'movep', 'movec', 'optimovej', 'optimovel', 'speedj', 'speedl', 'servoj'))),
            ], label_width=118)
            serious = sum(count for severity, count in report.counts() if severity in ('Critical', 'High'))
            status = 'Critical' if serious and any(f.severity == 'Critical' for f in report.findings) else 'High' if serious else 'Medium' if report.findings else 'Good'
            summary = (f'{len(report.findings)} finding(s) in {len({l for f in report.findings for l in f.lines})} source line(s); '
                       f'{len(report.observations)} topic(s) need verification on the robot.') if report.findings else \
                      'No source value departs from UR guidance or the entered specification. This does not establish compliance: verify the observations listed later.'
            page.banner(status, f'Program health score: {report.score} / 100', summary)
            page.tiles([(str(report.score), 'program health score'),
                        (str(serious), 'critical or high findings'),
                        (str(sum(f.severity == 'Medium' for f in report.findings)), 'medium findings'),
                        (str(len(report.observations)), 'topics to verify')])
            if report.findings:
                page.heading('Top risks', 11)
                page.table(('ID', 'Severity', 'Category', 'Location'),
                           [(f.id, Chip(f.severity), f.category, f.location) for f in report.findings[:3]],
                           (.1, .18, .4, .32), size=8)
            page.paragraph('Findings by severity: ' + ' · '.join(f'{name} {count}' for name, count in report.counts()), size=8.5, color=MUTED)
            page.y += 4
            page.paragraph('This review does not replace a risk assessment (ISO 10218 / ISO/TS 15066). Values that depend on variables use their saved '
                           'initial value; the value at run time is not proven. UR acceleration recommendations are starting points, not safety limits.',
                           size=8.5, color=MUTED)

            # 2. Findings table, 3. details
            if report.findings:
                page.start_section('Findings')
                page.table(('ID', 'Severity', 'Category', 'Location', 'Issue', 'Recommended fix'),
                           [(f.id, Chip(f.severity), f.category, f.location, f.issue, f.fix) for f in report.findings],
                           (.07, .12, .17, .12, .26, .26), size=7.5)
                page.start_section('Finding details')
                by_category = {}
                for finding in report.findings:
                    by_category.setdefault(finding.category, []).append(finding)
                for category, findings in by_category.items():
                    page.ensure(140)
                    page.heading(f'{category} · {", ".join(f.id for f in findings)}', 11.5)
                    references = sorted({n for f in findings for n in f.references})
                    page.table(('', ''), [(('Why it matters', MUTED, 'bold'), findings[0].why),
                                          (('Basis', MUTED, 'bold'), '; '.join(dict.fromkeys(f.basis for f in findings))
                                           + ('. References ' + ', '.join(f'[{n}]' for n in references) if references else '') + '.')],
                               (.18, .82), size=8.5, header=False)
                    page.y -= 6
                    page.table(('ID', 'Source evidence'), [(f.id, f.evidence or f.location) for f in findings], (.08, .92), size=7.5)

            # 4. Observations / needs verification
            if report.observations:
                page.start_section('Observations / needs verification')
                page.paragraph('The source alone cannot decide these. They are not findings; check them on the robot or enter the missing specification.', size=9, color=MUTED)
                page.y += 6
                rows = []
                for item in report.observations:
                    shown = item.items[:2] + ([f'{len(item.items)-2} more items of this kind.'] if len(item.items) > 2 else [])
                    rows.append((item.category, '\n'.join(shown), item.location))
                page.table(('Topic', 'What needs verifying', 'Where'), rows, (.2, .58, .22), size=7.5, bold_first=True)

            # 5. Prioritised action list
            page.start_section('Prioritised action list')
            actions = report.actions() + [f'Verify on the robot: {item.category.lower()} (see Observations).' for item in report.observations]
            for number, action in enumerate(actions or ['No source corrections are needed; keep this report as the baseline for the next change.'], 1):
                page.bullet(action, f'{number}.', size=9, indent=22)

            page.new_page('Checks and source values')
            page.paragraph('The values the review read from the program, grouped by topic. Findings and observations above are not repeated here.')
            page.y += 6
            for label, meaning in (('Source value', 'what is saved in the program or installation, not proof that it is physically correct.'),
                                   ('Within guideline', 'the saved value is inside the selected guidance; this is not a safety approval.')):
                page.bullet(f'{label}: {meaning}', size=9)
            page.y += 10
            order = {'Source observation': 0}
            remaining = report.values
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
            page.heading('How findings are rated')
            page.paragraph(SEVERITY_RULE, size=9)
            page.y += 4
            page.paragraph(SCORE_RULE, size=9)
            page.y += 4
            page.paragraph('Each finding is marked “UR documented recommendation” when a UR reference supports the check, otherwise “Engineering best practice / inference”.', size=9)
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
