"""Flight Recording Report PDFs, and the A4 page frame shared by every report.

All figures are recorded measurements over the complete selected recording,
independently of the desktop viewport and selected joint. No limit is inferred
and no verdict is given; missing channels stay unavailable, derived quantities
are labelled, and program facts are static source counts, never proof that a
particular branch executed.
"""
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import tempfile

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure
from matplotlib.font_manager import FontProperties
from matplotlib.lines import Line2D
from matplotlib.patches import Patch, Rectangle
from matplotlib.textpath import text_to_path

from motionstudio.flight.data import JOINTS
from motionstudio.flight.charts import RENDER_LOCK, script_line_at
from motionstudio.flight.stats import Peak, joint_statistics, protective_stops, recording_overview, state_changes

PAGE_WIDTH, PAGE_HEIGHT = 595.32, 841.92
LEFT, RIGHT, BOTTOM = 70.0, 525.32, 758.0
INK, MUTED, BAR = '#073746', '#56707a', '#677f84'
ACTUAL, TARGET, STOP = '#1f7fb5', '#5a8f1f', '#c0392b'
DEGREES = 180 / np.pi
NOTE = ('Recorded measurements only; no limits are inferred and no verdict is given. A current peak or a '
        'protective stop alone does not show a collision or a fault. Missing channels are unavailable, never zero.')
# metric, panel title, display unit, factor from the recorded unit
PANELS = (('position', 'Position', '°', DEGREES), ('velocity', 'Speed', '°/s', DEGREES),
          ('acceleration', 'Acceleration', '°/s²', DEGREES), ('current', 'Current', 'A', 1.),
          ('torque', 'Torque', 'Nm', 1.))


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
    """A4 frame for all reports: header, wrapped text, tables and 'Page N of M' footers."""
    section = ''

    def __init__(self, pdf, source, subtitle='FLIGHT RECORDING REPORT', note=NOTE):
        self.pdf, self.source = pdf, str(source)
        self.subtitle, self.note = subtitle, note
        self.generated = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        self.figure = None
        self.pages = []

    def lines(self, text, width, size=9.5, weight='normal'):
        font = FontProperties(family='DejaVu Sans', size=size, weight=weight)
        def fits(value):
            # Unhinted advances are what the PDF embeds; hinted raster widths differ by several percent.
            return text_to_path.get_text_width_height_descent(value, font, False)[0] <= width
        result = []
        with RENDER_LOCK:
            for paragraph in str(text).split('\n'):
                current = ''
                for word in paragraph.split():
                    if current and not fits(current + ' ' + word):
                        result.append(current)
                        current = ''
                    # Long filenames and source expressions must not escape their box;
                    # binary search keeps very long source paths inexpensive to wrap.
                    while not fits(word) and len(word) > 1:
                        low, high = 1, len(word)
                        while low < high:
                            middle = (low+high+1)//2
                            if fits(word[:middle]):
                                low = middle
                            else:
                                high = middle-1
                        # Prefer to break a path or URL after a separator near the end of the line.
                        low = max(word.rfind(mark, low//2, low) for mark in '/_-.,=:')+1 or low
                        result.append(word[:low])
                        word = word[low:]
                    current = (current + ' ' + word).strip()
                result.append(current)
        return result

    def text(self, x, y, text, size=9.5, color=INK, weight='normal', **kwargs):
        return self.figure.text(x/PAGE_WIDTH, 1-y/PAGE_HEIGHT, str(text),
                                fontsize=size, color=color, weight=weight,
                                family='DejaVu Sans', va='top', **kwargs)

    def rect(self, x, y, width, height, color, edge='#d1d9db'):
        self.figure.add_artist(Rectangle((x/PAGE_WIDTH, 1-(y+height)/PAGE_HEIGHT),
                                        width/PAGE_WIDTH, height/PAGE_HEIGHT,
                                        transform=self.figure.transFigure,
                                        facecolor=color, edgecolor=edge, linewidth=.7, zorder=-1))

    def rule(self, y):
        self.figure.add_artist(Line2D([LEFT/PAGE_WIDTH, RIGHT/PAGE_WIDTH], [1-y/PAGE_HEIGHT]*2,
                                      transform=self.figure.transFigure, color='#c3ced1', linewidth=.7))

    def new_page(self, title=None):
        self.finish_page()
        continued = bool(title) and title.endswith(' (continued)')
        if title and not continued:
            self.section = title
        self.figure = Figure(figsize=(PAGE_WIDTH/72, PAGE_HEIGHT/72), dpi=72, facecolor='white')
        self.text(LEFT, 38, 'UR MOTION STUDIO', 10, INK, 'bold')
        self.text(LEFT, 52, self.subtitle, 7.5, MUTED)
        if self.source:
            first, *rest = self.lines(self.source, 260, 7.5)
            self.text(RIGHT, 52, first + ('…' if rest else ''), 7.5, MUTED, ha='right')
        self.rule(66)
        self.y = 88
        if title:
            self.paragraph(title, size=9 if continued else 20, color=MUTED if continued else INK)
            self.y += 8 if continued else 3
        self.top = self.y

    def finish_page(self):
        if self.figure is not None:
            self.pages.append(self.figure)
            self.figure = None

    def close(self):
        """Write every page; footers wait until here because they state the page count."""
        self.finish_page()
        note = self.lines(self.note, RIGHT-LEFT, 7)
        for number, self.figure in enumerate(self.pages, 1):
            self.rule(768)
            for index, line in enumerate(note):
                self.text(LEFT, 784+index*9.5, line, 7, MUTED)
            self.text(LEFT, 816, 'Generated ' + self.generated, 7.5, MUTED)
            self.text(RIGHT, 816, f'Page {number} of {len(self.pages)}', 7.5, INK, ha='right')
            with RENDER_LOCK:
                self.pdf.savefig(self.figure, dpi=144)
                self.figure.clear()
        self.pages, self.figure = [], None

    def paragraph(self, text, x=LEFT, width=RIGHT-LEFT, size=9.5, color=INK, weight='normal'):
        leading = size * (1.6 if size >= 13 else 1.45)
        for line in self.lines(text, width, size, weight):
            if self.y + leading > BOTTOM:
                self.new_page(self.section + ' (continued)')
            self.text(x, self.y, line, size, color, weight)
            self.y += leading

    def heading(self, title, size=13):
        if self.y + 90 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        elif self.y > self.top:
            self.y += 10
        self.paragraph(title, size=size, weight='bold')
        self.y += 1

    def bullet(self, text, marker='•', size=9.5, indent=16):
        if self.y + size*1.45 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.text(LEFT+2, self.y, marker, size)
        self.paragraph(text, LEFT+indent, RIGHT-LEFT-indent, size)
        self.y += 4

    def bar(self, title):
        self.rect(LEFT, self.y, RIGHT-LEFT, 20, BAR, BAR)
        self.text(LEFT+8, self.y+5.5, title, 8.5, 'white', 'bold')
        self.y += 20

    def rows(self, title, rows, label_width=110):
        """Two-column table; a value is text or a list of (text, color, weight) paragraphs."""
        if not rows:
            return
        wrapped = []
        for label, value in rows:
            parts = value if isinstance(value, (list, tuple)) else [(value, INK, 'normal')]
            wrapped.append((self.lines(label, label_width-16, 8.5, 'bold'),
                            [(line, color, weight) for text, color, weight in parts
                             for line in self.lines(text, RIGHT-LEFT-label_width-16, 9, weight)]))
        # A title bar is never left alone at the foot of a page.
        if self.y + 32 + min(max(map(len, wrapped[0])), 32)*13 > BOTTOM:
            self.new_page(self.section + ' (continued)')
        self.bar(title)
        for index, (left, right) in enumerate(wrapped):
            count = max(len(left), len(right))
            offset = 0
            while offset < count:
                room = int((BOTTOM-self.y-12) // 13)
                remaining = count-offset
                # Keep ordinary rows together; split oversized evidence safely.
                if room < 1 or (offset == 0 and remaining <= 32 and remaining > room):
                    self.new_page(self.section + ' (continued)')
                    self.bar(title + ' (continued)')
                    room = int((BOTTOM-self.y-12) // 13)
                take = min(room, remaining)
                height = take*13+12
                self.rect(LEFT, self.y, RIGHT-LEFT, height, '#f0f4f5' if index % 2 == 0 else 'white')
                for n, line in enumerate(left[offset:offset+take]):
                    self.text(LEFT+8, self.y+6+n*13, line, 8.5, INK, 'bold')
                for n, (line, color, weight) in enumerate(right[offset:offset+take]):
                    self.text(LEFT+label_width+8, self.y+6+n*13, line, 9, color, weight)
                self.y += height
                offset += take
        self.y += 14


def _number(value, decimals):
    text = f'{value:,.{decimals}f}'
    if float(text.replace(',', '')) == 0:
        text = text.lstrip('-')
    return text.replace('-', '−')


def _vector(text):
    """Shorten a recorded numeric vector for reading; anything else is shown as recorded."""
    try:
        values = [float(part) for part in text.strip().lstrip('p').strip('[]').split(',')]
    except ValueError:
        return text
    return '[' + ', '.join(f'{value:.4g}' for value in values).replace('-', '−') + ']'


def _plural(count, noun):
    return f'{count:,} {noun}' + ('' if count == 1 else 's')


def _torque_basis(report, joint):
    header = report.source_header('torque', joint)
    if header is None:
        return 'target'
    return 'controller current as torque' if header.startswith('Actual current as torque') else ''


def _identity(report):
    """Recording identity rows; anything the file does not state is left out."""
    meta = report.metadata
    kind = meta.get('report_type', '')
    rows = [('Recording', report.name),
            ('Recording type', kind.replace('_', ' ').capitalize() if kind.isupper() else kind),
            ('Recorded', meta.get('recorded_at', '').replace('T', ' ')),
            ('Program', ' · '.join(filter(None, (
                meta.get('program'), meta.get('program_state') and f"state when reported: {meta['program_state']}",
                meta.get('installation') and f"installation: {meta['installation']}")))),
            ('Robot', ' · '.join(filter(None, (
                report.model, meta.get('serial') and f"serial {meta['serial']}",
                meta.get('robot_address') and f"address {meta['robot_address']}")))),
            ('Controller software', meta.get('software_version', '')),
            ('Incident time', meta.get('incident_time') and meta['incident_time'] + ' (controller clock)')]
    length = f'{_plural(report.sample_count, "sample")} over {report.duration:.3f} s'
    if meta.get('sample_rate_hz'):
        length += f" at {meta['sample_rate_hz']} Hz"
    rows += [('Recording length', length),
             ('Time axis', f'Seconds from the first sample (controller uptime {report.timestamps[0]:.3f} s)')]
    payload = [meta['payload_kg'] + ' kg in the report summary'] if meta.get('payload_kg') else []
    mass = report.columns.get('Payload mass [kg]')
    mass = mass[np.isfinite(mass)] if mass is not None else ()
    if len(mass):
        low, high = float(mass.min()), float(mass.max())
        payload.append(f'active setting {low:.2f} kg throughout the recording' if low == high
                       else f'active setting between {low:.2f} and {high:.2f} kg during the recording')
    if meta.get('center_of_gravity'):
        payload.append(f"centre of gravity {_vector(meta['center_of_gravity'])} m")
    rows += [('Payload', '; '.join(payload).capitalize()),
             ('TCP offset', meta.get('tcp_offset') and _vector(meta['tcp_offset']) + ' (x, y, z in m; rotation vector in rad)'),
             ('Notes', meta.get('notes', ''))]
    return [(label, value) for label, value in rows if value]


def _findings(report, statistics):
    """Plain statements of recorded facts; nothing is interpreted as a cause."""
    overview = recording_overview(report)
    stops = overview['protective_stops']
    found = []
    if 'Safety mode' not in report.columns:
        found.append('Safety mode is not recorded, so protective stops cannot be identified from this file.')
    elif not stops:
        found.append('No protective stop is recorded.')
    else:
        spans = []
        for start, end in stops:
            span = (f'from {start:.2f} s to the end of the recording' if end >= report.duration
                    else f'from {start:.2f} s to {end:.2f} s')
            line = script_line_at(report, report.timestamps[int(np.searchsorted(report.time, start))])
            spans.append(span + (f' (recorded script line {line} when it began)' if line else ''))
        found.append(('A protective stop is recorded ' if len(stops) == 1
                      else f'{len(stops)} protective stops are recorded: ') + '; '.join(spans) + '.')
    changes = state_changes(report)
    for field in dict.fromkeys(change.field for change in changes):
        steps = [change for change in changes if change.field == field]
        if len(steps) == 1:
            found.append(f'{field}: {steps[0].label} for the whole recording.')
            continue
        text = ' → '.join(f'{step.label} at ' + ('the start' if not step.elapsed else f'{step.elapsed:.2f} s')
                          for step in steps[:9])
        found.append(f'{field}: {text}' + (f', then {len(steps)-9} further changes.' if len(steps) > 9 else '.'))
    for label, attribute, unit, factor, decimals in (
            ('Highest joint speed', 'peak_velocity', ' °/s', DEGREES, 1),
            ('Highest joint current', 'peak_current', ' A', 1., 2),
            ('Largest tracking error (actual minus target position)', 'peak_tracking_error', '°', DEGREES, 3)):
        peaks = [(row.joint, getattr(row, attribute)) for row in statistics if getattr(row, attribute)]
        if peaks:
            joint, peak = max(peaks, key=lambda item: abs(item[1].value))
            found.append(f'{label}: {joint}, {_number(peak.value*factor, decimals)}{unit} at {peak.elapsed:.2f} s.')
    scaling = overview['min_speed_scaling']
    if scaling:
        found.append('Speed scaling is 100 % for the whole recording.' if scaling.value >= 1 else
                     f'Lowest speed scaling: {scaling.value*100:.0f} % at {scaling.elapsed:.2f} s.')
    tool = []
    if overview['tcp_path_m'] is not None:
        tool.append(f"TCP path length {overview['tcp_path_m']:.2f} m")
    if overview['peak_tcp_speed']:
        tool.append(f"highest TCP speed {overview['peak_tcp_speed'].value:.2f} m/s at {overview['peak_tcp_speed'].elapsed:.2f} s")
    if overview['peak_arm_current']:
        tool.append(f"highest arm current {overview['peak_arm_current'].value:.2f} A at {overview['peak_arm_current'].elapsed:.2f} s")
    if overview['min_main_voltage']:
        tool.append(f"lowest main voltage {overview['min_main_voltage'].value:.2f} V at {overview['min_main_voltage'].elapsed:.2f} s")
    if tool:
        text = '; '.join(tool) + '.'
        found.append(text[0].upper() + text[1:])
    return found


def _summary_page(doc, report, statistics):
    doc.new_page('Flight Recording Report')
    doc.rows('Recording', _identity(report), label_width=118)
    doc.heading('What the recording shows')
    for finding in _findings(report, statistics):
        doc.bullet(finding)
    doc.y += 4
    doc.paragraph('Each statement is read directly from the recorded channels. Peaks are single samples; the joint '
                  'statistics and charts on the following pages give their context.', size=8.5, color=MUTED)


def _cell(value, decimals, factor=1., mark=''):
    """(value, elapsed time) text for a number or a recorded peak; an absent channel stays None."""
    if value is None:
        return None
    if isinstance(value, Peak):
        return _number(value.value*factor, decimals)+mark, f'at {value.elapsed:.2f} s'
    return _number(value*factor, decimals)+mark, ''


def _qualifier(words):
    """State a basis shared by every joint once; otherwise star the joints it applies to."""
    named = list(dict.fromkeys(word for word in words if word))
    if not named:
        return '', ['']*len(words)
    if len(named) == 1 and all(words):
        return ' · ' + named[0], ['']*len(words)
    return ' · * ' + ' or '.join(named), ['*' if word else '' for word in words]


def _statistics_page(doc, report, statistics):
    doc.new_page('Joint statistics')
    doc.paragraph('Complete recording, one column per joint. A peak is the single recorded sample of largest '
                  'magnitude, shown with its sign and the time at which it occurs.', size=9, color=MUTED)
    doc.y += 10
    derived, derived_marks = _qualifier(['derived from speed' if row.acceleration_derived else '' for row in statistics])
    basis, basis_marks = _qualifier([_torque_basis(report, joint) for joint in range(len(JOINTS))])
    table = [
        ('Lowest position', 'degrees', [_cell(row.position_min, 1, DEGREES) for row in statistics]),
        ('Highest position', 'degrees', [_cell(row.position_max, 1, DEGREES) for row in statistics]),
        ('Range of motion', 'degrees', [_cell(row.travel, 1, DEGREES) for row in statistics]),
        ('Peak speed', '°/s', [_cell(row.peak_velocity, 1, DEGREES) for row in statistics]),
        ('Peak acceleration', '°/s²'+derived, [_cell(row.peak_acceleration, 0, DEGREES, mark)
                                               for row, mark in zip(statistics, derived_marks)]),
        ('Peak current', 'A', [_cell(row.peak_current, 2) for row in statistics]),
        ('RMS current', 'A', [_cell(row.rms_current, 2) for row in statistics]),
        ('Peak torque', 'Nm'+basis, [_cell(row.peak_torque, 1, mark=mark) for row, mark in zip(statistics, basis_marks)]),
        ('Highest temperature', '°C', [_cell(row.max_temperature, 1) for row in statistics]),
        ('Lowest voltage', 'V', [_cell(row.min_voltage, 2) for row in statistics]),
        ('Largest tracking error', 'degrees · actual − target position', [_cell(row.peak_tracking_error, 3, DEGREES) for row in statistics]),
    ]
    shown = [row for row in table if any(row[2])]
    label_width = 128
    column = (RIGHT-LEFT-label_width)/len(JOINTS)
    if shown:
        doc.rect(LEFT, doc.y, RIGHT-LEFT, 20, BAR, BAR)
        doc.text(LEFT+8, doc.y+5.5, 'Measurement', 8.5, 'white', 'bold')
        for index, name in enumerate(JOINTS, 1):
            doc.text(LEFT+label_width+index*column-7, doc.y+5.5, name, 8.5, 'white', 'bold', ha='right')
        doc.y += 20
    for index, (label, unit, cells) in enumerate(shown):
        doc.rect(LEFT, doc.y, RIGHT-LEFT, 30, '#f0f4f5' if index % 2 == 0 else 'white')
        doc.text(LEFT+8, doc.y+6, label, 8.5, INK, 'bold')
        doc.text(LEFT+8, doc.y+17.5, unit, 7, MUTED)
        for position, cell in enumerate(cells, 1):
            x = LEFT+label_width+position*column-7
            if cell is None:
                doc.text(x, doc.y+6, '—', 8.5, MUTED, ha='right')
                continue
            doc.text(x, doc.y+6, cell[0], 8.5, INK, ha='right')
            doc.text(x, doc.y+17.5, cell[1], 7, MUTED, ha='right')
        doc.y += 30
    doc.y += 12
    notes = []
    if any(cell is None for row in shown for cell in row[2]):
        notes.append('— means the channel is not recorded for that joint.')
    missing = [row[0] for row in table if row not in shown]
    if missing:
        notes.append(('Not recorded in this file: ' if shown else 'No joint channel is recorded in this file: ')
                     + ', '.join(missing) + '.')
    notes.append('Position, speed and acceleration are converted from the recorded radians to degrees.')
    for note in notes:
        doc.paragraph(note, size=8.5, color=MUTED)
        doc.y += 3


def _traces(report, metric, joint, factor):
    """Every recorded series of one chart panel as (label, colour, line style, values)."""
    traces = []
    for target in (False, True):
        values = report.series(metric, joint, target)
        if not np.isfinite(values).any():
            continue
        label = 'Target'
        if not target:
            basis = ('derived from speed' if metric == 'acceleration' and report.source_header(metric, joint) is None
                     else _torque_basis(report, joint) if metric == 'torque' else '')
            label = f'Actual ({basis})' if basis else 'Actual'
        traces.append((label, TARGET if target else ACTUAL, (0, (4, 2.5)) if target else '-', values*factor))
    if metric == 'current':
        for key, label, style in (('current_upper', 'Recorded upper limit', '-'),
                                  ('current_lower', 'Recorded lower limit', ':')):
            values = report.series(key, joint)
            if np.isfinite(values).any():
                traces.append((label, STOP, style, values))
    return traces


def _chart_page(doc, report, joint, stops):
    """Full-recording traces for one joint; False when the joint has no recorded channel."""
    panels = [(title, unit, _traces(report, metric, joint, factor)) for metric, title, unit, factor in PANELS]
    missing = [title.lower() for title, _unit, traces in panels if not traces]
    panels = [panel for panel in panels if panel[2]]
    if not panels:
        return False
    doc.new_page(f'{JOINTS[joint]} · joint {joint}')
    doc.paragraph(f'Complete recording, {report.duration:.3f} s. Time is in seconds from the first sample '
                  f'(controller uptime {report.timestamps[0]:.3f} s). Solid: actual. Dashed: target.'
                  + (' Shaded: recorded protective stop.' if stops else '')
                  + (f' Not recorded for this joint: {", ".join(missing)}.' if missing else ''),
                  size=8.5, color=MUTED)
    top, gap, inset = doc.y+24, 38, 36
    height = min(125., (BOTTOM-22-top-gap*(len(panels)-1))/len(panels))
    for index, (title, unit, traces) in enumerate(panels):
        chart_top = top+index*(height+gap)
        axes = doc.figure.add_axes([(LEFT+inset)/PAGE_WIDTH, 1-(chart_top+height)/PAGE_HEIGHT,
                                    (RIGHT-LEFT-inset)/PAGE_WIDTH, height/PAGE_HEIGHT])
        handles = []
        for label, color, style, values in traces:
            # One line per recorded segment: gaps and missing samples are never bridged.
            for segment in report.valid_segments(values):
                line, = axes.plot(report.time[segment], values[segment], color=color, linewidth=.9, linestyle=style,
                                  marker='.' if segment.stop-segment.start == 1 else None, label=label)
            handles.append(line)
        for start, end in stops:
            axes.axvspan(start, end, facecolor=to_rgba(STOP, .13), edgecolor=to_rgba(STOP, .55), linewidth=.5)
        if stops and index == 0:
            handles.append(Patch(facecolor=to_rgba(STOP, .13), edgecolor=to_rgba(STOP, .55), linewidth=.5,
                                 label='Protective stop'))
        axes.legend(handles=handles, loc='lower right', bbox_to_anchor=(1, 1), borderaxespad=.25,
                    fontsize=7.5, frameon=False, ncol=len(handles), labelcolor=INK, handlelength=2.4)
        axes.set_title(f'{title} [{unit}]', loc='left', fontsize=9.5, color=INK, fontweight='bold', pad=6)
        axes.tick_params(labelsize=7.5, colors=MUTED, length=2.5)
        axes.ticklabel_format(axis='x', style='plain', useOffset=False)
        axes.grid(True, color='#e3e9ea', linewidth=.5)
        axes.set_axisbelow(True)
        for name, spine in axes.spines.items():
            spine.set_visible(name in ('left', 'bottom'))
            spine.set_color('#b9c5c8')
        if report.sample_count > 1:
            axes.set_xlim(0., report.duration)
    axes.set_xlabel('Elapsed time [s]', fontsize=8, color=INK, labelpad=3)
    return True


def _notes_page(doc, report, statistics, uncharted):
    facts = program_facts(report.script)
    doc.new_page('Program context and notes' if report.script else 'Notes')
    if report.script:
        counts = Counter(call[1] for call in facts.calls)
        blends = sum(bool(re.search(r'\br\s*=', call[2])) for call in facts.named('movej', 'movel', 'movep'))
        def calls(*names):
            found = facts.named(*names)
            if not found:
                return 'None found'
            number, name, arguments = found[0]
            return f'{_plural(len(found), "call statement")}; first at script line {number}: {name}({arguments})'
        rows = [('Script', _plural(facts.line_count, 'line')),
                ('Move statements', f"{counts['movej']} MoveJ, {counts['movel']} MoveL, {counts['movep']} MoveP; "
                                    f'{blends} with a blend radius'),
                ('Payload statements', calls('set_payload', 'set_target_payload')),
                ('TCP statements', calls('set_tcp'))]
        if facts.urcaps:
            rows.append(('URCap sources', '\n'.join(f'{source} (script line {line})' for line, source in facts.urcaps)))
        doc.rows('Program context · counted from the recorded script', rows, label_width=118)
        doc.paragraph('These are counts of statements in the script saved with the recording, including branches and '
                      'helper functions. They do not show which statements ran or whether the payload and TCP match '
                      'the real tool. Use a Program Check report to review the program itself.', size=8.5, color=MUTED)
    doc.heading('How to read this report')
    notes = ['Time is in seconds from the first sample. Controller uptime is not a wall-clock time'
             + ('; the incident time on page 1 is the controller\'s label for the whole recording and is not used '
                'to date individual samples.' if report.metadata.get('incident_time') else '.'),
             'Joint position, speed and acceleration are converted from the recorded radians to degrees. Current (A), '
             'torque (Nm), temperature (°C) and voltage (V) are shown as recorded.']
    derived = report.metadata.get('acceleration_note', '')
    if any(row.acceleration_derived and row.peak_acceleration for row in statistics):
        notes.append((derived if 'derived' in derived else 'Actual acceleration is derived from recorded speed '
                      '(finite differences)') + '. It is a calculated value, not a measurement.')
    bases = {_torque_basis(report, joint) for joint, row in enumerate(statistics) if row.peak_torque}
    if 'target' in bases:
        notes.append('Torque is the controller\'s target joint torque. Actual torque is not recorded and is not '
                     'estimated from current.')
    if 'controller current as torque' in bases:
        notes.append('Actual torque is the controller\'s own conversion of motor current to torque, not a torque sensor reading.')
    notes += ['Missing or invalid samples stay as gaps: lines are not drawn across them and the statistics use '
              'recorded samples only.',
              'No limit is applied and no pass/fail verdict is given. A current peak or a protective stop on its own '
              'does not show a collision or a component fault.']
    if report.metadata.get('model_basis'):
        notes.append(f"Robot model {report.model}: {report.metadata['model_basis'][0].lower()}{report.metadata['model_basis'][1:]}.")
    for note in notes:
        doc.bullet(note, size=9)
    doc.heading('Data quality')
    quality = [warning for warning in report.warnings if warning != derived + '.']
    if uncharted:
        quality.append('No channel is recorded for ' + ', '.join(uncharted) + '; '
                       + ('this joint has' if len(uncharted) == 1 else 'these joints have') + ' no chart page.')
    for warning in quality or ['The file was read without warnings.']:
        doc.bullet(warning, size=9)


def export_flight_pdf(report, path):
    """Write a complete selected-flight PDF atomically; return its destination Path."""
    if report.sample_count == 0:
        raise ValueError('Cannot export a flight with no timestamped samples.')
    destination = Path(path)
    statistics = joint_statistics(report)
    stops = protective_stops(report)
    handle, temporary_name = tempfile.mkstemp(prefix='.flight-report-', suffix='.pdf', dir=destination.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        with PdfPages(temporary, metadata={'Title': f'Flight Recording Report - {report.name}',
                                          'Author': 'UR Motion Studio',
                                          'Subject': 'Recorded joint measurements for the complete flight recording'}) as pdf:
            doc = _Report(pdf, report.name)
            _summary_page(doc, report, statistics)
            _statistics_page(doc, report, statistics)
            uncharted = [name for joint, name in enumerate(JOINTS) if not _chart_page(doc, report, joint, stops)]
            _notes_page(doc, report, statistics, uncharted)
            doc.close()
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return destination
