"""Program Check PDF, using the separate white A4 reference report style."""
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from matplotlib.backends.backend_pdf import PdfPages
from flight_pdf import _Report, INK, MUTED
from flight_charts import RENDER_LOCK
from program_check import REFERENCES, CATEGORIES

NOTE = ('Independent source review by UR Motion Studio. Not a Universal Robots certification or safety validation. '
        'Runtime behavior, physical setup and application suitability require verification on the applicable robot.')


def _parameter_rows(findings):
    """Combine identical results while retaining every source line for review."""
    grouped = {}
    for finding in findings:
        grouped.setdefault((finding.status, finding.detail), []).append(finding.line)
    rows = []
    for (status, detail), lines in grouped.items():
        lines = sorted(set(lines))
        # Bound row height even for programs with thousands of identical checks.
        for start in range(0, len(lines), 24):
            rows.append(('Lines ' + ', '.join(map(str, lines[start:start + 24])),
                         status + ' · ' + detail))
    return rows


def export_program_pdf(review, destination):
    destination = Path(destination)
    fd, temporary = tempfile.mkstemp(suffix='.pdf', dir=destination.parent)
    os.close(fd)
    try:
        with RENDER_LOCK, PdfPages(temporary, metadata={'Title': f'Program Check — {review.pair.name}', 'Author': 'UR Motion Studio'}) as pdf:
            page = _Report(pdf, review.pair.name, subtitle='PROGRAM CHECK REPORT', note=NOTE)
            page.new_page('Program Check Report')
            page.paragraph('Static review of saved program source against published UR guidance. No program was executed and no robot was commanded. Resolvable numeric assignments and expressions are calculated; runtime inputs, execution order and physical correctness are not inferred.', size=10)
            page.y += 20
            page.table('PROGRAM SUMMARY', [
                ('Program', review.metadata.get('name', review.pair.name)),
                ('Software saved in URP', review.metadata.get('lastSavedIn', 'Unavailable')),
                ('Guidance profile', review.profile),
                ('Installation source', review.metadata.get('reviewInstallation', 'Not matched').replace('\\', '/').rsplit('/', 1)[-1]),
                ('Installation matching', review.metadata.get('reviewInstallationNote', 'No supplied installation context')),
                ('Script lines / motion calls', f'{review.script_lines:,} lines; ' + ', '.join(f'{review.counts[name]} {name}' for name in ('movej', 'movel', 'movep'))),
                ('Other motion commands', ', '.join(f'{count} {name}' for name, count in review.counts.items() if name in ('optimovej', 'optimovel', 'movec', 'servoj', 'speedj', 'speedl')) or 'None identified'),
                ('Payload / TCP source calls', f'{sum(review.counts[n] for n in ("set_payload", "set_target_payload", "set_payload_mass", "set_payload_cog"))} payload; {review.counts["set_tcp"]} TCP'),
                ('Generated', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')),
                ('Overall result', f'{sum(f.status == "Review recommended" for f in review.findings)} flagged source findings; review required'),
            ])
            page.card('SCOPE & INTERPRETATION', [
                ('UR deployment acceleration values are suggested starting points for CB-Series/e-Series, not specifications or safety limits. Confirm the selected profile, physical payload/TCP, controller version and application before acting.', 9, INK, 'normal'),
                ('Only supplied source is reviewed. Saved installation settings and source-defined constants are reported separately from runtime values. Helpers, branches, URCaps and motion are not executed. Unresolved inputs remain unassessed. Installation safety serialization is not treated as an active velocity specification. Counts exclude labels/comments but include definitions; they do not indicate execution frequency.', 9, MUTED, 'normal'),
            ])
            page.new_page('Review checklist')
            page.table('CHECKLIST', review.checklist, label_width=265)
            page.card('READ THE RESULTS', [
                ('Within guideline: a resolvable classic motion acceleration is within the cited starting value. Review recommended: a concrete source value deserves attention. Source observation: a configuration call exists. Manual review: the evidence cannot establish the runtime or physical result.', 9, INK, 'normal'),
            ])
            page.new_page('Detailed findings')
            for category in CATEGORIES:
                page.reserve(150)
                page.paragraph(category, size=16, weight='bold')
                page.y += 12
                selected = [f for f in review.findings if f.category == category]
                detailed = selected
                if category in (CATEGORIES[4], CATEGORIES[5], CATEGORIES[7], CATEGORIES[8]):
                    detailed = [f for f in selected if not f.line or f.status == 'Review recommended']
                    compact = [f for f in selected if f.line and f.status != 'Review recommended']
                    explanation = {
                        CATEGORIES[4]: 'Resolved accelerations are checked against entered specifications, or UR starting recommendations when the family is confirmed. Duration-based moves and runtime inputs remain unassessed. OptiMove fractions and ServoJ have distinct semantics.',
                        CATEGORIES[5]: 'Resolved blend radii are compared with known adjacent move distances and radius sums. Geometric warnings do not prove skipped waypoints; motion version and actual execution still matter.',
                        CATEGORIES[7]: 'Resolved velocities are checked against entered specifications. No generic robot velocity limit is assumed. Source values do not establish measured speed.',
                        CATEGORIES[8]: 'Distances are unblended translational lengths for resolvable adjacent source targets. Circular moves use arc length. Shared rigid-frame offsets can establish separation. Unknown starts, joint-space paths and uncertain runtime flow remain unassessed.',
                    }[category]
                    page.paragraph(explanation, size=9)
                    page.y += 14
                else:
                    compact = []
                for finding in detailed:
                    title = finding.status + (f' · Line {finding.line}' if finding.line else '')
                    parts = [(finding.detail, 9, INK, 'normal')]
                    if finding.evidence:
                        evidence = finding.evidence
                        if len(evidence) > 650:
                            evidence = evidence[:650] + '… [excerpt shortened; see source line]'
                        parts.append((evidence, 8, MUTED, 'normal'))
                    parts.append((f'UR reference [{finding.reference+1}]', 7, MUTED, 'normal'))
                    page.card(title, parts)
                if compact:
                    page.table('SOURCE PARAMETER EVIDENCE · ' + category.upper(),
                               _parameter_rows(compact), label_width=115)
            page.new_page('Sources & next steps')
            page.paragraph('Confirm matching URP/script versions. Review flagged lines in PolyScope, verify physical payload and TCP, and validate motion using the relevant robot/software manuals. Use flight measurements and incident logs to investigate observed behavior separately.', size=10)
            page.y += 18
            for identity, digest in review.hashes.items():
                page.card('INPUT SOURCE', [(identity, 8, INK, 'normal'), ('SHA-256: '+digest, 7, MUTED, 'normal')])
            for index, (title, url) in enumerate(REFERENCES, 1):
                page.reserve(75, heading='UR references (continued)')
                page.paragraph(f'[{index}] {title}', size=9, weight='bold')
                page.paragraph(url, size=7, color=MUTED)
                page.y += 12
            page.finish_page()
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination
