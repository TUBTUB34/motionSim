"""Flight graph rendering, independent of Tk and report loading."""
import numpy as np
from matplotlib.ticker import MaxNLocator, FuncFormatter
from matplotlib.lines import Line2D
from functools import wraps
from threading import RLock
from ui_theme import (BACKGROUND, PANEL, BORDER as GRID, INK as TEXT,
                      MUTED, ACCENT as ACTUAL, TARGET, DANGER)

# Matplotlib font/rendering state is shared even by independent figures.
# PDF rendering takes this lock per page, never for an entire export.
RENDER_LOCK = RLock()


def serialized_render(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with RENDER_LOCK:
            return function(*args, **kwargs)
    return wrapped

JOINTS = ('Base', 'Shoulder', 'Elbow', 'Wrist 1', 'Wrist 2', 'Wrist 3')
METRICS = ('position', 'velocity', 'acceleration', 'current', 'torque', 'temperature', 'voltage', 'control_mode')
METRIC_LABELS = {metric: metric.replace('_', ' ').title() for metric in METRICS}
UNITS = {'position': 'rad', 'velocity': 'rad/s', 'acceleration': 'rad/s²', 'current': 'A',
         'torque': 'Nm', 'temperature': '°C', 'voltage': 'V', 'control_mode': 'code'}
JOINT_COLORS = ('#53b8f2', '#e8ac58', '#ad91ef', '#66d3b4', '#ef819a', '#d7d676')


def series_label(report, metric, joint, target=False):
    if target:
        return 'Target'
    if metric == 'acceleration' and report.source_header(metric, joint) is None:
        return 'Actual (derived dV/dt)'
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


class FlightCharts:
    """Keep full measured traces static; move only shared timeline artists."""
    def __init__(self, figure):
        self.figure = figure
        self.axes = []
        self.playheads = []
        self.backgrounds = None
        self.report = None
        self.selected_time = 0.
        self._layout_key = None

    @serialized_render
    def draw(self, report, joint=1, focus='All readings', *, compare_joints=None, series='Actual + target', separate_joints=False, shared_scale=False):
        self.report = report
        self.figure.clear()
        self.figure.set_facecolor(BACKGROUND)
        metrics = METRICS if focus == 'All readings' else (focus.lower().replace(' ', '_'),)
        comparing = compare_joints is not None
        joints = tuple(dict.fromkeys(compare_joints)) if comparing else (joint,)
        targets = (False, True) if series == 'Actual + target' or not comparing else (series == 'Target',)
        self.axes, self.playheads = [], []
        self.backgrounds = None
        self._layout_key = None
        separate = comparing and separate_joints and bool(joints)
        panels = [(metric, (selected,)) for metric in metrics for selected in joints] if separate else [(metric, joints) for metric in metrics]
        metric_axes = {}
        for index, (metric, panel_joints) in enumerate(panels):
            ax = self.figure.add_subplot(len(panels), 1, index + 1,
                                         sharex=self.axes[0] if self.axes else None,
                                         sharey=metric_axes.get(metric) if separate and shared_scale else None)
            metric_axes.setdefault(metric, ax)
            self.axes.append(ax)
            ax.set_facecolor(BACKGROUND)
            ax.set_title(f'{METRIC_LABELS[metric]} · ' + (f'{JOINTS[panel_joints[0]]} · {series}' if separate else (f'Compare joints · {series}' if comparing else f'Joint {joint} {JOINTS[joint]}')),
                         color=TEXT, loc='left', fontsize=9, fontweight='bold', pad=7)
            legend_handles = []
            for selected in panel_joints:
                plotted = False
                for target in targets:
                    color = JOINT_COLORS[selected] if comparing else (TARGET if target else ACTUAL)
                    values = report.series(metric, selected, target=target)
                    label = series_label(report, metric, selected, target)
                    if comparing and not separate:
                        label = f'{JOINTS[selected]} · {label}'
                    if np.isfinite(values).any():
                        plotted = True
                        isolated = [segment.start for segment in report.valid_segments(values)
                                    if segment.stop - segment.start == 1]
                        ax.plot(*gap_series(report, values), color=color, linewidth=.95, label=label,
                                linestyle='--' if comparing and target else '-',
                                drawstyle='steps-post' if metric == 'control_mode' else 'default')
                        if isolated:
                            ax.scatter(report.timestamps[isolated], values[isolated], color=color, s=12)
                if comparing and plotted:
                    legend_handles.append(Line2D([], [], color=JOINT_COLORS[selected], marker='o',
                                                 linestyle='None', markersize=4, label=JOINTS[selected]))
            if metric == 'current' and not comparing:
                for key, label, style in [('current_upper', 'Recorded upper limit', '-'),
                                          ('current_lower', 'Recorded lower limit', '--')]:
                    values = report.series(key, joint)
                    if np.isfinite(values).any():
                        ax.plot(*gap_series(report, values), color=DANGER, linestyle=style,
                                linewidth=.8, label=label)
            if not ax.lines:
                ax.text(.5, .5, 'No joints selected' if not joints else 'No recorded measurements for this selection', transform=ax.transAxes,
                        ha='center', color=MUTED)
            else:
                options = dict(handles=legend_handles) if comparing and not separate else {}
                ax.legend(loc='upper right', fontsize=7, labelcolor=TEXT,
                          facecolor=PANEL, edgecolor=GRID, framealpha=.85, ncol=3 if comparing else 2,
                          **options)
            safety = report.columns.get('Safety mode')
            if safety is not None:
                # Protective-stop shading is sourced only from recorded mode 3.
                for segment in report.valid_segments(np.where(safety == 3, safety, np.nan)):
                    ax.axvspan(report.timestamps[segment.start], report.timestamps[segment.stop-1],
                               color=DANGER, alpha=.12, linewidth=0)
            ax.set_ylabel(UNITS[metric], color=MUTED, fontsize=8)
            if metric == 'control_mode':
                ax.yaxis.set_major_locator(MaxNLocator(integer=True))
            ax.tick_params(colors=MUTED, labelsize=7)
            if index < len(panels) - 1:
                ax.tick_params(labelbottom=False)
            ax.grid(color=GRID, linewidth=.65)
            for spine in ax.spines.values():
                spine.set_edgecolor(GRID)
            ax.xaxis.set_major_formatter(FuncFormatter(
                lambda timestamp, _position: str(line) if (line := script_line_at(report, timestamp)) is not None else '—'))
            start, end = float(report.timestamps[0]), float(report.timestamps[-1])
            ax.set_xlim(start, end if end > start else start + .001)
            self.playheads.append(ax.axvline(float(report.timestamps[0]), color=TEXT,
                                            linewidth=.85, linestyle='--', animated=True))
        self.axes[-1].set_xlabel('Script line number · recorded source' +
                                ('\nSolid: actual · Dashed: target' if comparing else ''), color=MUTED, fontsize=8)
        self._layout()
        self.selected_time = float(report.timestamps[0])

    def _layout(self):
        height = max(150., self.figure.bbox.height)
        key = (self.figure.bbox.width, self.figure.bbox.height, len(self.axes))
        if key == self._layout_key:
            return False
        self._layout_key = key
        margin = 64. if self.axes and '\n' in self.axes[-1].get_xlabel() else 50.
        bottom = min(.48, margin / height)
        top = 1 - min(.18, 32. / height)
        compact = height < 400 and len(self.axes) > 1
        for axis in self.axes:
            axis.set_title(axis.get_title(loc='left'), loc='left',
                           fontsize=8 if compact else 9, pad=5 if compact else 7,
                           fontweight='bold')
        self.figure.subplots_adjust(left=.085, right=.985, top=top, bottom=bottom,
                                    hspace=(.7 if compact else .55) if len(self.axes) > 1 else .15)
        return True

    @serialized_render
    def capture_background(self, canvas):
        self.backgrounds = [canvas.copy_from_bbox(ax.bbox) for ax in self.axes]
        self.move(self.selected_time, canvas)

    @serialized_render
    def move(self, timestamp, canvas):
        self.selected_time = timestamp
        for line in self.playheads:
            line.set_xdata([timestamp, timestamp])
        if self.backgrounds is None:
            canvas.draw_idle()
            return
        for ax, line, background in zip(self.axes, self.playheads, self.backgrounds):
            canvas.restore_region(background)
            ax.draw_artist(line)
            canvas.blit(ax.bbox)

    @serialized_render
    def reset_zoom(self, canvas):
        if self.report is not None:
            start, end = self.report.timestamps[0], self.report.timestamps[-1]
            self.axes[0].set_xlim(start, end if end > start else start + .001)
            self.backgrounds = None
            canvas.draw_idle()
