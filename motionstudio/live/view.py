"""Live monitor: watch the connected robot's telemetry, record it, then save it.

The page reads the simulator connection's RTDE stream; it never opens its own
connection and sends nothing to the robot. Saved recordings are flight-report
ZIPs, so Flight reports can chart, replay and export them.
"""
import math
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog

import numpy as np
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.transforms import Bbox, IdentityTransform

from motionstudio.ui.charts import StackedPanels, joint_colors
from motionstudio.core.kinematics import JOINTS
from motionstudio.core.rendering import serialized_render
from motionstudio.core.paths import default_library
from motionstudio.ui.chart_canvas import ChartCanvas
from motionstudio.live.rtde_client import DATA_TIMEOUT, STALE_AFTER
from motionstudio.live.session import reconnect_reason
from motionstudio.core.telemetry import ROBOT_MODES, RUNTIME_STATES, SAFETY_MODES, enum_text
from motionstudio.ui import dialogs as messagebox
from motionstudio.ui import theme
from motionstudio.ui.theme import flow

# metric -> (title, actual header, target header or None, unit, is an angle)
SIGNALS = {
    'position': ('Position', 'Actual position j{j} [rad]', 'Target position j{j} [rad]', 'rad', True),
    'velocity': ('Velocity', 'Actual velocity j{j} [rad/s]', 'Target velocity j{j} [rad/s]', 'rad/s', True),
    'current': ('Current', 'Actual current j{j} [A]', 'Target current j{j} [A]', 'A', False),
    'torque': ('Torque', 'Actual current as torque j{j} [Nm]', 'Target moment j{j} [Nm]', 'Nm', False),
    'temperature': ('Temperature', 'Temperature j{j} [C]', None, '°C', False),
}
# TCP metric -> (title, unit, magnitude symbol, note); one panel each, never per joint.
TCP_SIGNALS = {
    'tcp_speed': ('TCP speed', 'm/s', '|v|', ''),
    'tcp_acceleration': ('TCP acceleration', 'm/s²', '|a|', 'derived'),
}
# actual_TCP_speed is optional in RTDE; older controllers leave these columns NaN.
TCP_VELOCITY = tuple(f'Actual TCP velocity {axis} [m/s]' for axis in 'xyz')
# The same stream's rotational part; the TCP panels show one or the other.
TCP_ROTATION = tuple(f'Actual TCP velocity {axis} [rad/s]' for axis in ('rx', 'ry', 'rz'))
ROTATION_SIGNALS = {
    'tcp_speed': ('TCP rotation speed', 'rad/s', '|ω|', ''),
    'tcp_acceleration': ('TCP angular acceleration', 'rad/s²', '|α|', 'derived'),
}
WINDOWS = {'10 s': 10., '30 s': 30., '60 s': 60., '120 s': 120.}
# Chart tabs, as in Flight reports; each shows a fixed set of panels.
VIEWS = {'All readings': (*SIGNALS, *TCP_SIGNALS), 'Position': ('position',), 'Velocity': ('velocity',),
         'Current': ('current',), 'Torque': ('torque',), 'Temperature': ('temperature',),
         'TCP': tuple(TCP_SIGNALS)}
# Centred averaging window for derived TCP acceleration: long enough to quiet the
# differentiated 125 Hz velocity, short next to how long a robot move accelerates.
AVERAGE = .08
PROTECTIVE_STOP = 3
CONNECT_HINT = 'Connect a robot to watch its telemetry. Recording starts only when you choose Record.'
STREAM_HINT = ('Recording starts only when you choose Record. Saved recordings open in Flight reports. '
               'Red bands mark protective stops.')
PANEL_HEIGHT = 160  # pixels per chart before the chart area starts to scroll


def envelope(x, y, bins=900):
    """Min/max per pixel-sized bin, so short peaks survive a long window."""
    if len(x) <= 2 * bins:
        return x, y
    step = len(x) // bins
    count = step * bins
    values = y[-count:].reshape(bins, step)
    centers = x[-count:].reshape(bins, step).mean(axis=1)
    pairs = np.column_stack((np.fmin.reduce(values, axis=1), np.fmax.reduce(values, axis=1)))
    return np.repeat(centers, 2), pairs.ravel()


def tcp_acceleration(times, velocity, average=AVERAGE):
    """TCP acceleration [m/s²] from TCP velocity rows (n, 3) against controller timestamps.

    Differentiation never crosses a missing sample, a non-increasing timestamp or a
    dropout longer than five stream periods, so gaps stay blank instead of spiking.
    Within each run a central difference (np.gradient) is averaged over a centred
    ``average``-second window; the window is set in time, so 125 Hz and 500 Hz
    streams are smoothed alike, and it shrinks at run edges rather than padding.
    """
    times = np.asarray(times, float)
    if not len(times):
        return np.empty((0, 3))
    velocity = np.asarray(velocity, float).reshape(len(times), -1)
    result = np.full(velocity.shape, np.nan)
    valid = np.isfinite(times) & np.isfinite(velocity).all(axis=1)
    steps = np.diff(times)
    good = steps[valid[1:] & valid[:-1] & (steps > 0)]
    if not len(good):
        return result
    period = float(np.median(good))
    breaks = np.flatnonzero(~(steps > 0) | (steps > 5 * period) | ~valid[1:] | ~valid[:-1]) + 1
    half = int(round(average / period / 2))
    for first, last in zip(np.r_[0, breaks], np.r_[breaks, len(times)]):
        if last - first < 3 or not valid[first]:
            continue
        rates = np.gradient(velocity[first:last], times[first:last], axis=0)
        if half:
            total = np.vstack((np.zeros((1, rates.shape[1])), np.cumsum(rates, axis=0)))
            index = np.arange(len(rates))
            low, high = np.maximum(index - half, 0), np.minimum(index + half + 1, len(rates))
            rates = (total[high] - total[low]) / (high - low)[:, None]
        result[first:last] = rates
    return result


def tcp_series(metric, times, velocity):
    """x, y, z and magnitude columns for a TCP panel."""
    vectors = velocity if metric == 'tcp_speed' else tcp_acceleration(times, velocity)
    return np.column_stack((vectors, np.linalg.norm(vectors, axis=1)))


def tcp_colors():
    return (*theme.CHART_SERIES[:3], theme.INK)


def settle_limits(axis, low, high):
    """Grow the y range at once; shrink it only when the data uses under half of it. True when it changed.

    Rescaling on every refresh makes live lines jump even when nothing changed.
    """
    span = high - low
    pad = span * .08 if span > 0 else max(abs(high) * .05, 1e-3)
    current = getattr(axis, '_live_limits', None)
    target = (low - pad, high + pad)
    if current is not None and (target == current or (
            current[0] <= low and current[1] >= high and span > .5 * (current[1] - current[0]))):
        return False
    axis._live_limits = target
    axis.set_ylim(*target)
    return True


# Decimals per unit, matching the joint table and the tiles.
DECIMALS = {'°': 2, '°/s': 1, '°/s²': 1, 'rad': 4, 'rad/s': 3, 'rad/s²': 2, 'A': 2, 'Nm': 1, '°C': 1,
            'm/s': 3, 'm/s²': 2}


def latest_text(value, unit):
    return f'{value:.{DECIMALS.get(unit, 2)}f} {unit}' if np.isfinite(value) else ''


def stop_spans(x, stopped):
    """(left, right) x for each run of protective-stop samples."""
    edges = np.diff(np.r_[0, stopped.astype(np.int8), 0])
    return [(x[first], x[min(last, len(x) - 1)])
            for first, last in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1))]


def clock_text(seconds):
    return f'{int(seconds // 60):02d}:{seconds % 60:04.1f}'


class LiveCanvas(ChartCanvas):
    """Times every full render so the page can back off when drawing is slow."""
    draw_seconds = 0.
    drawn_at = 0.

    def draw(self):
        start = time.perf_counter()
        try:
            return super().draw()
        finally:
            self.draw_seconds = time.perf_counter() - start
            self.drawn_at = time.monotonic()


class LiveCharts(StackedPanels):
    """Persistent axes and lines; each refresh only replaces their data.

    Panels are (metric, joints): Combined overlays the selected joints, Separate
    joints gives each joint its own panel, and TCP panels are never per joint.

    Each panel owns a full-width strip of the figure (its slot). A full draw paints
    only the static parts (axes, ticks, titles); the lines, stop shading and latest
    value are animated artists. After any full draw the static image of every drawn
    slot is kept, so ``paint`` restores a slot, draws its animated artists and copies
    just that strip to the screen. A panel is re-rendered on its own when its scale
    or note changes, or when it first scrolls into view; scrolling draws nothing.
    """
    def __init__(self, figure):
        self.figure = figure
        self.panels = []
        self.axes = []
        self.lines = []
        self.stops = []
        self.notes = []
        self.readouts = []
        self.values = {}
        self.headers = ()
        self.tcp_headers = TCP_VELOCITY
        self.band = (0., 1.)
        self._stopped = False
        self._layout_key = None
        self._dynamic = {}
        self._backgrounds = {}
        self._dirty = set()
        self._drawn_size = None
        self._window = None
        self._hooked = None
        # update() counts data versions; each panel remembers the version it shows.
        self._version = 0
        self._shown = {}

    @serialized_render
    def configure(self, metrics, joints, show_target, degrees, separate=False, shared_scale=False, components=False,
                  rotation=False):
        """``components`` adds x, y and z to the TCP panels; the magnitude is always drawn.

        ``rotation`` charts the TCP's rotational speed (°/s, or rad/s without ``degrees``)
        and its derived angular acceleration instead of the linear ones.
        """
        self.tcp_headers = TCP_ROTATION if rotation else TCP_VELOCITY
        self.figure.clear()
        self.figure.set_facecolor(theme.BACKGROUND)
        self.axes, self.lines, self.stops, self.notes = [], [], [], []
        self._dynamic, self._backgrounds, self._dirty, self._shown = {}, {}, set(), {}
        self._drawn_size = self._window = None
        # (axis, line key, scale, unit): the latest value shown at the panel's top right.
        self.readouts = []
        self.values = {}
        # Each axis's y-scale group: itself, or the first panel of its metric when scales are shared.
        self.scale_root = {}
        self._stopped, self._layout_key = False, None
        separate = separate and bool(joints)
        self.panels = [(metric, None) if metric in TCP_SIGNALS else (metric, tuple(joints))
                       for metric in metrics if not (separate and metric in SIGNALS)]
        if separate:
            order = list(metrics)
            self.panels += [(metric, (joint,)) for metric in metrics if metric in SIGNALS for joint in joints]
            self.panels.sort(key=lambda panel: order.index(panel[0]))
        headers = ['Safety mode']
        colors = joint_colors()
        shared = {}
        for row, (metric, panel_joints) in enumerate(self.panels):
            axis = self.figure.add_subplot(len(self.panels), 1, row + 1, sharex=self.axes[0] if self.axes else None,
                                           sharey=shared.get(metric) if separate and shared_scale else None)
            shared.setdefault(metric, axis)
            self.scale_root[axis] = shared[metric] if separate and shared_scale else axis
            self.axes.append(axis)
            axis.set_facecolor(theme.BACKGROUND)
            if panel_joints is None:
                title, unit, magnitude, note = (ROTATION_SIGNALS if rotation else TCP_SIGNALS)[metric]
                scale = 1.
                if rotation and degrees:
                    unit, scale = unit.replace('rad', '°'), 180 / np.pi
                axis.set_title(' · '.join(filter(None, (title, unit, note))), color=theme.INK, loc='left',
                               fontsize=9, fontweight='bold', pad=6)
                headers.extend(self.tcp_headers)
                # The magnitude leads; components are thin and drawn underneath it.
                names = ('rx', 'ry', 'rz') if rotation else ('x', 'y', 'z')
                for column, (name, color) in enumerate(zip((*names, magnitude), tcp_colors())):
                    if column < 3 and not components:
                        continue
                    self.lines.append(((metric, column), axis, scale, axis.plot(
                        [], [], color=color, label=name, zorder=3 if column == 3 else 2,
                        linewidth=1.8 if column == 3 else .9, alpha=1. if column == 3 else .85)[0]))
                self.readouts.append((axis, (metric, 3), scale, unit))
                self.values[axis] = axis.set_title('', loc='right', color=theme.MUTED, fontsize=9, pad=6)
                if components:
                    # On the title line, between the name and the latest value, never over the data.
                    axis.legend(loc='lower center', bbox_to_anchor=(.5, 1.), ncol=4, frameon=False, fontsize=8,
                                labelcolor=theme.MUTED, handlelength=1.4, borderaxespad=0., borderpad=.1)
            else:
                title, actual, target, unit, angle = SIGNALS[metric]
                if angle and degrees:
                    unit = unit.replace('rad', '°')
                name = f' · {JOINTS[panel_joints[0]]}' if separate else ''
                axis.set_title(f'{title}{name} · {unit}', color=theme.INK, loc='left', fontsize=9, fontweight='bold', pad=6)
                if separate:
                    self.readouts.append((axis, actual.format(j=panel_joints[0]),
                                          180 / np.pi if angle and degrees else 1., unit))
                    self.values[axis] = axis.set_title('', loc='right', color=theme.MUTED, fontsize=9, pad=6)
                for joint in panel_joints:
                    for dashed, template in ((False, actual), (True, target if show_target else None)):
                        if template is None:
                            continue
                        header = template.format(j=joint)
                        headers.append(header)
                        self.lines.append((header, axis, 180 / np.pi if angle and degrees else 1., axis.plot(
                            [], [], color=colors[joint], linewidth=1.1 if not dashed else .9,
                            linestyle='--' if dashed else '-')[0]))
            # One persistent shading collection per panel; update() only moves its vertices.
            self.stops.append(axis.add_collection(PolyCollection(
                [], transform=axis.get_xaxis_transform(), facecolor=theme.DANGER, alpha=.14, linewidth=0), autolim=False))
            self.notes.append(axis.text(.5, .5, 'No joints selected' if panel_joints == () else
                                        'Not streamed by this controller', transform=axis.transAxes,
                                        ha='center', va='center', color=theme.MUTED, fontsize=9,
                                        visible=panel_joints == ()))
            axis.tick_params(colors=theme.MUTED, labelsize=8)
            axis.grid(color=theme.BORDER, linewidth=.6)
            for spine in axis.spines.values():
                spine.set_edgecolor(theme.BORDER)
            dynamic = [line for _key, owner, _scale, line in self.lines if owner is axis]
            dynamic += [self.stops[-1]] + ([self.values[axis]] if axis in self.values else [])
            for artist in dynamic:
                artist.set_animated(True)
            self._dynamic[axis] = sorted(dynamic, key=lambda artist: artist.get_zorder())
        self._hook()
        if not self.axes:
            self.figure.text(.5, .5, 'Tick at least one signal to chart.', color=theme.MUTED, ha='center')
        else:
            targets = show_target and any(metric in SIGNALS and SIGNALS[metric][2] for metric, _joints in self.panels)
            self.axes[-1].set_xlabel('Seconds before now' + (' · solid: actual · dashed: target' if targets else ''),
                                     color=theme.MUTED, fontsize=8)
            self._layout()
        self.headers = tuple(dict.fromkeys(headers))

    def _layout(self):
        """Called by the canvas just before each render, when positions are final."""
        changed = super()._layout()
        for axis, visible in zip(self.axes, self.visible()):
            axis.set_visible(visible)
        return changed

    def visible(self):
        """Which panels overlap the viewport band (fractions from the top), with a panel of margin."""
        top, bottom = self.band
        margin = PANEL_HEIGHT / max(self.figure.bbox.height, 1.)
        low, high = 1 - bottom - margin, 1 - top + margin
        return [axis.get_position().y1 >= low and axis.get_position().y0 <= high for axis in self.axes]

    def view(self, top, bottom):
        """Set the scrolled band; True when a panel comes into or leaves view and needs a render."""
        self.band = (top, bottom)
        return any(visible != axis.get_visible() for axis, visible in zip(self.axes, self.visible()))

    @serialized_render
    def update(self, window, data):
        times = data['Timestamp [s]']
        if not len(times) or not self.axes:
            return
        self._version += 1
        x = times - times[-1]
        tcp = {}
        filled = set()
        for key, axis, scale, line in self.lines:
            if isinstance(key, str):
                values = data[key]
            else:
                metric, column = key
                if metric not in tcp:
                    tcp[metric] = tcp_series(metric, times, np.column_stack([data[header] for header in self.tcp_headers]))
                values = tcp[metric][:, column]
            if not np.isfinite(values).any():
                line.set_data([], [])
                continue
            filled.add(axis)
            line.set_data(*envelope(x, values * scale if scale != 1 else values))
        for axis, note in zip(self.axes, self.notes):
            if note.get_visible() != (axis not in filled):
                note.set_visible(axis not in filled)
                self._dirty.add(axis)
        latest = {}
        for key, _axis, scale, line in self.lines:
            latest[key] = (tcp[key[0]][:, key[1]] if not isinstance(key, str) else data[key]), scale
        for axis, key, scale, unit in self.readouts:
            values, _scale = latest.get(key, (np.empty(0), 1.))
            if key == ('tcp_acceleration', 3):
                # The centred average is complete only half a window back from the newest sample.
                values = values[times <= times[-1] - AVERAGE / 2]
            values = values[np.isfinite(values)]
            self.values[axis].set_text(latest_text(values[-1] * scale, unit) if len(values) else '')
        stopped = data['Safety mode'] == PROTECTIVE_STOP
        if stopped.any() or self._stopped:
            # Shading uses only the streamed safety mode, as in Flight reports.
            verts = [[(left, 0), (left, 1), (right, 1), (right, 0)] for left, right in stop_spans(x, stopped)]
            for shading in self.stops:
                shading.set_verts(verts)
            self._stopped = bool(verts)
        # Shared y scales need every panel's data range before any of them is set.
        ranges = {}
        for _key, axis, _scale, line in self.lines:
            values = np.asarray(line.get_ydata(), float)
            values = values[np.isfinite(values)]
            if len(values):
                root = self.scale_root[axis]
                low, high = ranges.get(root, (np.inf, -np.inf))
                ranges[root] = min(low, values.min()), max(high, values.max())
        for root, (low, high) in ranges.items():
            if settle_limits(root, low, high):
                self._dirty.update(axis for axis in self.axes if self.scale_root[axis] is root)
        if window != self._window:
            self._window = window
            self.axes[0].set_xlim(-window, 0)
            self._drawn_size = None  # every panel's ticks change: the next paint is a full draw

    # ------------------------------------------------------------ repainting
    def _hook(self):
        canvas = self.figure.canvas
        if canvas is not None and self._hooked is not canvas:
            canvas.mpl_connect('draw_event', self._drawn)
            self._hooked = canvas

    def _slot(self, axis):
        """The panel's strip in display pixels: full width, halfway into the gaps to its neighbours."""
        index = self.axes.index(axis)
        height, width = self.figure.bbox.height, self.figure.bbox.width
        box = axis.get_position()
        top = height if index == 0 else round((box.y1 + self.axes[index - 1].get_position().y0) / 2 * height)
        bottom = 0 if index == len(self.axes) - 1 else round((box.y0 + self.axes[index + 1].get_position().y1) / 2 * height)
        return Bbox.from_extents(0, bottom, width, top)

    def _draw_dynamic(self, axis):
        for artist in self._dynamic[axis]:
            if artist.get_visible():
                axis.draw_artist(artist)

    def _drawn(self, _event):
        """After a full draw: keep each drawn panel's static image, then add its live artists."""
        canvas = self.figure.canvas
        self._backgrounds = {}
        self._drawn_size = tuple(self.figure.bbox.size)
        for axis in self.axes:
            if axis.get_visible():
                self._backgrounds[axis] = canvas.copy_from_bbox(self._slot(axis))
                self._draw_dynamic(axis)
                self._dirty.discard(axis)
                self._shown[axis] = self._version

    def _render_panel(self, axis):
        """Draw one panel's strip from scratch without touching the rest of the figure."""
        canvas = self.figure.canvas
        renderer = canvas.get_renderer()
        slot = self._slot(axis)
        Rectangle((slot.x0, slot.y0), slot.width, slot.height, transform=IdentityTransform(), linewidth=0,
                  facecolor=self.figure.get_facecolor(), edgecolor='none', antialiased=False).draw(renderer)
        axis.set_visible(True)
        axis.draw(renderer)
        self._backgrounds[axis] = canvas.copy_from_bbox(slot)
        self._dirty.discard(axis)
        self._draw_dynamic(axis)
        self._shown[axis] = self._version
        canvas.blit(slot)

    def on_screen(self):
        """Panels whose strip overlaps the scrolled viewport band."""
        top, bottom = self.band
        height = max(self.figure.bbox.height, 1.)
        low, high = (1 - bottom) * height, (1 - top) * height
        return [axis for axis in self.axes if (slot := self._slot(axis)).y1 > low and slot.y0 < high]

    @serialized_render
    def paint(self, renders=1, ahead=True, updates=None):
        """Show the latest data on on-screen panels; returns how many panels still wait for a render.

        Panels with a kept static image are updated in place. At most ``renders``
        panels are rendered from scratch per call, so a page can yield to input
        between them. With ``ahead``, spare renders go to off-screen panels nearest
        the viewport, so later scrolling finds them ready. ``updates`` likewise limits
        in-place updates per call (None: all on-screen panels).
        """
        canvas = self.figure.canvas
        if not self.axes or canvas is None:
            return 0
        self._hook()
        if self._drawn_size != tuple(self.figure.bbox.size):
            canvas.draw()
            return 0
        waiting = 0
        for axis in self.on_screen():
            if axis in self._backgrounds and axis not in self._dirty:
                if self._shown.get(axis) == self._version:
                    continue  # already showing this data, e.g. after a scroll
                if updates is not None:
                    if updates <= 0:
                        waiting += 1
                        continue
                    updates -= 1
                canvas.restore_region(self._backgrounds[axis])
                self._draw_dynamic(axis)
                self._shown[axis] = self._version
                canvas.blit(self._slot(axis))
            elif renders > 0:
                self._render_panel(axis)
                renders -= 1
            else:
                waiting += 1
        if not ahead:
            return waiting
        on_screen = set(self.on_screen())
        middle = sum(self.band) / 2 * self.figure.bbox.height
        later = sorted((axis for axis in self.axes if axis not in on_screen
                        and (axis not in self._backgrounds or axis in self._dirty)),
                       key=lambda axis: abs(self.figure.bbox.height - (self._slot(axis).y0 + self._slot(axis).y1) / 2 - middle))
        for axis in later:
            if renders > 0:
                self._render_panel(axis)
                renders -= 1
            else:
                waiting += 1
        return waiting


