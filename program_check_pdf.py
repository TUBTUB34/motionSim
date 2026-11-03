"""Readable, grouped Program Check PDFs; analysis values are never recalculated."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import tempfile
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.font_manager import FontProperties
from flight_pdf import _Report, INK, MUTED, LEFT, RIGHT, BOTTOM
from flight_charts import RENDER_LOCK
from program_check import REFERENCES, CATEGORIES

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


class _ProgramReport(_Report):
    """Larger text and bounded pagination, isolated from flight-report styling."""
    section = 'Program review'

    def lines(self, text, width, size=9, weight='normal'):
        font = FontProperties(family='DejaVu Sans', size=size, weight=weight)
        def fits(value):
            return self.renderer.get_text_width_height_descent(value, font, False)[0] <= width
        result = []
        for paragraph in str(text).split('\n'):
            current = ''
            for word in paragraph.split():
                if current and not fits(current + ' ' + word):
                    result.append(current)
                    current = ''
                # Binary search keeps very long source paths inexpensive to wrap.
                while not fits(word) and len(word) > 1:
                    low, high = 1, len(word)
                    while low < high:
                        middle = (low+high+1)//2
                        if fits(word[:middle]):
                            low = middle
                        else:
                            high = middle-1
                    result.append(word[:low])
                    word = word[low:]
                current = (current + ' ' + word).strip()
            result.append(current)
        return result

    def new_page(self, title=None):
        if title and not title.endswith(' (continued)'):
            self.section = title
        super().new_page(title)

    def paragraph(self, text, x=LEFT, width=RIGHT-LEFT, size=10, color=INK, weight='normal'):
        leading = size * (1.8 if size >= 15 else 1.45)
        for line in self.lines(text, width, size, weight):
            if self.y + leading > BOTTOM:
                self.new_page(self.section + ' (continued)')
            self.text(x, self.y, line, size, color, weight)
            self.y += leading

    def heading(self, title):
        if self.y + 100 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.paragraph(title, size=15, weight='bold')
        self.y += 9

    def rows(self, title, rows, label_width=110):
        if not rows:
            return
        if self.y + 75 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.bar(title)
        for index, (label, value) in enumerate(rows):
            left = self.lines(label, label_width-16, 9, 'bold')
            right = self.lines(value, RIGHT-LEFT-label_width-16, 9.5)
            count = max(len(left), len(right))
            offset = 0
            while offset < count:
                room = int((BOTTOM-self.y-14) // 14)
                remaining = count-offset
                # Keep ordinary rows together; split oversized evidence safely.
                if room < 1 or (offset == 0 and remaining <= 32 and remaining > room):
                    self.new_page(self.section + ' (continued)')
                    self.bar(title + ' (continued)')
                    room = int((BOTTOM-self.y-14) // 14)
                take = min(room, remaining)
                height = take*14+14
                self.rect(LEFT, self.y, RIGHT-LEFT, height, '#f0f4f5' if index % 2 == 0 else 'white')
                for lines, x, size, weight in ((left, LEFT+8, 9, 'bold'),
                                               (right, LEFT+label_width+8, 9.5, 'normal')):
                    for n, line in enumerate(lines[offset:offset+take]):
                        self.text(x, self.y+7+n*14, line, size, INK, weight)
                self.y += height
                offset += take
        self.y += 16


def _source_label(group):
    return 'Lines ' + line_ranges(group.lines) if group.lines else 'Program context'


def deployment_follow_up(document):
    """Keep physical checks separate from every computed finding and rating."""
    from commissioning import DEPLOYMENT_CHECKS
    physical_keys = {'mounting', 'environment', 'electrical', 'risk_assessment',
                     'safety_validation', 'inspection'}
    document.new_page('Physical installation follow-up')
    document.paragraph('Not assessed by this report. Source files and telemetry cannot verify these items. '
                       'Record the inspection evidence and outstanding actions in the deployment checklist.', size=10)
    document.y += 12
    rows = []
    for check in DEPLOYMENT_CHECKS:
        if check.key not in physical_keys:
            continue
        sources = '; '.join(check.sources).replace('Installation Guide v1.3.3, PDF ', 'Guide ')
        sources = sources.replace('Deployment Workshop 2024, PDF ', 'Workshop ')
        rows.append((check.title, check.guidance + '\nSource: ' + sources))
    document.rows('Manual checks · all unassessed', rows, label_width=125)
    document.paragraph('Guide: UR Installation Guide v1.3.3. Workshop: UR Application Deployment Workshop, May 2024. '
                       'Page numbers refer to the supplied PDFs. Apply the current model-specific manual; these prompts are not a complete safety assessment.', size=8, color=MUTED)


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
        refs = 'UR ' + ', '.join(f'[{n}]' for n in group.references)
        if group.estimated:
            return 'Source estimate · ' + ', '.join(definitions[note] for note in group.assumptions) + ' · ' + refs
        return refs

    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(suffix='.pdf', dir=destination.parent)
    os.close(fd)
    try:
        with RENDER_LOCK, PdfPages(temporary, metadata={'Title': f'Program Check — {review.pair.name}', 'Author': 'UR Motion Studio'}) as pdf:
            page = _ProgramReport(pdf, review.pair.name, subtitle='PROGRAM CHECK REPORT', note=NOTE)
            page.new_page('Program review')
            page.paragraph(review.metadata.get('name', review.pair.name), size=17, weight='bold')
            page.y += 12
            count = len(issues)
            page.paragraph(f'{count} issue group' + ('' if count == 1 else 's') + f' · {len(affected)} affected source lines',
                           size=14, weight='bold', color='#9a4300' if issues else INK)
            page.paragraph('Repeated results are combined below. Every affected line is retained; different values, limits and calculation bases remain separate.', size=10)
            page.y += 14
            page.rows('PROGRAM AT A GLANCE', [
                ('Saved software', review.metadata.get('lastSavedIn', 'Unavailable')),
                ('Guidance', review.profile),
                ('Installation', review.metadata.get('reviewInstallation', 'Unavailable — installation settings omitted').replace('\\', '/').rsplit('/', 1)[-1]),
                ('Source coverage', f'{review.script_lines:,} script lines; ' + ', '.join(f'{n} {name}' for name, n in review.counts.items() if name in ('movej', 'movel', 'movep', 'movec', 'optimovej', 'optimovel', 'speedj', 'speedl', 'servoj'))),
                ('Needs verification', f'{sum(group.status == "Manual review" for group in remaining)} grouped checks need runtime evidence or additional information'),
                ('Generated', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')),
            ], label_width=115)
            page.heading('Start here')
            if issues:
                page.paragraph('Review the grouped issues on the next pages. Each entry gives the comparison, affected script lines and any source-estimate assumptions.')
            else:
                page.paragraph('No explicit source-value warnings were found. This does not establish compliance: check the unresolved items and source coverage below.')
            page.y += 10
            page.paragraph('Source estimates substitute saved initializers; initialization and runtime values are not proven. UR acceleration recommendations are starting points, not safety limits. Unknown values are not treated as zero or a pass.', size=10)

            if issues:
                page.new_page('Issues to review')
                for index, group in enumerate(issues, 1):
                    example_text = ''
                    examples = [f for f in group.findings if f.evidence]
                    if examples:
                        example = examples[0]
                        text = example.evidence
                        if len(text) > 240:
                            text = text[:240] + '…'
                        example_text = f'Example at line {example.line}: {text}'
                    parts = [(f'{index:02d}  {group.category}', 14), (group.detail, 11),
                             (_source_label(group), 10), (basis(group), 9), (example_text, 9)]
                    height = 30 + sum(len(page.lines(text, RIGHT-LEFT, size))*size*(1.8 if size >= 15 else 1.45) for text, size in parts if text)
                    if page.y + height > BOTTOM and height < BOTTOM-185:
                        page.new_page('Issues to review (continued)')
                    page.paragraph(f'{index:02d}  {group.category}', size=14, weight='bold')
                    page.y += 7
                    page.paragraph(group.detail, size=11)
                    page.y += 7
                    page.paragraph(_source_label(group), size=10, weight='bold')
                    page.paragraph(basis(group), size=9, color=MUTED)
                    if example_text:
                        page.paragraph(example_text, size=9, color=MUTED)
                    page.y += 16

            page.new_page('Checks & source values')
            page.paragraph('Issues already listed above are not repeated here. “Needs verification” means runtime evidence or a specification is missing. “Source value” records saved configuration, not physical correctness.', size=10)
            page.y += 15
            categories = list(dict.fromkeys([*CATEGORIES, *(group.category for group in remaining)]))
            for category in categories:
                selected = [group for group in remaining if group.category == category]
                if not selected:
                    continue
                rows = [(_source_label(group), STATUS.get(group.status, group.status) + '\n' + group.detail + '\n' + basis(group)) for group in selected]
                page.rows(category.upper(), rows)

            deployment_follow_up(page)
            page.new_page('Source notes')
            page.heading('How to interpret the review')
            page.paragraph('This is a static source review; no URScript is executed. Counts include definitions and branches, not execution frequency. Source values do not establish measured speeds or accelerations. Blends depend on controller planning; a geometric warning does not prove a skipped waypoint. Validate physical payload, centre of gravity, inertia and TCP, including each workpiece or tool change.')
            page.y += 12
            if definitions:
                page.paragraph('D references identify saved initializer substitutions. They are conditional estimates, not proof that those values were initialized or unchanged when a move ran.')
                page.y += 10
                page.rows('INITIALIZER EVIDENCE', [
                    (label, note + ('\nUsed at script lines ' + line_ranges(definition_lines[note])
                                    if definition_lines[note] else ''))
                    for note, label in definitions.items()], label_width=42)
            page.heading('Reviewed source files')
            if review.metadata.get('reviewInstallationNote'):
                page.paragraph(review.metadata['reviewInstallationNote'], size=9)
                page.y += 10
            for identity, digest in review.hashes.items():
                page.paragraph(identity, size=9)
                page.paragraph('SHA-256: ' + digest, size=8, color=MUTED)
                page.y += 12
            used_refs = sorted({n for group in groups for n in group.references})
            page.heading('UR references')
            for number in used_refs:
                if not 1 <= number <= len(REFERENCES):
                    continue
                title, url = REFERENCES[number-1]
                if page.y + 65 > BOTTOM:
                    page.new_page('UR references (continued)')
                page.paragraph(f'[{number}] {title}', size=9, weight='bold')
                page.paragraph(url, size=8, color=MUTED)
                page.y += 10
            page.finish_page()
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination
