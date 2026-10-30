"""Vector PDF reports using the supplied Program Check report's visual structure.

Program facts are static source observations, never compliance certification or
claims that a particular branch executed. All plotted telemetry covers the full
selected recording, independently of the desktop viewport and selected joint.
"""
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import os
import re
import tempfile

import numpy as np
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

from flight_data import JOINTS, UNITS
from flight_charts import RENDER_LOCK

PAGE_WIDTH, PAGE_HEIGHT = 595.32, 841.92
LEFT, RIGHT, BOTTOM = 70.0, 525.32, 758.0
INK, MUTED, BAR = '#073746', '#657e85', '#677f84'
ACTUAL, TARGET = '#278ec1', '#79ad35'
NOTE = ('Source observations and recorded measurements only. Program compliance and '
        'application suitability are not certified. Missing measurements remain unavailable; '
        'derived quantities are identified. Review the original recording and robot program.')


@dataclass(frozen=True)
class ProgramFacts:
    line_count: int
    calls: tuple
    urcaps: tuple

    def named(self, *names):
        return tuple(item for item in self.calls if item[1] in names)


def program_facts(script):
    """Count actual call statements, excluding comments and PolyScope label strings."""
    calls, urcaps = [], []
    for number, line in enumerate(script.splitlines(), 1):
        match = re.match(r'^\s*(movej|movel|movep|set_payload|set_target_payload|set_tcp)\s*\((.*)\)\s*(?:#.*)?$', line)
        if match:
            calls.append((number, match[1], match[2]))
        source = re.match(r'^\s*#\s*Source:\s*(.+)$', line)
        if source:
            urcaps.append((number, source[1]))
    return ProgramFacts(len(script.splitlines()), tuple(calls), tuple(urcaps))


def default_pdf_name(report):
    stem = re.sub(r'[^A-Za-z0-9_.-]+', '_', Path(report.name).stem).strip('._') or 'flight'
    return f'{stem}_flight_report.pdf'


def measurement_summary(report):
    """Full-record finite-value statistics; no imputation and no inferred limits."""
    rows = []
    for joint, name in enumerate(JOINTS):
        position = report.series('position', joint)
        velocity = report.series('velocity', joint)
        current = report.series('current', joint)
        p, v, c = (x[np.isfinite(x)] for x in (position, velocity, current))
        rows.append(dict(joint=name, position_min=float(p.min()) if p.size else None,
                         position_max=float(p.max()) if p.size else None,
                         peak_speed=float(np.abs(v).max()) if v.size else None,
                         rms_current=float(np.sqrt(np.mean(c*c))) if c.size else None,
                         position_samples=int(p.size)))
    return rows


class _Report:
    def __init__(self, pdf, source, subtitle="FLIGHT RECORD ANALYSIS", note=NOTE):
        self.pdf, self.source = pdf, source
        self.subtitle, self.note = subtitle, note
        self.figure = None
        self.page_number = 0
        self.renderer = RendererAgg(600, 850, 72)

    def lines(self, text, width, size=9, weight='normal'):
        font = FontProperties(family='DejaVu Sans', size=size, weight=weight)
        def fits(value):
            with RENDER_LOCK:
                return self.renderer.get_text_width_height_descent(value, font, False)[0] <= width
        result = []
        for paragraph in str(text).split('\n'):
            current = ''
            for word in paragraph.split():
                candidate = f'{current} {word}'.strip()
                if current and not fits(candidate):
                    result.append(current)
                    current = ''
                # Long filenames and source expressions must not escape their box.
                while not fits(word) and len(word) > 1:
                    cut = len(word) - 1
                    while cut > 1 and not fits(word[:cut]):
                        cut -= 1
                    if current:
                        result.append(current)
                        current = ''
                    result.append(word[:cut])
                    word = word[cut:]
                current = f'{current} {word}'.strip()
            result.append(current)
        return result

    def text(self, x, y, text, size=9, color=INK, weight='normal', **kwargs):
        return self.figure.text(x/PAGE_WIDTH, 1-y/PAGE_HEIGHT, str(text),
                                fontsize=size, color=color, weight=weight,
                                family='DejaVu Sans', va='top', **kwargs)

    def paragraph(self, text, x=LEFT, width=RIGHT-LEFT, size=9, color=INK, weight='normal'):
        lines = self.lines(text, width, size, weight)
        for line in lines:
            self.text(x, self.y, line, size, color, weight)
            self.y += size * 1.4

    def rect(self, x, y, width, height, color, edge='#d1d1d1'):
        self.figure.add_artist(Rectangle((x/PAGE_WIDTH, 1-(y+height)/PAGE_HEIGHT),
                                        width/PAGE_WIDTH, height/PAGE_HEIGHT,
                                        transform=self.figure.transFigure,
                                        facecolor=color, edgecolor=edge, linewidth=.7, zorder=-1))

    def new_page(self, title=None):
        self.finish_page()
        self.page_number += 1
        self.figure = Figure(figsize=(PAGE_WIDTH/72, PAGE_HEIGHT/72), dpi=72, facecolor='white')
        self.text(LEFT, 46, 'UR MOTION STUDIO', 11, INK, 'bold')
        self.text(LEFT, 64, self.subtitle, 7, MUTED)
        self.y = 108
        if title:
            self.paragraph(title, size=25)
            self.y += 15

    def finish_page(self):
        if self.figure is None:
            return
        y = 786
        for line in self.lines(self.note, RIGHT-LEFT, 5.4):
            self.text(LEFT, y, line, 5.4, MUTED)
            y += 7
        self.text(PAGE_WIDTH/2, 822, str(self.page_number), 7, INK, ha='center')
        with RENDER_LOCK:
            self.pdf.savefig(self.figure, dpi=144)
            self.figure.clear()
        self.figure = None

    def reserve(self, height, heading='Detailed findings (continued)'):
        if self.y + height > BOTTOM:
            self.new_page(heading)

    def bar(self, title):
        self.rect(LEFT, self.y, RIGHT-LEFT, 22, BAR)
        self.text(LEFT+8, self.y+5, title, 9, 'white', 'bold')
        self.y += 22

    def card(self, title, paragraphs):
        parts = [(self.lines(text, RIGHT-LEFT-24, size), size, color, weight)
                 for text, size, color, weight in paragraphs]
        height = 22 + 14 + sum(len(lines)*size*1.4 + 7 for lines, size, _, _ in parts)
        self.reserve(height+12)
        top = self.y
        self.rect(LEFT, top, RIGHT-LEFT, height, 'white')
        self.bar(title)
        self.y += 8
        for lines, size, color, weight in parts:
            for line in lines:
                self.text(LEFT+12, self.y, line, size, color, weight)
                self.y += size*1.4
            self.y += 7
        self.y = top+height+14

    def table(self, title, rows, label_width=200):
        self.reserve(70)
        self.bar(title)
        for index, (label, value) in enumerate(rows):
            left = self.lines(label, label_width-16, 8.4, 'bold')
            right = self.lines(value, RIGHT-LEFT-label_width-16, 8.4)
            height = max(len(left), len(right))*11.8+14
            if self.y+height > BOTTOM:
                self.new_page()
                self.bar(title+' (continued)')
            self.rect(LEFT, self.y, RIGHT-LEFT, height, '#ececec' if index%2 == 0 else 'white')
            for line_index, line in enumerate(left):
                self.text(LEFT+8, self.y+7+line_index*11.8, line, 8.4, INK, 'bold')
            for line_index, line in enumerate(right):
                self.text(LEFT+label_width+8, self.y+7+line_index*11.8, line, 8.4, MUTED)
            self.y += height
        self.y += 14


def _observations(report, facts):
    payloads = facts.named('set_payload', 'set_target_payload')
    tcps = facts.named('set_tcp')
    motions = facts.named('movej', 'movel', 'movep')
    blends = [call for call in motions if re.search(r'\br\s*=', call[2])]
    def first(calls):
        if not calls:
            return 'No matching call statement is available in the supplied program source.'
        number, name, arguments = calls[0]
        return f'First source call at script line {number}: {name}({arguments}).'
    return [
        ('Initial Payload Mass, CoG and Inertia', first(payloads)),
        ('Dynamic payload changes', f'{len(payloads)} payload call statements appear in the source. '
         'This count does not establish which calls executed or whether the physical payload is correct.'),
        ('Initial TCP', first(tcps)),
        ('Dynamic TCP changes', f'{len(tcps)} TCP call statements appear in the source. '
         'Expression values and runtime changes require program review.'),
        ('Acceleration limits', 'The following charts show recorded target acceleration. Actual '
         'acceleration is recorded when available, otherwise derived from measured velocity. Application-specific acceleration limits '
         'are not established by the recording; no compliance verdict is inferred.'),
        ('Blend radius overlap', f'{len(blends)} motion call statements include a blend-radius argument. '
         'Dynamic waypoints and expression evaluation are not resolved; geometric overlap is not assessed.'),
        ('Blended motion continuation', 'Control-flow, helper functions and runtime branches require '
         'manual review. A command following a move in the source alone does not prove that blending was aborted.'),
    ]