class LivePage(ttk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.capture = app.live_capture
        self.view = tk.StringVar(value='All readings')
        self.joints = [tk.BooleanVar(value=True) for _ in JOINTS]
        self.show_target = tk.BooleanVar(value=True)
        self.degrees = tk.BooleanVar(value=True)
        self.components = tk.BooleanVar(value=False)
        self.tcp_motion = tk.StringVar(value='linear')
        self.window = tk.StringVar(value='30 s')
        self.graphs = tk.StringVar(value='Combined')
        self.shared_scale = tk.BooleanVar(value=False)
        self.frozen = False
        self.notes = tk.StringVar()
        self.state = tk.StringVar()
        self.record_text = tk.StringVar(value='Not recording')
        self.status = tk.StringVar(value=CONNECT_HINT)
        self.saved_path = None
        self.saving = False
        self.results = queue.Queue()
        self.viewer = None
        self.viewer_identity = None
        self._last_sample = None
        self._last_draw = self._last_text = self._viewer_at = self._scrolled_at = 0.
        # Seconds the last chart paint took; refreshes back off when painting is slow.
        self.paint_seconds = 0.
        self._paint_id = None
        self._painted_at = 0.
        self._viewer_joints = None
        self._record_state = None
        self._build()
        self.rebuild_charts()
        theme.register_theme_callback(self, self.rebuild_charts)
        self.refresh(force=True)

    # ---------------------------------------------------------------- layout
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        heading = ttk.Frame(self, style='Toolbar.TFrame', padding=(20, 14))
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='Live monitor', style='Title.Panel.TLabel').pack(side='left')
        self.state_label = ttk.Label(heading, textvariable=self.state, style='Panel.TLabel')
        self.state_label.pack(side='left', padx=(16, 0))
        ttk.Button(heading, text='How to', command=lambda: self.app.open_help('Live monitor')).pack(side='right')

        # Recording: one action, its state, a note; Save, Discard and Open appear only when they apply.
        bar = ttk.Frame(self, padding=(20, 12, 20, 0))
        bar.grid(row=1, column=0, sticky='ew')
        bar.columnconfigure(3, weight=1)
        self.record_button = ttk.Button(bar, text='● Record', style='Accent.TButton', command=self.toggle_recording, width=11)
        self.record_button.grid(row=0, column=0)
        self.record_label = ttk.Label(bar, textvariable=self.record_text, width=40)
        self.record_label.grid(row=0, column=1, padx=(12, 12), sticky='w')
        ttk.Label(bar, text='Note', style='Muted.TLabel').grid(row=0, column=2, padx=(0, 8))
        self.notes_entry = ttk.Entry(bar, textvariable=self.notes)
        self.notes_entry.grid(row=0, column=3, sticky='ew')
        self.save_button = ttk.Button(bar, text='Save recording…', command=self.save_recording, state='disabled')
        self.save_button.grid(row=0, column=4, padx=(8, 0))
        self.discard_button = ttk.Button(bar, text='Discard', command=self.discard_recording, state='disabled')
        self.discard_button.grid(row=0, column=5, padx=(8, 0))
        self.open_button = ttk.Button(bar, text='Open in Flight reports', command=self.open_saved, state='disabled')
        self.open_button.grid(row=0, column=6, padx=(8, 0))
        for button in (self.save_button, self.discard_button, self.open_button):
            button.grid_remove()

        body = ttk.Frame(self, padding=(20, 12, 20, 0))
        body.grid(row=2, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)

        # Row 1: what to chart (tabs, as in Flight reports) and the time controls.
        top = tk.Frame(body, background=theme.BACKGROUND)
        top.grid(row=0, column=0, sticky='ew')
        tabs = tk.Frame(top, background=theme.BACKGROUND)
        tabs.pack(side='left')
        self.view_tabs = {}
        for name in VIEWS:
            self.view_tabs[name] = ttk.Button(tabs, text=name, style='Tab.TButton',
                                              command=lambda value=name: self.select_view(value))
            self.view_tabs[name].pack(side='left', padx=(0, 4))
        time_controls = tk.Frame(top, background=theme.BACKGROUND)
        time_controls.pack(side='right')
        self.window_buttons = {}
        for name in WINDOWS:
            self.window_buttons[name] = ttk.Button(time_controls, text=name, style='Tab.TButton', width=5,
                                                   command=lambda value=name: self.select_window(value))
            self.window_buttons[name].pack(side='left', padx=(0, 4))
        self.freeze_button = ttk.Button(time_controls, text='Freeze', command=self.toggle_freeze, width=8)
        self.freeze_button.pack(side='left', padx=(8, 0))

        # Row 2: how to draw it. Each joint's tick box carries its line colour, so it is also the legend.
        options = tk.Frame(body, background=theme.BACKGROUND)
        options.grid(row=1, column=0, sticky='ew', pady=(12, 8))

        def group(caption=None):
            holder = tk.Frame(options, background=theme.BACKGROUND)
            if caption:
                ttk.Label(holder, text=caption, style='Muted.TLabel').pack(side='left', padx=(0, 8))
            return holder

        self.joint_group = group()
        for index, (name, variable) in enumerate(zip(JOINTS, self.joints)):
            ttk.Checkbutton(self.joint_group, text=name, variable=variable, style=f'Joint{index}.TCheckbutton',
                            command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        self.layout_group = group()
        self.layout_buttons = {}
        for name, caption in (('Combined', 'Combined'), ('Separate joints', 'Per joint')):
            self.layout_buttons[name] = ttk.Button(self.layout_group, text=caption, style='Tab.TButton',
                                                   command=lambda value=name: self.select_layout(value))
            self.layout_buttons[name].pack(side='left', padx=(0, 4))
        self.scale_check = ttk.Checkbutton(self.layout_group, text='Same scale', variable=self.shared_scale,
                                           command=self.rebuild_charts)
        self.scale_check.pack(side='left', padx=(8, 0))
        # One group, so the row fits; the parts that do not apply to a tab are left out.
        series = group()
        self.series_group = tk.Frame(series, background=theme.BACKGROUND)
        ttk.Checkbutton(self.series_group, text='Targets', variable=self.show_target,
                        command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(self.series_group, text='Degrees', variable=self.degrees,
                        command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        self.tcp_group = tk.Frame(series, background=theme.BACKGROUND)
        ttk.Label(self.tcp_group, text='TCP', style='Muted.TLabel').pack(side='left', padx=(0, 6))
        self.tcp_buttons = {}
        for value in ('linear', 'rotation'):
            self.tcp_buttons[value] = ttk.Button(self.tcp_group, style='Tab.TButton',
                                                 command=lambda value=value: self.select_tcp_motion(value))
            self.tcp_buttons[value].pack(side='left', padx=(0, 4))
        ttk.Checkbutton(self.tcp_group, text='x, y, z', variable=self.components,
                        command=self.rebuild_charts).pack(side='left', padx=(6, 0))
        self._arrange_options = flow(options, gap=16)

        chart_frame = tk.Frame(body, background=theme.BACKGROUND, highlightbackground=theme.BORDER, highlightthickness=1)
        chart_frame.grid(row=2, column=0, sticky='nsew')
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=theme.BACKGROUND)
        self.charts = LiveCharts(self.figure)
        # Many panels scroll instead of shrinking. The chart canvas scrolls itself: Tk
        # shows the scrolled part of the image, so scrolling renders nothing.
        self.canvas = LiveCanvas(self.figure, master=chart_frame)
        self.canvas.charts = self.charts
        self.canvas.content_height = self._content_height
        self.canvas.visible_region = self._viewport_region
        plot = self.chart_viewport = self.canvas.get_tk_widget()
        plot.configure(height=180, yscrollincrement=1)
        plot.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(chart_frame, orient='vertical', command=self.scroll_chart)
        scroll.grid(row=0, column=1, sticky='ns')
        plot.configure(yscrollcommand=scroll.set)
        plot.bind('<Configure>', lambda _event: self._show_band(), add='+')
        plot.bind('<MouseWheel>', self.wheel_chart)
        plot.bind('<Button-4>', lambda _event: self.scroll_chart('scroll', -48, 'units'))
        plot.bind('<Button-5>', lambda _event: self.scroll_chart('scroll', 48, 'units'))

        side = ttk.Frame(body, width=360)
        side.grid(row=0, column=1, rowspan=3, sticky='ns', padx=(16, 0))
        side.grid_propagate(False)
        side.columnconfigure((0, 1), weight=1, uniform='tiles')
        side.rowconfigure(0, weight=1)
        self.viewer_host = tk.Frame(side, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        self.viewer_host.grid(row=0, column=0, columnspan=2, sticky='nsew')
        self.viewer_notice = ttk.Label(self.viewer_host, text='No live robot connection', style='Muted.Panel.TLabel',
                                       anchor='center', justify='center', wraplength=300)
        self.viewer_notice.pack(fill='both', expand=True)
        self.tiles = {}
        for index, (key, caption) in enumerate((('robot', 'ROBOT'), ('safety', 'SAFETY'),
                                                ('program', 'PROGRAM'), ('speed', 'SPEED SCALING'),
                                                ('tcp_speed', 'TCP SPEED'), ('tcp_acceleration', 'TCP ACCELERATION'))):
            tile = ttk.Frame(side, style='Card.TFrame', padding=(12, 8))
            tile.grid(row=1 + index // 2, column=index % 2, sticky='ew', pady=(8, 0), padx=(0, 4) if index % 2 == 0 else (4, 0))
            ttk.Label(tile, text=caption, style='Section.Panel.TLabel').pack(anchor='w')
            value = self.tiles[key] = ttk.Label(tile, text='—', style='Panel.TLabel', font=theme.font(11, 'bold'),
                                                wraplength=150)
            value.pack(anchor='w', pady=(4, 0))
        columns = ('position', 'speed', 'current', 'temperature')
        self.table = ttk.Treeview(side, columns=columns, show='tree headings', height=6, selectmode='none')
        self.table.heading('#0', text='Joint')
        self.table.column('#0', width=82, stretch=False)
        for name, caption, width in zip(columns, ('Pos', 'Speed', 'A', '°C'), (76, 76, 58, 50)):
            self.table.heading(name, text=caption, anchor='e')
            self.table.column(name, width=width, anchor='e', stretch=True)
        for joint in JOINTS:
            self.table.insert('', 'end', iid=joint, text=joint, values=('—',) * 4)
        self.table.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        footer = ttk.Frame(self, padding=(20, 8, 20, 12))
        footer.grid(row=3, column=0, sticky='ew')
        self.status_label = ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', wraplength=1000)
        self.status_label.pack(anchor='w', fill='x')
        footer.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(300, event.width - 40)))

    def _style_joints(self):
        """Each joint's tick box fills with its chart colour; restyled after a theme switch."""
        style = ttk.Style(self)
        for index, color in enumerate(joint_colors()):
            style.map(f'Joint{index}.TCheckbutton',
                      indicatorbackground=[('disabled', theme.PANEL), ('selected', color), ('!selected', theme.FIELD)],
                      indicatorforeground=[('selected', theme.BACKGROUND)])

    def select_view(self, name):
        self.view.set(name)
        self.rebuild_charts()

    def select_window(self, name):
        self.window.set(name)
        self._sync_controls()
        self.draw_charts()

    def select_tcp_motion(self, value):
        self.tcp_motion.set(value)
        self.rebuild_charts()

    def select_layout(self, name):
        self.graphs.set(name)
        self.rebuild_charts()

    def _sync_controls(self):
        metrics = VIEWS[self.view.get()]
        for value, button in self.tcp_buttons.items():
            button.configure(text='m/s' if value == 'linear' else '°/s' if self.degrees.get() else 'rad/s')
        for buttons, current in ((self.view_tabs, self.view.get()), (self.window_buttons, self.window.get()),
                                 (self.layout_buttons, self.graphs.get()), (self.tcp_buttons, self.tcp_motion.get())):
            for name, button in buttons.items():
                button.configure(style='TabActive.TButton' if name == current else 'Tab.TButton')
        joints = any(metric in SIGNALS for metric in metrics)
        for holder in (self.joint_group, self.layout_group):
            holder.flow_hidden = not joints
        for part, shown in ((self.series_group, joints), (self.tcp_group, any(metric in TCP_SIGNALS for metric in metrics))):
            part.pack_forget()  # repacked in order below
            part.flow_hidden = not shown
        for part in (self.series_group, self.tcp_group):
            if not part.flow_hidden:
                part.pack(side='left')
        self.scale_check.configure(state='normal' if self.graphs.get() == 'Separate joints' else 'disabled')
        self._arrange_options()

    # ---------------------------------------------------------------- charts
    def rebuild_charts(self):
        self._style_joints()
        self._sync_controls()
        metrics = list(VIEWS[self.view.get()])
        joints = [joint for joint, variable in enumerate(self.joints) if variable.get()]
        self.charts.configure(metrics, joints, self.show_target.get(), self.degrees.get(),
                              separate=self.graphs.get() == 'Separate joints', shared_scale=self.shared_scale.get(),
                              components=self.components.get(), rotation=self.tcp_motion.get() == 'rotation')
        self.canvas.draw_seconds = 0.
        heading = 'Pos °' if self.degrees.get() else 'Pos rad'
        self.table.heading('position', text=heading)
        self.table.heading('speed', text='°/s' if self.degrees.get() else 'rad/s')
        self._last_text = 0.
        self.canvas.fit_content()
        self.chart_viewport.yview_moveto(0)
        self._show_band()
        self.draw_charts()

    def draw_charts(self):
        window = WINDOWS[self.window.get()]
        self.charts.update(window, self.capture.snapshot(window, self.charts.headers))
        self.paint_seconds = 0.  # summed over this update's panel-by-panel paints
        self.paint_charts()
        self._last_draw = time.monotonic()

    def paint_charts(self):
        """Repaint on-screen panels; panels that need a full render follow one per idle turn."""
        if self._paint_id is not None:
            self.after_cancel(self._paint_id)
            self._paint_id = None
        start = time.perf_counter()
        scrolling = time.monotonic() - self._scrolled_at < .3
        # One panel per turn: input and the robot view run between panels, never behind all of them.
        waiting = self.charts.paint(ahead=not scrolling, updates=1)
        self._painted_at = time.monotonic()
        self.paint_seconds += time.perf_counter() - start
        if waiting or scrolling:
            # Off-screen panels render one per pause, so wheel and clicks are handled in between.
            self._paint_id = self.after(10 if waiting else 300, self._paint_more)

    def _paint_more(self):
        self._paint_id = None
        self.paint_charts()

    def _viewport_region(self):
        """The scrolled-in part of the tall chart image, in display coordinates."""
        top, bottom = self.chart_viewport.yview()
        width, height = self.figure.bbox.width, self.figure.bbox.height
        return Bbox.from_extents(0, (1 - bottom) * height, width, (1 - top) * height)

    def _content_height(self, viewport):
        """Figure height for a viewport: the viewport itself, or a fixed height per panel when they do not fit."""
        height = max(180, viewport)
        return max(height, len(self.charts.axes) * PANEL_HEIGHT + 60) if len(self.charts.axes) > 1 else height

    def _show_band(self):
        # Scrolling only moves the image; panels new to the viewport are painted after the scroll.
        self.charts.view(*self.chart_viewport.yview())
        if self._paint_id is None:
            self._paint_id = self.after_idle(self._paint_more)

    def scroll_chart(self, *args):
        self.chart_viewport.yview(*args)
        self._scrolled_at = time.monotonic()
        self._show_band()
        return 'break'

    def wheel_chart(self, event):
        # Windows uses multiples of 120; macOS trackpads deliver small deltas.
        factor = 3 if self.tk.call('tk', 'windowingsystem') == 'aqua' else .4
        pixels = getattr(self, '_wheel_remainder', 0.) - event.delta * factor
        whole = int(pixels)
        self._wheel_remainder = pixels - whole
        if whole:
            self.scroll_chart('scroll', whole, 'units')
        return 'break'

    def toggle_freeze(self):
        self.frozen = not self.frozen
        self.freeze_button.configure(text='Resume' if self.frozen else 'Freeze')
        if not self.frozen:
            self.draw_charts()

    # ------------------------------------------------------------- recording
    def toggle_recording(self):
        recording = self.capture.recording
        if recording is not None and recording.active:
            self.capture.stop_recording()
            self.status.set('Recording stopped. Save it to keep it, or discard it.')
        else:
            if recording is not None and not self.confirm_discard():
                return
            self.capture.discard_recording()
            try:
                self.capture.start_recording()
            except (ValueError, OSError) as error:
                self.status.set(str(error))
                return
            self.saved_path = None
            self.status.set('Recording every sample of the live stream. Switching pages does not stop it.')
        self.refresh(force=True)

    def confirm_discard(self):
        """True when no unsaved recording exists or the user agrees to lose it."""
        recording = self.capture.recording
        if recording is None or not recording.samples or self.saving:
            return not self.saving
        return messagebox.askyesno('Discard live recording?',
                                   f'The {clock_text(recording.duration)} live recording has not been saved.\n\nDiscard it?',
                                   parent=self, default='no')

    def discard_recording(self):
        if self.capture.recording is not None and not self.saving and self.confirm_discard():
            self.capture.discard_recording()
            self.status.set('Recording discarded.')
            self.refresh(force=True)

    def default_filename(self, recording):
        model = (self.app.settings.model if self.app.settings else 'robot').replace(' ', '')
        return f'live_{model}_{recording.started_at:%Y%m%d_%H_%M_%S}.zip'

    def save_recording(self):
        recording = self.capture.recording
        if recording is None or recording.active or self.saving:
            return
        folder = Path(getattr(self.app, 'flight_library_root', None) or default_library())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = Path.home()
        path = filedialog.asksaveasfilename(parent=self, title='Save live recording', initialdir=str(folder),
                                            initialfile=self.default_filename(recording), defaultextension='.zip',
                                            filetypes=[('Flight recording', '*.zip')])
        if not path:
            return
        settings = self.app.settings
        details = dict(model=settings.model if settings else None, robot=settings.ip if settings else None,
                       notes=self.notes.get(), installation=self.app.installation_data)
        self.saving = True
        self.status.set(f'Saving {Path(path).name}…')
        self.refresh(force=True)

        def work():
            try:
                self.results.put(('saved', recording.save(path, **details)))
            except Exception as error:
                self.results.put(('error', str(error) or type(error).__name__))
        threading.Thread(target=work, daemon=True).start()

    def open_saved(self):
        if self.saved_path is not None:
            self.app.open_flights()
            self.app.flight_window.load_path(self.saved_path)

    def _finish_save(self):
        try:
            kind, value = self.results.get_nowait()
        except queue.Empty:
            return
        self.saving = False
        if kind == 'saved':
            self.saved_path = value
            self.capture.discard_recording()
            self.notes.set('')
            self.status.set(f'Saved {value} · Open it in Flight reports to analyse, replay or export a PDF.')
            flights = getattr(self.app, 'flight_window', None)
            if flights is not None and flights.winfo_exists():
                flights.refresh_library()
        else:
            self.status.set(f'Could not save the recording: {value} · It is still available to save again.')

    # --------------------------------------------------------------- refresh
    def _update_record_bar(self):
        recording = self.capture.recording
        streaming = self.app.session is not None and self.app.last_sample is not None
        if recording is not None and recording.active:
            state = 'recording'
            text = f'● REC  {clock_text(recording.duration)} · {recording.samples:,} samples'
        elif recording is not None:
            state = 'saving' if self.saving else 'recorded'
            text = f'Recorded {clock_text(recording.duration)} · {recording.samples:,} samples · not saved'
            if recording.stop_reason and self._record_state == 'recording':
                self.status.set(recording.stop_reason)
        else:
            state = 'saved' if self.saved_path else 'idle'
            text = f'Saved · {self.saved_path.name}' if self.saved_path else 'Not recording'
        if self.record_text.get() != text:
            self.record_text.set(text)
        key = (state, streaming)
        if key == self._record_state:
            return
        self._record_state = key
        self.record_label.configure(foreground=theme.DANGER if state == 'recording' else theme.INK)
        self.record_button.configure(text='■ Stop' if state == 'recording' else '● Record',
                                     state='normal' if state == 'recording' or (streaming and state != 'saving') else 'disabled')
        finished = state == 'recorded'
        self.save_button.configure(state='normal' if finished else 'disabled',
                                   style='Accent.TButton' if finished else 'TButton')
        self.discard_button.configure(state='normal' if finished else 'disabled')
        self.open_button.configure(state='normal' if state == 'saved' else 'disabled')
        for button, shown in ((self.save_button, state in ('recorded', 'saving')),
                              (self.discard_button, state in ('recorded', 'saving')),
                              (self.open_button, state == 'saved')):
            button.grid() if shown else button.grid_remove()

    def _update_state(self):
        app = self.app
        sample = app.last_sample
        if app.settings is None or (app.session is None and sample is None):
            text, color = 'Not connected', theme.MUTED
        elif app.session is None:
            text, color = f'Disconnected · {app.settings.model} · {app.settings.ip} · showing last received data', theme.WARNING
        elif sample is None:
            text, color = f'Connecting to {app.settings.ip}…', theme.ACCENT
        elif reconnect_reason(app.session):
            text = (f'Reconnecting · {app.settings.model} · {app.settings.ip} · the robot closed the data stream · '
                    f'retrying for up to {DATA_TIMEOUT:.0f} s')
            color = theme.WARNING
        elif (age := time.monotonic() - sample.received_at) >= STALE_AFTER:
            text = (f'Slow link · {app.settings.model} · {app.settings.ip} · no data for {age:.0f} s · '
                    f'waiting (gives up after {DATA_TIMEOUT:.0f} s)')
            color = theme.WARNING
        else:
            rate = self.capture.sample_rate()
            text = f'Live · {app.settings.model} · {app.settings.ip}' + (f' · {rate:.0f} Hz' if rate else '')
            color = theme.SUCCESS
        if self.state.get() != text:
            self.state.set(text)
            self.state_label.configure(foreground=color)
        if self.status.get() in (CONNECT_HINT, STREAM_HINT):
            self.status.set(STREAM_HINT if app.session is not None and sample is not None else CONNECT_HINT)

    def _update_viewer(self):
        app = self.app
        if app.settings is None:
            return
        identity = (app.settings.ip, app.settings.model)
        if self.viewer is None or identity != self.viewer_identity:
            from motionstudio.viewer.robot_view import RobotView
            if self.viewer is not None:
                self.viewer.destroy()
            self.viewer_notice.pack_forget()
            self.viewer = RobotView(self.viewer_host, app.settings.model)
            self.viewer.visibility['info'] = False
            self.viewer.pack(fill='both', expand=True)
            self.viewer_identity = identity
            self._last_sample = self._viewer_joints = None
        sample = app.last_sample
        now = time.monotonic()
        # The side view is a glance, not the Simulator: at most 5 frames a second, only when the
        # robot moved, never in the same turn as a chart paint, and paused while scrolling.
        if (sample is not None and sample is not self._last_sample and now - self._viewer_at >= .2
                and now - self._scrolled_at >= .25 and now - self._painted_at >= .02):
            joints = np.asarray(sample.joints)
            if (self._viewer_joints is None or np.abs(joints - self._viewer_joints).max() > 1e-4
                    or self.viewer.installation is not app.installation_data):
                self.viewer.update_robot(sample, app.installation_data)
                self._viewer_joints = joints
                self._viewer_at = now
            self._last_sample = sample

    def _update_readouts(self):
        sample = self.app.last_sample
        if sample is None:
            return
        values = sample.telemetry
        safety = values.get('safety_status', values.get('safety_mode'))
        scaling = values.get('speed_scaling')
        for key, text, color in (
                ('robot', enum_text(values.get('robot_mode'), ROBOT_MODES), theme.INK),
                ('safety', enum_text(safety, SAFETY_MODES),
                 theme.INK if safety is None else theme.SUCCESS if safety == 1 else theme.WARNING),
                ('program', enum_text(values.get('runtime_state'), RUNTIME_STATES), theme.INK),
                ('speed', 'Unavailable' if scaling is None else f'{scaling * 100:.0f} %', theme.INK),
                *zip(('tcp_speed', 'tcp_acceleration'), self._tcp_readouts(values), (theme.INK, theme.INK))):
            if self.tiles[key].cget('text') != text:
                self.tiles[key].configure(text=text, foreground=color)
        degrees = self.degrees.get()
        speeds, currents, temperatures = (values.get(name) for name in ('actual_qd', 'actual_current', 'joint_temperatures'))
        for joint, name in enumerate(JOINTS):
            angle = lambda value: math.degrees(value) if degrees else value
            row = (f'{angle(sample.joints[joint]):.2f}' if degrees else f'{sample.joints[joint]:.4f}',
                   '—' if speeds is None else f'{angle(speeds[joint]):.1f}' if degrees else f'{speeds[joint]:.3f}',
                   '—' if currents is None else f'{currents[joint]:.2f}',
                   '—' if temperatures is None else f'{temperatures[joint]:.1f}')
            self.table.item(name, values=row)

    def _tcp_readouts(self, values):
        """Latest TCP speed and derived acceleration magnitudes as tile text, linear or rotational as charted."""
        speed = values.get('actual_TCP_speed')
        if speed is None:
            return 'Unavailable', 'Unavailable'
        rotation = self.tcp_motion.get() == 'rotation'
        headers = TCP_ROTATION if rotation else TCP_VELOCITY
        data = self.capture.snapshot(.3, headers)
        velocity = np.column_stack([data[header] for header in headers])
        times = data['Timestamp [s]']
        magnitude = tcp_series('tcp_acceleration', times, velocity)[:, 3]
        # Read half an averaging window back, where the centred average is complete.
        magnitude = magnitude[np.isfinite(magnitude) & (times <= times[-1] - AVERAGE / 2)] if len(times) else magnitude
        if not rotation:
            return (latest_text(math.hypot(*speed[:3]), 'm/s'),
                    latest_text(magnitude[-1], 'm/s²') if len(magnitude) else '—')
        scale, unit = (180 / math.pi, '°/s') if self.degrees.get() else (1., 'rad/s')
        return (latest_text(math.hypot(*speed[3:6]) * scale, unit),
                latest_text(magnitude[-1] * scale, unit + '²') if len(magnitude) else '—')

    def refresh(self, force=False):
        """Called from the application's poll loop; cheap unless the page is visible."""
        self._finish_save()
        self._update_record_bar()
        if not force and not self.winfo_viewable():
            return
        self._update_state()
        self._update_viewer()
        now = time.monotonic()
        streaming = self.app.session is not None
        # Wait at least three times the last paint, so drawing never takes more than a
        # quarter of the UI thread and controls stay responsive on slow machines.
        interval = max(.1, 3 * self.paint_seconds)
        scrolling = now - self._scrolled_at < .3  # new data waits until the wheel stops
        if force or (streaming and not self.frozen and not scrolling
                     and now - max(self._last_draw, self.canvas.drawn_at) >= interval):
            self.draw_charts()
        if force or now - self._last_text >= .2:
            self._update_readouts()
            self._last_text = now

    def shutdown(self):
        self.capture.discard_recording()
