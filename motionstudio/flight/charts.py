"""Flight graph rendering, independent of Tk and report loading."""
import numpy as np
from matplotlib.ticker import MaxNLocator, FuncFormatter
from motionstudio.core.kinematics import JOINTS
from motionstudio.core.rendering import serialized_render
from motionstudio.ui import theme
from motionstudio.ui.charts import StackedPanels, chart_palette, joint_colors, retheme_figure

METRICS = ('position', 'velocity', 'acceleration', 'current', 'torque', 'temperature', 'voltage', 'control_mode')
METRIC_LABELS = {metric: metric.replace('_', ' ').title() for metric in METRICS}
UNITS = {'position': 'rad', 'velocity': 'rad/s', 'acceleration': 'rad/s²', 'current': 'A',
         'torque': 'Nm', 'temperature': '°C', 'voltage': 'V', 'control_mode': 'code'}
def series_label(report, metric, joint, target=False):
    if target:
        return 'Target'
    if metric == 'acceleration' and report.source_header(metric, joint) is None:
        return 'Actual (derived dV/dt)'
    if metric == 'torque' and (report.source_header(metric, joint) or '').startswith('Actual current as torque'):
        return 'Actual (controller current as torque)'
    return 'Actual'


def gap_series(report, values):
    """Insert breaks at lost timestamp intervals without removing measured endpoints."""
    x = report.timestamps
    breaks = np.r_[False, np.diff(x) > report.gap_threshold]
    if report.break_before is not None:
        breaks |= report.break_before
    gaps = np.flatnonzero(breaks)
    if not len(gaps):
        return x, values
    return np.insert(x, gaps, np.nan), np.insert(values, gaps, np.nan)


def script_line_at(report, timestamp):
    """Map chronological coordinates to recorded lines without bridging gaps."""
    values = report.columns.get('Script control line')
    times = report.timestamps
    if values is None or not len(times) or not np.isfinite(timestamp) or timestamp < times[0] or timestamp > times[-1]:
        return None
    index = min(int(np.searchsorted(times, timestamp)), len(times)-1)
    if index and timestamp != times[index]:
        if times[index] - times[index-1] > report.gap_threshold or (
                report.break_before is not None and report.break_before[index]):
            return None
        if timestamp - times[index-1] < times[index] - timestamp:
            index -= 1
    value = values[index]
    return int(value) if np.isfinite(value) and value > 0 and value == int(value) else None


X_REFERENCES = ('Recording timestamp', 'Elapsed time', 'Script line')
DEGREE_UNITS = {'position': '°', 'velocity': '°/s', 'acceleration': '°/s²'}
STOP_LEAD, STOP_TAIL = .10, .05


def metric_unit(metric, degrees=False):
    return DEGREE_UNITS.get(metric, UNITS[metric]) if degrees else UNITS[metric]


def style_axis(axis, title):
    """One chart card: quiet surface, fine border, title above the plot."""
    axis.set_facecolor(theme.FIELD)
    axis.set_title(title, color=theme.INK, loc='left', fontsize=10, fontweight='bold', pad=8)
    axis.tick_params(colors=theme.MUTED, labelsize=8, length=3)
    axis.grid(color=theme.BORDER, linewidth=.6)
    for spine in axis.spines.values():
        spine.set_edgecolor(theme.BORDER)


def state_runs(report, field):
    """(first index, last index, value) for each unbroken run of one recorded state."""
    values = report.columns.get(field)
    if values is None:
        return []
    runs = []
    for segment in report.valid_segments(values):
        block = values[segment]
        edges = np.flatnonzero(np.diff(block)) + 1
        for first, last in zip(np.r_[0, edges], np.r_[edges, len(block)]):
            runs.append((segment.start + int(first), segment.start + int(last) - 1, int(block[first])))
    return runs


class FlightCharts(StackedPanels):
    """Keep full measured traces static; move only shared timeline artists."""
    def __init__(self, figure):
        self.figure = figure
        self.axes = []
        self.playheads = []
        self.markers = []
        self.legend = []
        self.backgrounds = None
        self.report = None
        self.selected_time = 0.
        self._layout_key = None
        self._palette = chart_palette()

    @serialized_render
    def draw(self, report, joint=1, focus='All readings', *, compare_joints=None, series='Actual + target',
             separate_joints=False, shared_scale=False, x_reference='Recording timestamp', state_band=None,
             degrees=False):
        from motionstudio.flight.stats import STATE_FIELDS, protective_stops
        self.report = report
        self._palette = chart_palette()
        self.figure.clear()
        self.figure.set_facecolor(theme.BACKGROUND)
        metrics = METRICS if focus == 'All readings' else (focus.lower().replace(' ', '_'),)
        comparing = compare_joints is not None
        joints = tuple(dict.fromkeys(compare_joints)) if comparing else (joint,)
        targets = (False, True) if series == 'Actual + target' or not comparing else (series == 'Target',)
        self.axes, self.playheads, self.markers, self.legend = [], [], [], []
        self.backgrounds = None
        self._layout_key = None
        separate = comparing and separate_joints and bool(joints)
        panels = [(metric, (selected,)) for metric in metrics for selected in joints] if separate else [(metric, joints) for metric in metrics]
        metric_axes = {}
        legend = {}
        start, end = float(report.timestamps[0]), float(report.timestamps[-1])
        stops = [start + first for first, _last in protective_stops(report)]
        for index, (metric, panel_joints) in enumerate(panels):
            ax = self.figure.add_subplot(len(panels), 1, index + 1,
                                         sharex=self.axes[0] if self.axes else None,
                                         sharey=metric_axes.get(metric) if separate and shared_scale else None)
            metric_axes.setdefault(metric, ax)
            self.axes.append(ax)
            style_axis(ax, f'{METRIC_LABELS[metric]} · ' + (f'{JOINTS[panel_joints[0]]} · {series}' if separate else (f'Compare joints · {series}' if comparing else f'Joint {joint} {JOINTS[joint]}')))
            scale = 180 / np.pi if degrees and metric in DEGREE_UNITS else 1
            unit = metric_unit(metric, degrees)
            for selected in panel_joints:
                for target in targets:
                    color = joint_colors()[selected] if comparing else (theme.TARGET if target else theme.ACTUAL)
                    values = report.series(metric, selected, target=target)
                    label = series_label(report, metric, selected, target)
                    if np.isfinite(values).any():
                        if scale != 1:
                            values = values * scale
                        isolated = [segment.start for segment in report.valid_segments(values)
                                    if segment.stop - segment.start == 1]
                        ax.plot(*gap_series(report, values), color=color, linewidth=1.1, label=label,
                                linestyle='--' if comparing and target else '-',
                                drawstyle='steps-post' if metric == 'control_mode' else 'default')
                        if isolated:
                            ax.scatter(report.timestamps[isolated], values[isolated], color=color, s=12)
                        if comparing:
                            legend.setdefault(JOINTS[selected], (color, False))
                        else:
                            legend.setdefault(f'{label} {METRIC_LABELS[metric]}' if len(metrics) == 1 else label, (color, False))
            if metric == 'current' and not comparing:
                for key, label, style in [('current_upper', 'Current window +', '-'),
                                          ('current_lower', 'Current window −', '--')]:
                    values = report.series(key, joint)
                    if np.isfinite(values).any():
                        ax.plot(*gap_series(report, values), color=theme.DANGER, linestyle=style,
                                linewidth=.9, label=label)
                        legend.setdefault(label, (theme.DANGER, style == '--'))
            if not ax.lines:
                ax.text(.5, .5, 'No joints selected' if not joints else 'No recorded measurements for this selection', transform=ax.transAxes,
                        ha='center', color=theme.MUTED)
            safety = report.columns.get('Safety mode')
            if safety is not None:
                # Protective-stop shading is sourced only from recorded mode 3.
                for segment in report.valid_segments(np.where(safety == 3, safety, np.nan)):
                    ax.axvspan(report.timestamps[segment.start], report.timestamps[segment.stop-1],
                               color=theme.DANGER, alpha=.10, linewidth=0, gid='stop-shade')
            for stop in stops:
                # The box frames the instant the recorded safety mode became a protective stop.
                ax.axvspan(stop - STOP_LEAD, stop + STOP_TAIL, facecolor='none', edgecolor=theme.DANGER,
                           linewidth=1.2, gid='stop-onset')
                if index == 0:
                    ax.annotate('Protective Stop', (stop, 1), xycoords=ax.get_xaxis_transform(),
                                xytext=(0, 3), textcoords='offset points', ha='center', va='bottom',
                                color=theme.DANGER, fontsize=10, fontweight='bold', annotation_clip=True)
            if index == 0 and state_band in STATE_FIELDS:
                self._state_band(ax, report, state_band, STATE_FIELDS[state_band])
            ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _position, unit=unit: f'{value:g} {unit}'))
            if metric == 'control_mode':
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            if x_reference == 'Script line':
                ax.xaxis.set_major_formatter(FuncFormatter(
                    lambda timestamp, _position: str(line) if (line := script_line_at(report, timestamp)) is not None else '—'))
            elif x_reference == 'Elapsed time':
                ax.xaxis.set_major_formatter(FuncFormatter(lambda timestamp, _position: f'{timestamp - start:.6g}'))
            else:
                ax.ticklabel_format(axis='x', style='plain', useOffset=False)
            ax.set_xlim(start, end if end > start else start + .001)
            self.playheads.append(ax.axvline(start, color=theme.INK, linewidth=1., linestyle='--', animated=True))
            self.markers.append(ax.scatter([start], [1], marker='v', color=theme.INK, s=36, gid='playhead',
                                           transform=ax.get_xaxis_transform(), clip_on=False, animated=True))
        if comparing and len(targets) == 2:
            legend['Solid: actual · dashed: target'] = (theme.MUTED, True)
        self.legend = [(label, color, dashed) for label, (color, dashed) in legend.items()]
        self.axes[-1].set_xlabel({'Script line': 'Script line number · recorded source',
                                  'Elapsed time': 'Elapsed time [s]'}.get(x_reference, 'Recording timestamp [s]'),
                                 color=theme.MUTED, fontsize=8)
        self._layout()
        self.selected_time = start

    def _state_band(self, ax, report, field, labels):
        """Label each recorded state along the foot of the first chart."""
        ax.set_ymargin(.12)
        low, high = ax.get_ylim()
        ax.set_ylim(low - .18 * (high - low), high)
        edge = theme.DANGER if field == 'Safety mode' else theme.ACCENT
        for first, last, value in state_runs(report, field)[:200]:
            left, right = float(report.timestamps[first]), float(report.timestamps[last])
            normal = value == {'Safety mode': 1, 'Robot mode': 7, 'Program state': 2}[field]
            patch = ax.axvspan(left, right, ymin=0, ymax=.11, facecolor=theme.PANEL if normal else edge,
                               alpha=1 if normal else .35, edgecolor=edge, linewidth=.8, gid='state-band')
            ax.axvspan(left, right, ymin=0, ymax=.11, facecolor='none', edgecolor=edge, linewidth=.8, gid='state-band')
            text = ax.text((left + right) / 2, .055, labels.get(value, f'Unknown ({value})'),
                           transform=ax.get_xaxis_transform(), ha='center', va='center',
                           fontsize=7.5, color=theme.INK, clip_on=True)
            text.set_clip_path(patch)

    def refresh_theme(self):
        previous = self._palette
        self._palette = retheme_figure(self.figure, previous)
        colors = {color: self._palette[name] for name, color in previous.items()}
        self.legend = [(label, colors.get(color, color), dashed) for label, color, dashed in self.legend]
        self.backgrounds = None

    @serialized_render
    def capture_background(self, canvas):
        # The playhead marker sits just above each plot area.
        self.backgrounds = [canvas.copy_from_bbox(ax.bbox.expanded(1.02, 1.12)) for ax in self.axes]
        self.move(self.selected_time, canvas)

    @serialized_render
    def move(self, timestamp, canvas):
        self.selected_time = timestamp
        for line, marker in zip(self.playheads, self.markers):
            line.set_xdata([timestamp, timestamp])
            marker.set_offsets([[timestamp, 1]])
        if self.backgrounds is None:
            canvas.draw_idle()
            return
        for ax, line, marker, background in zip(self.axes, self.playheads, self.markers, self.backgrounds):
            canvas.restore_region(background)
            ax.draw_artist(line)
            ax.draw_artist(marker)
            canvas.blit(ax.bbox.expanded(1.02, 1.12))

    @serialized_render
    def reset_zoom(self, canvas):
        if self.report is not None:
            start, end = self.report.timestamps[0], self.report.timestamps[-1]
            self.axes[0].set_xlim(start, end if end > start else start + .001)
            self.backgrounds = None
            canvas.draw_idle()