def _summary_page(doc, report, facts):
    doc.new_page('Program Check Report')
    doc.paragraph('Selected flight · '+report.name, size=10, color=MUTED)
    doc.y += 14
    doc.card('Reporting scope', [(NOTE, 8.5, INK, 'normal')])
    counts = Counter(call[1] for call in facts.calls)
    summary = (f"Program: {report.metadata.get('program', 'Not recorded')}\n"
               f"Source: {report.name}\n"
               f"Robot: {report.model or 'Unknown'} · Serial: {report.metadata.get('serial', 'Not recorded')}\n"
               f"Software: {report.metadata.get('software_version', 'Not recorded')} · "
               f"Program state: {report.metadata.get('program_state', 'Not recorded')}\n"
               f"Incident: {report.metadata.get('incident_time', 'Not recorded')}\n"
               f"Recorded payload: {report.metadata.get('payload_kg', 'Not recorded')} kg\n"
               f"Recorded TCP offset [m, rad]: {report.metadata.get('tcp_offset', 'Not recorded')}\n"
               f"{report.sample_count:,} samples · {report.duration:.3f} s · "
               f"{report.metadata.get('sample_rate_hz', 'Unknown')} Hz nominal\n"
               f"{facts.line_count:,} script lines; {counts['movej']} MoveJ, {counts['movel']} MoveL, "
               f"{counts['movep']} MoveP call statements.")
    doc.card('Report Summary', [('Overall result: Not assessed', 10, '#b27619', 'bold'),
                                (summary, 8.5, INK, 'normal')])
    caps = '\n'.join(f'{source} · script line {line}' for line, source in facts.urcaps) or 'No URCap source declarations found in the selected program.'
    doc.card('URCaps Used in This Program', [(caps, 8.5, INK, 'normal')])
    doc.table('Compliance Status', [
        ('Not assessed', 'No automated compliance certification is performed.'),
        ('Recorded', 'Numeric values are taken from the selected flight recording.'),
        ('Derived', 'Missing actual acceleration is derived from velocity; summary statistics are calculated.')])


def _chart_page(doc, report, joint, summary):
    doc.new_page(f'Joint {joint} · {JOINTS[joint]}')
    doc.paragraph('Detailed findings · complete recording', size=10, color=MUTED)
    doc.y += 5
    doc.paragraph(f'{report.name} | {report.sample_count:,} samples | {report.duration:.3f} s', size=8, color=MUTED)
    top, plot_height, step = 215.0, 96.0, 130.0
    for index, metric in enumerate(('position', 'velocity', 'acceleration', 'current')):
        chart_top = top+index*step
        axes = doc.figure.add_axes([86/PAGE_WIDTH, 1-(chart_top+plot_height)/PAGE_HEIGHT,
                                    (RIGHT-94)/PAGE_WIDTH, plot_height/PAGE_HEIGHT])
        is_derived = metric == 'acceleration' and f'Actual acceleration j{joint} [rad/s^2]' not in report.columns
        actual_label = 'Actual (derived)' if is_derived else 'Actual'
        any_values = False
        for target, color, label in [(False, ACTUAL, actual_label), (True, TARGET, 'Target')]:
            values = report.series(metric, joint, target)
            segments = list(report.valid_segments(values))
            for segment_index, segment in enumerate(segments):
                axes.plot(report.timestamps[segment], values[segment], color=color,
                          linewidth=.65, label=label if segment_index == 0 else None,
                          marker='.' if segment.stop-segment.start == 1 else None)
                any_values = True
        if metric == 'current':
            for envelope, style in [('current_upper', '-'), ('current_lower', '--')]:
                values = report.series(envelope, joint)
                for segment_index, segment in enumerate(report.valid_segments(values)):
                    axes.plot(report.timestamps[segment], values[segment], color='#c34f59',
                              linewidth=.65, linestyle=style,
                              label=envelope.replace('_', ' ').title() if segment_index == 0 else None)
                    any_values = True
        if not any_values:
            axes.text(.5, .5, 'No recorded channel', transform=axes.transAxes,
                      ha='center', color=MUTED, fontsize=8)
        else:
            axes.legend(loc='upper right', fontsize=6, frameon=False, ncol=2)
        axes.set_title(metric.title(), loc='left', fontsize=9, color=INK, pad=5)
        axes.set_ylabel(UNITS[metric], fontsize=7, color=INK)
        axes.set_xlabel('Recording timestamp [s] · controller uptime', fontsize=6, color=MUTED, labelpad=2)
        axes.tick_params(labelsize=6, colors=MUTED, length=2)
        axes.ticklabel_format(axis='x', style='plain', useOffset=False)
        axes.grid(True, color='#dfe5e6', linewidth=.5)
        for spine in axes.spines.values():
            spine.set_color('#cbd4d6')
        if report.sample_count > 1:
            axes.set_xlim(float(report.timestamps[0]), float(report.timestamps[-1]))
    def value(number):
        return 'Unavailable' if number is None else f'{number:.4g}'
    doc.y = 739
    doc.paragraph(f"Position range: {value(summary['position_min'])} to {value(summary['position_max'])} rad · "
                  f"Peak |speed|: {value(summary['peak_speed'])} rad/s · RMS current: {value(summary['rms_current'])} A",
                  size=6.5, color=MUTED)
    acceleration_basis = ('recorded' if f'Actual acceleration j{joint} [rad/s^2]' in report.columns
                          else 'finite differences of measured velocity')
    doc.paragraph(f'Gaps are preserved. Actual acceleration: {acceleration_basis}. No inferred current limits.',
                  size=6.5, color=MUTED)


