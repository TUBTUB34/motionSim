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
from matplotlib.figure import Figure

from flight_charts import JOINTS, joint_colors, serialized_render
from flight_transfer import default_library
from telemetry import ROBOT_MODES, RUNTIME_STATES, SAFETY_MODES, enum_text
import ui_dialogs as messagebox
import ui_theme as theme
from ui_theme import flow

# metric -> (title, actual header, target header or None, unit, is an angle)
SIGNALS = {
    'position': ('Position', 'Actual position j{j} [rad]', 'Target position j{j} [rad]', 'rad', True),
    'velocity': ('Velocity', 'Actual velocity j{j} [rad/s]', 'Target velocity j{j} [rad/s]', 'rad/s', True),
    'current': ('Current', 'Actual current j{j} [A]', 'Target current j{j} [A]', 'A', False),
    'torque': ('Torque', 'Actual current as torque j{j} [Nm]', 'Target moment j{j} [Nm]', 'Nm', False),
    'temperature': ('Temperature', 'Temperature j{j} [C]', None, '°C', False),
}
WINDOWS = {'10 s': 10., '30 s': 30., '60 s': 60., '120 s': 120.}
PROTECTIVE_STOP = 3


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


def clock_text(seconds):
    return f'{int(seconds // 60):02d}:{seconds % 60:04.1f}'


class LiveCharts:
    """Persistent axes and lines; each refresh only replaces their data."""
    def __init__(self, figure):
        self.figure = figure
        self.axes = {}
        self.lines = {}
        self.stops = {}
        self.headers = ()
        self.degrees = True

    @serialized_render
    def configure(self, metrics, joints, show_target, degrees):
        self.figure.clear()
        self.figure.set_facecolor(theme.BACKGROUND)
        self.axes, self.lines, self.stops = {}, {}, {}
        self.degrees = degrees
        headers = ['Safety mode']
        colors = joint_colors()
        first = None
        for row, metric in enumerate(metrics):
            title, actual, target, unit, angle = SIGNALS[metric]
            axis = self.figure.add_subplot(len(metrics), 1, row + 1, sharex=first)
            first = first or axis
            self.axes[metric] = axis
            axis.set_facecolor(theme.BACKGROUND)
            if angle and degrees:
                unit = unit.replace('rad', '°')
            axis.set_title(f'{title} · {unit}', color=theme.INK, loc='left', fontsize=9, fontweight='bold', pad=6)
            for joint in joints:
                for dashed, template in ((False, actual), (True, target if show_target else None)):
                    if template is None:
                        continue
                    header = template.format(j=joint)
                    headers.append(header)
                    self.lines[header] = (metric, axis.plot(
                        [], [], color=colors[joint], linewidth=1.1 if not dashed else .9,
                        linestyle='--' if dashed else '-')[0])
            axis.tick_params(colors=theme.MUTED, labelsize=8)
            axis.grid(color=theme.BORDER, linewidth=.6)
            for spine in axis.spines.values():
                spine.set_edgecolor(theme.BORDER)
            if row < len(metrics) - 1:
                axis.tick_params(labelbottom=False)
        if first is None:
            self.figure.text(.5, .5, 'Tick at least one signal to chart.', color=theme.MUTED, ha='center')
        else:
            axis.set_xlabel('Seconds before now' + (' · solid: actual · dashed: target' if show_target else ''),
                            color=theme.MUTED, fontsize=8)
            self.figure.subplots_adjust(left=.075, right=.985, top=.94, bottom=.1, hspace=.42)
        self.headers = tuple(dict.fromkeys(headers))

    @serialized_render
    def update(self, window, data):
        times = data['Timestamp [s]']
        if not len(times) or not self.axes:
            return
        x = times - times[-1]
        for header, (metric, line) in self.lines.items():
            values = data[header]
            if not np.isfinite(values).any():
                line.set_data([], [])
                continue
            if SIGNALS[metric][4] and self.degrees:
                values = np.degrees(values)
            line.set_data(*envelope(x, values))
        stopped = data['Safety mode'] == PROTECTIVE_STOP
        for metric, axis in self.axes.items():
            previous = self.stops.pop(metric, None)
            if previous is not None:
                previous.remove()
            if stopped.any():
                # Shading uses only the streamed safety mode, as in Flight reports.
                self.stops[metric] = axis.fill_between(x, 0, 1, where=stopped, transform=axis.get_xaxis_transform(),
                                                       color=theme.DANGER, alpha=.14, linewidth=0)
            axis.relim(visible_only=True)
            axis.autoscale_view(scalex=False)
            axis.set_xlim(-window, 0)


class LivePage(ttk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.capture = app.live_capture
        self.signals = {name: tk.BooleanVar(value=name in ('position', 'velocity', 'current')) for name in SIGNALS}
        self.joints = [tk.BooleanVar(value=True) for _ in JOINTS]
        self.show_target = tk.BooleanVar(value=True)
        self.degrees = tk.BooleanVar(value=True)
        self.window = tk.StringVar(value='30 s')
        self.frozen = False
        self.notes = tk.StringVar()
        self.state = tk.StringVar()
        self.record_text = tk.StringVar(value='Not recording')
        self.status = tk.StringVar(value='Connect a robot to watch its telemetry. Recording starts only when you choose Record.')
        self.saved_path = None
        self.saving = False
        self.results = queue.Queue()
        self.viewer = None
        self.viewer_identity = None
        self._last_sample = None
        self._last_draw = self._last_text = 0.
        self._record_state = None
        self._build()
        self.rebuild_charts()
        theme.register_theme_callback(self, self.rebuild_charts)
        self.refresh(force=True)

    # ---------------------------------------------------------------- layout
    def _build(self):
        from flight_view import FlightCanvas
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        heading = ttk.Frame(self, style='Toolbar.TFrame', padding=(20, 14))
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='Live monitor', style='Title.Panel.TLabel').pack(side='left')
        self.state_label = ttk.Label(heading, textvariable=self.state, style='Panel.TLabel')
        self.state_label.pack(side='left', padx=(18, 0))
        ttk.Button(heading, text='How to', command=lambda: self.app.open_help('Live monitor')).pack(side='right')

        bar = ttk.Frame(self, padding=(20, 12, 20, 4))
        bar.grid(row=1, column=0, sticky='ew')
        bar.columnconfigure(4, weight=1)
        self.record_button = ttk.Button(bar, text='● Record', style='Accent.TButton', command=self.toggle_recording, width=11)
        self.record_button.grid(row=0, column=0)
        self.record_label = ttk.Label(bar, textvariable=self.record_text, width=44)
        self.record_label.grid(row=0, column=1, padx=(12, 12), sticky='w')
        ttk.Label(bar, text='Note', style='Muted.TLabel').grid(row=0, column=3, padx=(0, 6))
        self.notes_entry = ttk.Entry(bar, textvariable=self.notes)
        self.notes_entry.grid(row=0, column=4, sticky='ew')
        self.save_button = ttk.Button(bar, text='Save recording…', command=self.save_recording, state='disabled')
        self.save_button.grid(row=0, column=5, padx=(10, 0))
        self.discard_button = ttk.Button(bar, text='Discard', command=self.discard_recording, state='disabled')
        self.discard_button.grid(row=0, column=6, padx=(8, 0))
        self.open_button = ttk.Button(bar, text='Open in Flight reports', command=self.open_saved, state='disabled')
        self.open_button.grid(row=0, column=7, padx=(8, 0))

        body = ttk.Frame(self, padding=(20, 8, 20, 0))
        body.grid(row=2, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        options = ttk.Frame(body)
        options.grid(row=0, column=0, sticky='ew', pady=(0, 6))

        def group(caption):
            holder = ttk.Frame(options)
            ttk.Label(holder, text=caption, style='Section.TLabel').pack(side='left', padx=(0, 8))
            return holder

        signals = group('SIGNALS')
        for name, spec in SIGNALS.items():
            ttk.Checkbutton(signals, text=spec[0], variable=self.signals[name],
                            command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        joints = group('JOINTS')
        for name, variable in zip(JOINTS, self.joints):
            ttk.Checkbutton(joints, text=name, variable=variable, command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        view = group('VIEW')
        ttk.Checkbutton(view, text='Targets', variable=self.show_target, command=self.rebuild_charts).pack(side='left', padx=(0, 8))
        ttk.Checkbutton(view, text='Degrees', variable=self.degrees, command=self.rebuild_charts).pack(side='left', padx=(0, 12))
        window = ttk.Combobox(view, textvariable=self.window, values=tuple(WINDOWS), state='readonly', width=6)
        window.pack(side='left')
        window.bind('<<ComboboxSelected>>', lambda _event: self.draw_charts())
        self.freeze_button = ttk.Button(view, text='Freeze', command=self.toggle_freeze, width=8)
        self.freeze_button.pack(side='left', padx=(8, 0))
        flow(options, gap=14)
        self.legend_bar = tk.Frame(body, background=theme.BACKGROUND)
        self.legend_bar.grid(row=1, column=0, sticky='ew', pady=(0, 6))
        self._arrange_legend = flow(self.legend_bar)
        chart_frame = tk.Frame(body, background=theme.BACKGROUND, highlightbackground=theme.BORDER, highlightthickness=1)
        chart_frame.grid(row=2, column=0, sticky='nsew')
        self.figure = Figure(figsize=(8, 5), dpi=100, facecolor=theme.BACKGROUND)
        self.charts = LiveCharts(self.figure)
        self.canvas = FlightCanvas(self.figure, master=chart_frame)
        self.canvas.get_tk_widget().pack(fill='both', expand=True)

        side = ttk.Frame(body, width=360)
        side.grid(row=0, column=1, rowspan=3, sticky='ns', padx=(16, 0))
        side.grid_propagate(False)
        side.columnconfigure((0, 1), weight=1)
        side.rowconfigure(0, weight=1)
        self.viewer_host = tk.Frame(side, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        self.viewer_host.grid(row=0, column=0, columnspan=2, sticky='nsew')
        self.viewer_notice = ttk.Label(self.viewer_host, text='No live robot connection', style='Muted.Panel.TLabel',
                                       anchor='center', justify='center', wraplength=300)
        self.viewer_notice.pack(fill='both', expand=True)
        self.tiles = {}
        for index, (key, caption) in enumerate((('robot', 'ROBOT'), ('safety', 'SAFETY'),
                                                ('program', 'PROGRAM'), ('speed', 'SPEED SCALING'))):
            tile = ttk.Frame(side, style='Card.TFrame', padding=(10, 8))
            tile.grid(row=1 + index // 2, column=index % 2, sticky='ew', pady=(8, 0), padx=(0, 4) if index % 2 == 0 else (4, 0))
            ttk.Label(tile, text=caption, style='Section.Panel.TLabel').pack(anchor='w')
            value = self.tiles[key] = ttk.Label(tile, text='—', style='Panel.TLabel', font=('TkDefaultFont', 11, 'bold'),
                                                wraplength=150)
            value.pack(anchor='w', pady=(2, 0))
        columns = ('position', 'speed', 'current', 'temperature')
        self.table = ttk.Treeview(side, columns=columns, show='tree headings', height=6, selectmode='none')
        self.table.heading('#0', text='Joint')
        self.table.column('#0', width=82, stretch=False)
        for name, caption, width in zip(columns, ('Pos', 'Speed', 'A', '°C'), (76, 76, 58, 50)):
            self.table.heading(name, text=caption, anchor='e')
            self.table.column(name, width=width, anchor='e', stretch=True)
        for joint in JOINTS:
            self.table.insert('', 'end', iid=joint, text=joint, values=('—',) * 4)
        self.table.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        footer = ttk.Frame(self, padding=(20, 8, 20, 12))
        footer.grid(row=3, column=0, sticky='ew')
        self.status_label = ttk.Label(footer, textvariable=self.status, style='Muted.TLabel', wraplength=1000)
        self.status_label.pack(anchor='w', fill='x')
        footer.bind('<Configure>', lambda event: self.status_label.configure(wraplength=max(300, event.width - 40)))

    # ---------------------------------------------------------------- charts
    def rebuild_charts(self):
        metrics = [name for name, variable in self.signals.items() if variable.get()]
        joints = [joint for joint, variable in enumerate(self.joints) if variable.get()]
        self.charts.configure(metrics, joints, self.show_target.get(), self.degrees.get())
        heading = 'Pos °' if self.degrees.get() else 'Pos rad'
        self.table.heading('position', text=heading)
        self.table.heading('speed', text='°/s' if self.degrees.get() else 'rad/s')
        for child in self.legend_bar.winfo_children():
            child.destroy()
        chips = [(JOINTS[joint], joint_colors()[joint]) for joint in joints]
        if chips and self.show_target.get():
            chips.append(('Solid: actual · dashed: target', theme.MUTED))
        for text, color in chips:
            chip = tk.Frame(self.legend_bar, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
            tk.Label(chip, text='●', foreground=color, background=theme.PANEL, font=('TkDefaultFont', 9)).pack(side='left', padx=(8, 2))
            tk.Label(chip, text=text, foreground=theme.INK, background=theme.PANEL,
                     font=('TkDefaultFont', 9)).pack(side='left', padx=(0, 8), pady=2)
        self._arrange_legend()
        self._last_text = 0.
        self.draw_charts()

    def draw_charts(self):
        window = WINDOWS[self.window.get()]
        self.charts.update(window, self.capture.snapshot(window, self.charts.headers))
        self.canvas.draw_idle()
        self._last_draw = time.monotonic()

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

    def _update_state(self):
        app = self.app
        sample = app.last_sample
        if app.settings is None or (app.session is None and sample is None):
            text, color = 'Not connected', theme.MUTED
        elif app.session is None:
            text, color = f'Disconnected · {app.settings.model} · {app.settings.ip} · showing last received data', theme.WARNING
        elif sample is None:
            text, color = f'Connecting to {app.settings.ip}…', theme.ACCENT
        elif time.monotonic() - sample.received_at >= 1:
            text, color = f'Stale · {app.settings.model} · {app.settings.ip} · no data for over 1 s', theme.WARNING
        else:
            rate = self.capture.sample_rate()
            text = f'Live · {app.settings.model} · {app.settings.ip}' + (f' · {rate:.0f} Hz' if rate else '')
            color = theme.SUCCESS
        if self.state.get() != text:
            self.state.set(text)
            self.state_label.configure(foreground=color)

    def _update_viewer(self):
        app = self.app
        if app.settings is None:
            return
        identity = (app.settings.ip, app.settings.model)
        if self.viewer is None or identity != self.viewer_identity:
            from view import RobotView
            if self.viewer is not None:
                self.viewer.destroy()
            self.viewer_notice.pack_forget()
            self.viewer = RobotView(self.viewer_host, app.settings.model)
            self.viewer.visibility['info'] = False
            self.viewer.pack(fill='both', expand=True)
            self.viewer_identity = identity
            self._last_sample = None
        sample = app.last_sample
        if sample is not None and sample is not self._last_sample:
            self.viewer.update_robot(sample, app.installation_data)
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
                ('speed', 'Unavailable' if scaling is None else f'{scaling * 100:.0f} %', theme.INK)):
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
        if (force or (streaming and not self.frozen and now - self._last_draw >= .12)):
            self.draw_charts()
        if force or now - self._last_text >= .2:
            self._update_readouts()
            self._last_text = now

    def shutdown(self):
        self.capture.discard_recording()