def export_flight_pdf(report, path):
    """Write a complete selected-flight PDF atomically; return its destination Path."""
    if report.sample_count == 0:
        raise ValueError('Cannot export a flight with no timestamped samples.')
    destination = Path(path)
    facts = program_facts(report.script)
    observations = _observations(report, facts)
    summaries = measurement_summary(report)
    handle, temporary_name = tempfile.mkstemp(prefix='.flight-report-', suffix='.pdf', dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with PdfPages(temporary, metadata={'Title': f'Flight Report - {report.name}',
                                          'Author': 'UR Motion Studio',
                                          'Subject': 'Full-record measurements and source program observations'}) as pdf:
            doc = _Report(pdf, report)
            _summary_page(doc, report, facts)
            doc.new_page()
            checklist = [(title, detail) for title, detail in observations]
            doc.table('Checklist Details', checklist, label_width=190)
            doc.new_page('Detailed findings')
            doc.paragraph('Static source observations. These statements do not certify execution, physical setup or compliance.',
                          size=9, color=MUTED)
            doc.y += 14
            # Source setup topics; acceleration and blending scope are retained in
            # the checklist and recommendation page, avoiding invented verdicts.
            for title, detail in observations[:4]:
                doc.card(title, [('Status: Source observation', 8.5, MUTED, 'bold'),
                                 (detail, 8.5, INK, 'normal')])
            for joint, summary in enumerate(summaries):
                _chart_page(doc, report, joint, summary)
            doc.new_page('Recommended Action Summary')
            actions = [
                ('1. Correlate the recorded event with the program',
                 'Use controller-uptime timestamps and source script lines. Incident wall-clock time is metadata; '
                 'it is not treated as an exact conversion for every sample.'),
                ('2. Validate the configured payload and TCP',
                 'Compare the source calls and recorded metadata with the actual tool and load. '
                 'A call count does not establish physical correctness.'),
                ('3. Review acceleration and blended motions',
                 'Evaluate limits, dynamic waypoints and control flow in the application context. '
                 'No unsupported acceleration-limit, blend-overlap or continuation verdict is generated.'),
                ('4. Keep measured and derived data distinct',
                 'Where actual acceleration is absent, it is calculated from recorded velocity within valid segments. '
                 'RMS current and peak speed use finite samples across the entire selected flight. '
                 'Robot geometry is nominal; factory calibration is not applied.'),
            ]
            for title, detail in actions:
                doc.card(title, [(detail, 8.5, INK, 'normal')])
            warnings = '\n'.join(report.warnings) or 'No parser warnings for this recording.'
            doc.card('Data quality and coverage', [(warnings, 8.5, MUTED, 'normal')])
            doc.finish_page()
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
