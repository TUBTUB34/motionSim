"""Recorded-flight workspace using the same native renderer as the live viewer."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog

import numpy as np
from matplotlib.backends.backend_tkagg import NavigationToolbar2Tk
from matplotlib.figure import Figure

from motionstudio.flight.charts import (FlightCharts, METRICS, METRIC_LABELS, UNITS, X_REFERENCES, metric_unit,
                                       series_label, script_line_at)
from motionstudio.core.kinematics import JOINTS
from motionstudio.flight.data import discover_flights, load_flight
from motionstudio.core.playback import PlaybackTimeline, sample_at
from motionstudio.flight.stats import STATE_FIELDS, protective_stops, state_changes
from motionstudio.ui.chart_canvas import ChartCanvas
from motionstudio.ui.workspace_panel import WorkspacePanel
from motionstudio.flight.transfer import discover_library
from motionstudio.core.paths import default_library, start_folder
from motionstudio.ui import theme
from motionstudio.ui.theme import ScrollableFrame, flow, style_text, style_toolbar, status_color
from motionstudio.core.about import APP_NAME

TRAILS = ('Off', 'Last 5 s', 'Whole recording')
EVIDENCE_WINDOW = .25


def label(parent, text=None, variable=None, *, muted=False, size=10, bold=False, **kwargs):
    kwargs.setdefault('anchor', 'w')
    return tk.Label(parent, text=text, textvariable=variable, background=parent.cget('background'),
                    foreground=theme.MUTED if muted else theme.INK, font=theme.font(size, 'bold' if bold else 'normal'),
                    **kwargs)


def button(parent, text, command, *, accent=False, **kwargs):
    return ttk.Button(parent, text=text, command=command,
                      style='Accent.TButton' if accent else 'TButton', **kwargs)


class FlightWindow(WorkspacePanel):
    def __init__(self, app, parent=None):
        super().__init__(app.root, page_host=parent, on_show=lambda: app.navigate_panel(self), on_hide=getattr(app, 'show_workspace', None), background=theme.BACKGROUND)
        self.app = app
        self.title(f'{APP_NAME} · Flight reports')
        self.geometry('1420x920')
        self.minsize(1000, 720)
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.closed = False
        self.report = self.timeline = None
        self.cache = OrderedDict()
        self.paths = []
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='flight-files')
        self.future = None
        self.generation = 0
        self.discovery_generation = 0
        self.loading = False
        self.exporting = False
        self._setting_scale = False
        self._last_tick = time.monotonic()
        self._last_hover = 0.
        self.flight = tk.StringVar()
        self.library = tk.StringVar(value=str(Path(getattr(app, 'flight_library_root', default_library())).resolve()))
        app.flight_library_root = Path(self.library.get())
        self.joint = tk.StringVar(value='Shoulder')
        self.focus = tk.StringVar(value='Position')
        self.view_mode = tk.StringVar(value='One joint')
        self.compare_series = tk.StringVar(value='Actual + target')
        self.compare_layout = tk.StringVar(value='Combined')
        self.compare_scale = tk.StringVar(value='Per joint')
        self.compare_joints = [tk.BooleanVar(value=True) for _ in JOINTS]
        self.view_notice = tk.StringVar()
        self._single_focus = 'Position'
        self.speed = tk.StringVar(value='1×')
        self.status = tk.StringVar(value='Finding flight recordings…')
        self.metadata = tk.StringVar(value='Open a flight recording to inspect measured robot motion.')
        self.clock = tk.StringVar(value='0.000 / 0.000 s')
        self.script_position = tk.StringVar(value='Script line unavailable')
        self.readout = tk.StringVar(value='Hover a graph or scrub the timeline to inspect a sample.')
        self.state_text = tk.StringVar(value='')
        self.state_band = tk.StringVar(value='Safety mode')
        self.x_reference = tk.StringVar(value=X_REFERENCES[0])
        self.chart_mode = tk.StringVar(value='Joint · Shoulder')
        self.degrees = tk.BooleanVar(value=False)
        self.sample_text = tk.StringVar(value='No recording loaded')
        self.target_overlay = tk.BooleanVar(value=False)
        self.trail_mode = tk.StringVar(value=TRAILS[0])
        self.follow_script = tk.BooleanVar(value=True)
        self.evidence_choice = tk.StringVar()
        self.evidence_text = tk.StringVar(value='Load a recording to list its recorded stops and state changes.')
        self.view_info = tk.StringVar()
        self.robot = None
        self.events_list = []
        self._trail = None
        self._build()
        self.after_id = self.after(30, self.poll)
        self.refresh_library()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        files = tk.Frame(self, background=theme.PANEL, padx=16, pady=10)
        files.grid(row=0, column=0, sticky='ew')
        files.columnconfigure(1, weight=1)
        label(files, 'Flight report', bold=True).grid(row=0, column=0, padx=(0, 10))
        self.flight_combo = ttk.Combobox(files, textvariable=self.flight, state='readonly', width=33)
        self.flight_combo.grid(row=0, column=1, sticky='ew')
        self.flight_combo.bind('<<ComboboxSelected>>', self.select_flight)
        button(files, 'Open…', self.open_file).grid(row=0, column=2, padx=(8, 0))
        button(files, 'Folder…', self.choose_folder).grid(row=0, column=3, padx=(8, 0))
        button(files, 'Reload', self.refresh_library).grid(row=0, column=4, padx=(8, 0))
        self.export_button = button(files, 'Export PDF…', self.export_pdf, accent=True, state='disabled')
        self.export_button.grid(row=0, column=5, padx=(20, 0))
        button(files, 'Full-size playback', self.show_simulator).grid(row=0, column=6, padx=(8, 0))
        if not self.embedded:
            button(files, 'Retrieve…', self.open_remote_reports).grid(row=0, column=7, padx=(8, 0))
        button(files, 'How to', lambda: self.app.open_help('Flight reports')).grid(row=0, column=8, padx=(8, 0))
        self.library_summary = tk.StringVar(value='Loading reports in this folder…')
        folder = tk.Frame(files, background=theme.PANEL)
        folder.grid(row=1, column=0, columnspan=9, sticky='ew', pady=(6, 0))
        self.metadata_label = label(folder, variable=self.metadata, size=9)
        self.metadata_label.pack(side='left')
        label(folder, variable=self.library_summary, muted=True, size=9).pack(side='right')
        label(folder, variable=self.library, muted=True, size=9).pack(side='right', padx=(0, 10))

        self.body = tk.Frame(self, background=theme.BACKGROUND, padx=12, pady=10)
        self.body.grid(row=1, column=0, sticky='nsew')
        self.body.columnconfigure(0, weight=3, uniform='flight')
        self.body.columnconfigure(1, weight=7, uniform='flight')
        self.body.rowconfigure(0, weight=1)
        self._build_robot_column()
        self._build_chart_column()

        controls = tk.Frame(self, background=theme.PANEL, padx=16, pady=10)
        controls.grid(row=2, column=0, sticky='ew')
        controls.columnconfigure(3, weight=1)
        self.play_button = button(controls, 'Play', self.toggle_play, accent=True, state='disabled', width=7)
        self.play_button.grid(row=0, column=0)
        self.restart_button = button(controls, 'Restart', self.restart, state='disabled')
        self.restart_button.grid(row=0, column=1, padx=8)
        combo = ttk.Combobox(controls, textvariable=self.speed, state='readonly', width=5,
                            values=('0.25×', '0.5×', '1×', '2×', '4×'))
        combo.grid(row=0, column=2, padx=(0, 10))
        combo.bind('<<ComboboxSelected>>', self.change_speed)
        self.scrubber = tk.Scale(controls, from_=0, to=1, resolution=.001, orient='horizontal',
                                 showvalue=False, command=self.scrub, background=theme.PANEL, foreground=theme.INK,
                                 troughcolor=theme.BACKGROUND, highlightthickness=1, sliderlength=15,
                                 highlightbackground=theme.PANEL, highlightcolor=theme.ACCENT, takefocus=True,
                                 activebackground=theme.ACCENT, borderwidth=0, state='disabled')
        self.scrubber.grid(row=0, column=3, sticky='ew')
        label(controls, variable=self.clock, size=10, width=20, anchor='e').grid(row=0, column=4, padx=(10, 0))
        self.status_label = label(self, variable=self.status, muted=True, size=9, padx=16, pady=6,
                                  wraplength=1350, justify='left')
        self.status_label.grid(row=3, column=0, sticky='ew')
        self._status_trace = self.status.trace_add('write', lambda *_:
            self.status_label.configure(foreground=status_color(self.status.get())))
        theme.register_theme_callback(self, self.refresh_theme)
        self.bind('<Configure>', self._resize)
        self.bind('<Map>', lambda _event: self.robot.redraw() if self.robot else None)

    def _build_robot_column(self):
        left = tk.Frame(self.body, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1, uniform='robot')
        left.rowconfigure(2, weight=1, uniform='robot')
        label(left, variable=self.sample_text, size=11, bold=True, padx=12, pady=8).grid(row=0, column=0, sticky='ew')
        self.robot_holder = tk.Frame(left, background=theme.PANEL)
        self.robot_holder.grid(row=1, column=0, sticky='nsew')
        self.robot_holder.columnconfigure(0, weight=1)
        self.robot_holder.rowconfigure(0, weight=1)
        self.robot_notice = label(self.robot_holder, 'Load a recording to replay the robot here.', muted=True,
                                  anchor='center', justify='center', wraplength=260)
        self.robot_notice.grid(row=0, column=0)
        self.details = ScrollableFrame(left, style='Panel.TFrame', padding=(12, 8))
        self.details.grid(row=2, column=0, sticky='nsew')
        panel = self.details.content
        panel.columnconfigure(1, weight=1)
        self.overlay_check = ttk.Checkbutton(panel, text='Target overlay', variable=self.target_overlay,
                                             command=self.update_overlays, style='Panel.TCheckbutton')
        self.overlay_check.grid(row=0, column=0, sticky='w')
        trail = ttk.Frame(panel, style='Panel.TFrame')
        trail.grid(row=0, column=1, sticky='e', padx=(10, 0))
        ttk.Label(trail, text='TCP trail', style='Panel.TLabel').pack(side='left', padx=(0, 6))
        self.trail_combo = ttk.Combobox(trail, textvariable=self.trail_mode, values=TRAILS, state='readonly', width=15)
        self.trail_combo.pack(side='left')
        self.trail_combo.bind('<<ComboboxSelected>>', self.update_overlays)
        # This recording's mounting: shown from the recording, adjustable for this recording only.
        from motionstudio.viewer.mounting import MountingControls
        from motionstudio.viewer.robot_view import recording_key
        self.mounting_controls = MountingControls(
            panel, lambda: self.report.installation if self.report else None,
            lambda: recording_key(self.report) if self.report else 'recording:', compact=True)
        self.mounting_controls.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        ttk.Separator(panel).grid(row=2, column=0, columnspan=2, sticky='ew', pady=10)
        self.evidence_title = ttk.Label(panel, text='Recorded events', style='Panel.TLabel', font=theme.font(10, 'bold'))
        self.evidence_title.grid(row=3, column=0, columnspan=2, sticky='w')
        self.evidence_combo = ttk.Combobox(panel, textvariable=self.evidence_choice, state='disabled')
        self.evidence_combo.grid(row=4, column=0, columnspan=2, sticky='ew', pady=(6, 6))
        self.evidence_combo.bind('<<ComboboxSelected>>', self.select_evidence)
        self.evidence_label = ttk.Label(panel, textvariable=self.evidence_text, style='Muted.Panel.TLabel',
                                        wraplength=280, justify='left')
        self.evidence_label.grid(row=5, column=0, columnspan=2, sticky='ew')
        ttk.Separator(panel).grid(row=6, column=0, columnspan=2, sticky='ew', pady=10)
        self.sections = {}
        for row, (name, caption) in enumerate((('joints', 'Joint values'), ('recording', 'Recording details')), 7):
            toggle = ttk.Button(panel, text='▸ ' + caption, style='Nav.TButton', command=lambda key=name: self.toggle_section(key))
            toggle.grid(row=row * 2, column=0, columnspan=2, sticky='w')
            self.sections[name] = [toggle, None, caption, False]
        columns = ('actual', 'target', 'velocity', 'current')
        values = ttk.Frame(panel, style='Panel.TFrame')
        values.columnconfigure(0, weight=1)
        self.joint_table = ttk.Treeview(panel, columns=columns, show='tree headings', height=6, selectmode='none')
        self.joint_table.heading('#0', text='Joint')
        self.joint_table.column('#0', width=74, stretch=False)
        for name, caption in zip(columns, ('Actual', 'Target', 'Speed', 'Current')):
            self.joint_table.heading(name, text=caption, anchor='e')
            self.joint_table.column(name, width=58, anchor='e')
        for joint in JOINTS:
            self.joint_table.insert('', 'end', iid=joint, text=joint, values=('—',) * 4)
        self.joint_table.grid(in_=values, row=0, column=0, sticky='ew')
        self.readout_label = ttk.Label(values, textvariable=self.readout, style='Muted.Panel.TLabel',
                                       wraplength=280, justify='left')
        self.readout_label.grid(row=1, column=0, sticky='ew', pady=(6, 0))
        self.sections['joints'][1] = values
        self.full_metadata = tk.StringVar()
        self.sections['recording'][1] = ttk.Label(panel, textvariable=self.full_metadata, style='Muted.Panel.TLabel',
                                                  wraplength=280, justify='left')
        for index, name in enumerate(('joints', 'recording')):
            self.sections[name][1].grid(row=(7 + index) * 2 + 1, column=0, columnspan=2, sticky='ew', pady=(4, 8))
            self.sections[name][1].grid_remove()
        panel.bind('<Configure>', lambda event: [widget.configure(wraplength=max(160, event.width - 30)) for widget in
                                                 (self.evidence_label, self.readout_label, self.sections['recording'][1])]
                   if event.widget is panel else None)

    def _build_chart_column(self):
        right = tk.Frame(self.body, background=theme.BACKGROUND)
        right.grid(row=0, column=1, sticky='nsew')
        self.chart_area = right
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)
        tabs = tk.Frame(right, background=theme.BACKGROUND)
        tabs.grid(row=0, column=0, sticky='ew')
        self.metric_tabs = {}
        for name in ('All readings', *METRIC_LABELS.values()):
            tab = self.metric_tabs[name] = ttk.Button(tabs, text=name, style='Tab.TButton',
                                                      command=lambda value=name: self.select_metric(value))
        flow(tabs, gap=4)
        self.legend_bar = tk.Frame(right, background=theme.BACKGROUND)
        self.legend_bar.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        self._arrange_legend = flow(self.legend_bar)
        tools = tk.Frame(right, background=theme.BACKGROUND)
        tools.grid(row=2, column=0, sticky='ew', pady=(8, 0))

        def group(parent, caption):
            holder = tk.Frame(parent, background=theme.BACKGROUND)
            label(holder, caption, muted=True).pack(side='left', padx=(0, 6))
            return holder

        self.band_buttons = {}
        band = group(tools, 'State band')
        for field in STATE_FIELDS:
            item = ttk.Button(band, text=field.split()[0], command=lambda value=field: self.select_state_band(value))
            item.pack(side='left', padx=(0, 4))
            self.band_buttons[field] = item
        self.mode_combo = ttk.Combobox(group(tools, 'Chart'), textvariable=self.chart_mode, state='readonly', width=17,
                                       values=(*(f'Joint · {name}' for name in JOINTS), 'Compare joints'))
        self.mode_combo.pack(side='left')
        self.mode_combo.bind('<<ComboboxSelected>>', self.select_chart_mode)
        reference = ttk.Combobox(group(tools, 'X axis'), textvariable=self.x_reference, values=X_REFERENCES,
                                 state='readonly', width=19)
        reference.pack(side='left')
        reference.bind('<<ComboboxSelected>>', self.redraw_charts)
        flow(tools, gap=10)
        self.compare_bar = tk.Frame(right, background=theme.BACKGROUND)
        self.compare_bar.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        self.joint_checks = tk.Frame(self.compare_bar, background=theme.BACKGROUND)
        for name, variable in zip(JOINTS, self.compare_joints):
            ttk.Checkbutton(self.joint_checks, text=name, variable=variable,
                            command=self.redraw_charts).pack(side='left', padx=(0, 8))
        for caption, variable, values, width in (('Series', self.compare_series, ('Actual + target', 'Actual', 'Target'), 15),
                                                 ('Graphs', self.compare_layout, ('Combined', 'Separate joints'), 14),
                                                 ('Scale', self.compare_scale, ('Per joint', 'Shared'), 9)):
            combo = ttk.Combobox(group(self.compare_bar, caption), textvariable=variable, values=values,
                                 state='readonly', width=width)
            combo.pack(side='left')
            combo.bind('<<ComboboxSelected>>', self.redraw_charts)
            setattr(self, {'Series': 'series_combo', 'Graphs': 'layout_combo', 'Scale': 'scale_combo'}[caption], combo)
        flow(self.compare_bar, gap=10)
        self.compare_bar.grid_remove()
        chart_frame = tk.Frame(right, background=theme.BACKGROUND, highlightbackground=theme.BORDER, highlightthickness=1)
        chart_frame.grid(row=4, column=0, sticky='nsew', pady=(8, 0))
        self.figure = Figure(figsize=(9, 6), dpi=100, facecolor=theme.BACKGROUND)
        self.charts = FlightCharts(self.figure)
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        self.chart_viewport = tk.Canvas(chart_frame, background=theme.BACKGROUND, highlightthickness=0, height=180,
                                        yscrollincrement=1)
        self.chart_viewport.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(chart_frame, orient='vertical', command=self.scroll_chart)
        scroll.grid(row=0, column=1, sticky='ns')
        self.chart_viewport.configure(yscrollcommand=scroll.set)
        self.canvas = ChartCanvas(self.figure, master=self.chart_viewport)
        self.canvas.charts = self.charts
        self.plot_window = self.chart_viewport.create_window(0, 0, anchor='nw', window=self.canvas.get_tk_widget())
        self.chart_viewport.bind('<Configure>', self._size_plot)
        plot = self.canvas.get_tk_widget()
        plot.configure(takefocus=True)
        plot.bind('<FocusIn>', lambda _: chart_frame.configure(highlightbackground=theme.ACCENT))
        plot.bind('<FocusOut>', lambda _: chart_frame.configure(highlightbackground=theme.BORDER))
        plot.bind('<MouseWheel>', self.wheel_chart)
        plot.bind('<Button-4>', lambda e: self.scroll_chart('scroll', -48, 'units'))
        plot.bind('<Button-5>', lambda e: self.scroll_chart('scroll', 48, 'units'))
        plot.bind('<Next>', lambda e: self.scroll_chart('scroll', 1, 'pages'))
        plot.bind('<Prior>', lambda e: self.scroll_chart('scroll', -1, 'pages'))
        self.figure.text(.5, .5, 'Loading flight recordings…', color=theme.MUTED, ha='center')
        self.canvas.mpl_connect('motion_notify_event', self.hover)
        self.canvas.mpl_connect('button_press_event', self.graph_click)
        self.canvas.mpl_connect('draw_event', self.chart_drawn)
        footer = tk.Frame(right, background=theme.BACKGROUND)
        footer.grid(row=5, column=0, sticky='ew')
        for text, action in (('←', lambda: self.shift_view(-1)), ('+', lambda: self.zoom_view(.5)),
                             ('−', lambda: self.zoom_view(2)), ('→', lambda: self.shift_view(1))):
            button(footer, text, action, width=3).pack(side='left', padx=(0, 4), pady=6)
        button(footer, 'Reset zoom', self.reset_zoom).pack(side='left')
        ttk.Checkbutton(footer, text='Degrees', variable=self.degrees, command=self.redraw_charts).pack(side='left', padx=12)
        self.view_notice_label = label(footer, variable=self.view_info, muted=True, size=8, anchor='e')
        self.view_notice_label.pack(side='right')
        # Matplotlib's toolbar reserves the figure width; packed last, it takes only what is left.
        self.toolbar = NavigationToolbar2Tk(self.canvas, footer, pack_toolbar=False)
        self.toolbar.pack(side='left')
        style_toolbar(self.toolbar)
        script = tk.Frame(right, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
        script.grid(row=6, column=0, sticky='ew', pady=(6, 0))
        script.columnconfigure(0, weight=1)
        heading = tk.Frame(script, background=theme.PANEL, padx=12, pady=6)
        heading.grid(row=0, column=0, sticky='ew')
        tk.Label(heading, text='SYNCHRONIZED SCRIPT', background=theme.PANEL, foreground=theme.ACCENT,
                 font=theme.font(8, 'bold')).grid(row=0, column=0, sticky='w')
        self.script_heading = label(heading, 'No program script in this recording', size=10, bold=True)
        self.script_heading.grid(row=1, column=0, sticky='w')
        heading.columnconfigure(1, weight=1)
        label(heading, variable=self.script_position, muted=True, size=9, anchor='e').grid(row=0, column=1, rowspan=2, sticky='e', padx=10)
        ttk.Checkbutton(heading, text='Follow script', variable=self.follow_script, style='Panel.TCheckbutton',
                        command=lambda: self.show_script_line(force=True)).grid(row=0, column=2, rowspan=2)
        self.script = tk.Text(script, background=theme.BACKGROUND, foreground=theme.INK, relief='flat',
                              wrap='none', height=4, width=32, font=theme.font(10, mono=True),
                              state='disabled', padx=12, pady=6)
        self.script.grid(row=1, column=0, sticky='ew')
        style_text(self.script)
        self.script.tag_configure('number', foreground=theme.MUTED)
        self.script.tag_configure('current', foreground=theme.INK, background=theme.SELECTED)
        self.select_state_band(self.state_band.get(), redraw=False)
        self._sync_controls()

    def _resize(self, event):
        if not hasattr(self, 'status_label'):
            return
        if event.widget is self:
            self.status_label.configure(wraplength=max(600, self.winfo_width()-40))
            # Give the charts priority on short windows and the script more context on tall ones.
            lines = int(np.clip((event.height - 640) // 70, 2, 9))
            if int(self.script.cget('height')) != lines:
                self.script.configure(height=lines)

    def _discover(self, directory, generation):
        try:
            paths = [p for p in discover_flights(directory)
                     if not p.name.startswith('.') and '.part.' not in p.name and not p.is_symlink()]
            paths.extend(discover_library(directory))
            self.events.put(('discovered', generation, paths))
        except Exception as exc:
            self.events.put(('discovery_error', generation, str(exc)))

    def _refresh_choices(self):
        self.flight_combo.configure(values=[self._choice_label(path) for path in self.paths])
        if self.report and not self.loading:
            self.flight.set(self._choice_label(self.report.path))

    def _choice_label(self, path):
        path = Path(path)
        return f'{path.name} · {path.parent}' if sum(p.name == path.name for p in self.paths) > 1 else path.name

    def _update_library_summary(self):
        count = sum(path.is_relative_to(Path(self.library.get())) for path in self.paths)
        noun = 'report' if count == 1 else 'reports'
        self.library_summary.set(f'{count} {noun} in this folder · Select a report to load its measurements.')

    def refresh_library(self):
        if not self.closed:
            self.discovery_generation += 1
            self.library_summary.set('Loading reports in this folder…')
            self.executor.submit(self._discover, Path(self.library.get()), self.discovery_generation)

    def choose_folder(self):
        retrieval = getattr(self.app, 'flight_retrieval_window', None)
        if retrieval is not None and retrieval.winfo_exists() and retrieval.is_busy():
            self.status.set('Wait for retrieval to finish or cancel it before changing the flight-report folder.')
            return
        path = filedialog.askdirectory(parent=self, title='Open another flight-report folder',
                                       initialdir=self.library.get(), mustexist=True)
        if path:
            self.set_library(path)

    def set_library(self, path, *, notify=True):
        path = Path(path).resolve()
        if path == Path(self.library.get()):
            return
        self.library.set(str(path))
        self.app.flight_library_root = path
        self.generation += 1  # Discard any load from the previous folder.
        if self.future:
            self.future.cancel()
        if self.timeline:
            self.timeline.pause()
        self.report = self.timeline = None
        self.loading = False
        self.paths.clear()
        self.cache.clear()
        self.flight.set('')
        self._refresh_choices()
        for control in (self.play_button, self.restart_button, self.scrubber, self.export_button):
            control.configure(state='disabled')
        self.metadata.set('Choose a flight report from this folder.')
        self.clock.set('0.000 / 0.000 s')
        self._clear_report_views()
        self.empty('Loading reports in the selected folder…')
        update_playback = getattr(self.app, 'update_recorded_playback', None)
        if update_playback:
            update_playback(self)
        if notify:
            retrieval = getattr(self.app, 'flight_retrieval_window', None)
            if retrieval is not None and retrieval.winfo_exists():
                retrieval.set_library(path, notify=False)
        self.refresh_library()

    def add_download(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(Path(self.library.get())):
            return
        if path not in self.paths:
            self.paths.append(path)
            self._refresh_choices()
        self._update_library_summary()
        # A scan may have taken its snapshot before this download was published.
        # Retire that snapshot and queue a scan that includes the completed file.
        self.discovery_generation += 1
        self.executor.submit(self._discover, Path(self.library.get()), self.discovery_generation)
        if self.report is None and not self.loading:
            self.load_path(path)

    def open_remote_reports(self):
        if hasattr(self.app, 'open_flight_retrieval'):
            self.app.open_flight_retrieval()
            return
        from motionstudio.flight.retrieval_view import RobotFlightWindow
        window = getattr(self.app, 'flight_retrieval_window', None)
        if window is None or not window.winfo_exists():
            window = self.app.flight_retrieval_window = RobotFlightWindow(self.app)
        window.deiconify()
        window.lift()

    def select_flight(self, _event=None):
        index = self.flight_combo.current()
        if 0 <= index < len(self.paths):
            self.load_path(self.paths[index])

    def open_file(self):
        name = filedialog.askopenfilename(parent=self, title='Open flight recording', initialdir=self.library.get(),
                                          filetypes=[('Flight recordings', '*.zip *.csv'), ('All files', '*.*')])
        if name:
            self.load_path(Path(name))

    def load_path(self, path):
        path = Path(path).resolve()
        self.generation += 1
        generation = self.generation
        if self.timeline:
            self.timeline.pause()
        self.play_button.configure(text='Play', state='disabled')
        self.restart_button.configure(state='disabled')
        self.scrubber.configure(state='disabled')
        self.export_button.configure(state='disabled')
        self.loading = True
        self.flight.set(self._choice_label(path))
        self.status.set(f'Loading {path.name}…')
        if self.report is None:
            self.empty(f'Loading {path.name}…')
        if self.future:
            self.future.cancel()
        try:
            stat = path.stat()
            key = (path, stat.st_mtime_ns, stat.st_size)
        except OSError as exc:
            self.events.put(('load_error', generation, str(exc)))
            return
        if key in self.cache:
            self.cache.move_to_end(key)
            self.events.put(('loaded', generation, key, self.cache[key]))
            return
        def work():
            try:
                self.events.put(('loaded', generation, key, load_flight(path)))
            except Exception as exc:
                self.events.put(('load_error', generation, str(exc)))
        self.future = self.executor.submit(work)

    def _clear_report_views(self):
        self.full_metadata.set('')
        self.state_text.set('')
        self.sample_text.set('No recording loaded')
        self.readout.set('Hover a graph or scrub the timeline to inspect a sample.')
        self.script_position.set('Script line unavailable')
        self._set_script('')
        self.events_list = []
        self.evidence_choice.set('')
        self.evidence_combo.configure(values=(), state='disabled')
        self.evidence_title.configure(text='Recorded events')
        self.evidence_text.set('Load a recording to list its recorded stops and state changes.')
        self._trail = None
        if self.robot is not None:
            self.robot.destroy()
            self.robot = None
        self.robot_notice.configure(text='Load a recording to replay the robot here.')
        self.robot_notice.grid()
        for joint in JOINTS:
            self.joint_table.item(joint, values=('—',) * 4)
        self._update_legend()

    def _set_script(self, text):
        self.script_lines = text.splitlines()
        self.last_script_line = None
        self.script.configure(state='normal')
        self.script.delete('1.0', 'end')
        for number, line in enumerate(self.script_lines, 1):
            self.script.insert('end', f'{number:5d}  ', 'number')
            self.script.insert('end', line + '\n')
        if not self.script_lines:
            self.script.insert('end', 'This recording does not include a program script.')
        self.script.configure(state='disabled')
        self.script_heading.configure(text='polyscope.in.memory.program' if self.script_lines
                                      else 'No program script in this recording')

    def set_report(self, report):
        self.report = report
        self.timeline = PlaybackTimeline(report)
        self.speed.set('1×')
        if report.path not in self.paths:
            self.paths.append(report.path)
            self._refresh_choices()
        self.flight.set(self._choice_label(report.path))
        self._set_script(report.script)
        meta = report.metadata
        captions = (('program', 'Program'), ('serial', 'Serial'), ('software_version', 'Software'),
                    ('incident_time', 'Incident'), ('reporting_time', 'Reported'), ('recorded_at', 'Recorded'),
                    ('robot_address', 'Robot address'), ('report_type', 'Type'), ('payload_kg', 'Payload [kg]'),
                    ('notes', 'Notes'))
        detail = '\n'.join(f'{caption}: {meta[key]}' for key, caption in captions if meta.get(key))
        mounting = report.installation.mounting if report.installation else None
        detail += ('\nMounting: base {:.1f}°, tilt {:.1f}° ({})'.format(*np.degrees(mounting), report.installation.mounting_source)
                   if mounting else '\nMounting: not recorded; shown upright')
        self.metadata.set(' · '.join(filter(None, (
            report.model or 'Unknown model', meta.get('program'), f'{report.sample_count:,} samples',
            f'{report.duration:.3f} s', f"{meta['sample_rate_hz']} Hz" if meta.get('sample_rate_hz') else None))))
        self.full_metadata.set(detail + ('\n\n' if detail and report.warnings else '') + '\n'.join(report.warnings))
        self.play_button.configure(state='normal', text='Play')
        self.restart_button.configure(state='normal')
        self.scrubber.configure(to=report.duration, state='normal')
        self.export_button.configure(state='disabled' if self.exporting else 'normal')
        self.loading = False
        self._load_robot(report)
        self.mounting_controls.mounting_changed(restore=True)
        self._load_events(report)
        self.redraw_charts()
        self.update_time(0.)
        stops = len(protective_stops(report))
        self.status.set('Ready · ' + (f'{stops} recorded protective stop{"s" if stops != 1 else ""} · choose it under Recorded events to jump there.'
                                      if stops else 'No protective stop in this recording.')
                        + (' See Recording details for source notes.' if report.warnings else ''))

    def _load_robot(self, report):
        if self.robot is not None:
            self.robot.destroy()
            self.robot = None
        headers = [f'Actual TCP pose {axis} [m]' for axis in 'xyz']
        self._trail = None
        if all(header in report.columns for header in headers):
            points = np.column_stack([report.columns[header] for header in headers])
            keep = np.flatnonzero(np.isfinite(points).all(axis=1))[::max(1, report.sample_count // 800)]
            self._trail = (report.time[keep], points[keep])
        self.trail_combo.configure(state='readonly' if self._trail is not None else 'disabled')
        if not report.model:
            self.robot_notice.configure(text='Robot model unavailable in this recording; geometry cannot be inferred.')
            self.robot_notice.grid()
            return
        from motionstudio.viewer.robot_view import RobotView, recording_key
        self.robot_notice.grid_remove()
        self.robot = RobotView(self.robot_holder, report.model)
        # This recording's own mounting (or its own adjustment), never the connected robot's.
        self.robot.mounting_key = recording_key(report)
        self.robot.configure(width=200, height=160)
        self.robot.visibility['info'] = False
        self.robot.zoom = 1.7
        self.robot.set_view_frame('World (mounting)')
        self.robot.set_stream_state('Recorded joint geometry')
        self.robot.grid(row=0, column=0, sticky='nsew')

    def _load_events(self, report):
        """Protective stops first, then every other recorded state change."""
        start = float(report.timestamps[0])
        events = [(first, f'Protective stop · {first:.3f} s', 'stop') for first, _last in protective_stops(report)]
        events += [(change.elapsed, f'{change.field} → {change.label} · {change.elapsed:.3f} s', 'change')
                   for change in state_changes(report) if change.previous is not None
                   and not (change.field == 'Safety mode' and change.value == 3)]
        self.events_list = events
        stops = sum(kind == 'stop' for _elapsed, _label, kind in events)
        self.evidence_title.configure(text='Protective stop evidence' if stops else 'Recorded events')
        self.evidence_combo.configure(values=[text for _elapsed, text, _kind in events],
                                      state='readonly' if events else 'disabled')
        self.evidence_choice.set('Select stop…' if stops else 'Select event…' if events else 'No state changes recorded')
        self.evidence_text.set(f'Controller time starts at {start:.3f} s. ' + (
            'Choose a stop to move the playhead there and see what was recorded around it.' if stops else
            'No protective stop was recorded.'))

    def select_evidence(self, _event=None):
        index = self.evidence_combo.current()
        if self.report is None or not 0 <= index < len(self.events_list):
            return
        elapsed, text, kind = self.events_list[index]
        self.timeline.pause()
        self.seek(elapsed)
        self.evidence_text.set(self._evidence(elapsed) if kind == 'stop' else
                               f'{text}\nController time {self.report.timestamps[0] + elapsed:.3f} s.')
        self.evidence_combo.selection_clear()

    def _evidence(self, elapsed):
        """Recorded values around a stop; a description, never a cause."""
        report = self.report
        window = np.abs(report.time - elapsed) <= EVIDENCE_WINDOW
        lines = [f'Safety mode became Protective stop at {elapsed:.3f} s '
                 f'(controller time {report.timestamps[0] + elapsed:.3f} s).']
        number = script_line_at(report, float(report.timestamps[0] + elapsed))
        program = report.metadata.get('program')
        if number is not None or program:
            lines.append(' · '.join(filter(None, (f'Script line {number}' if number is not None else None,
                                                   f'Program {program}' if program else None))))
        for metric, caption, unit in (('current', 'Highest current', 'A'), ('velocity', 'Highest speed', 'rad/s')):
            peaks = []
            for joint in range(6):
                values = np.where(window, np.abs(report.series(metric, joint)), np.nan)
                if np.isfinite(values).any():
                    peaks.append((float(np.nanmax(values)), joint, int(np.nanargmax(values))))
            if peaks:
                _size, joint, index = max(peaks)
                lines.append(f'{caption} within ±{EVIDENCE_WINDOW} s: {JOINTS[joint]} '
                             f'{report.series(metric, joint)[index]:.3g} {unit} at {report.time[index]:.3f} s')
        lines.append('Recorded values around the stop; they do not establish its cause.')
        return '\n'.join(lines)

    def toggle_section(self, name):
        toggle, widget, caption, shown = self.sections[name]
        self.sections[name][3] = not shown
        (widget.grid_remove if shown else widget.grid)()
        toggle.configure(text=('▸ ' if shown else '▾ ') + caption)
        if self.report is not None:
            self.update_time(self.timeline.elapsed)

    def update_overlays(self, _event=None):
        if self.report is not None:
            self.update_time(self.timeline.elapsed)

    def show_simulator(self):
        if self.report is None:
            self.status.set('Load a flight recording before opening recorded playback.')
            return
        self.app.open_recorded_playback(self)
        self.withdraw()

    def _size_plot(self, _event=None):
        width = self.chart_viewport.winfo_width()
        if width < 2:
            return
        height = max(180, self.chart_viewport.winfo_height())
        if len(self.charts.axes) > 1:
            height = max(height, len(self.charts.axes) * 178 + 60)
        self.chart_viewport.itemconfigure(self.plot_window, width=width, height=height)
        self.chart_viewport.configure(scrollregion=(0, 0, width, height))

    def scroll_chart(self, *args):
        self.chart_viewport.yview(*args)
        self.canvas.refresh_pixels()
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

    def chart_drawn(self, _event=None):
        self.charts.capture_background(self.canvas)
        self.update_view_info()

    def update_view_info(self):
        if self.report is None or not self.charts.axes:
            self.view_info.set('')
            return
        low, high = self.charts.axes[0].get_xlim()
        start, end = float(self.report.timestamps[0]), float(self.report.timestamps[-1])
        span = max(end - start, 1e-9)
        self.view_info.set(f'Showing {max(low, start) - start:.2f}–{min(high, end) - start:.2f} s of {span:.2f} s')

    def refresh_theme(self):
        self.charts.refresh_theme()
        style_toolbar(self.toolbar)
        self._update_legend()
        self.canvas.draw_idle()

    def _update_legend(self):
        """Colour chips above the charts, in place of legends inside them."""
        for child in self.legend_bar.winfo_children():
            child.destroy()
        for text, color, dashed in (self.charts.legend if self.report is not None else ()):
            chip = tk.Frame(self.legend_bar, background=theme.PANEL, highlightbackground=theme.BORDER, highlightthickness=1)
            tk.Label(chip, text='┄' if dashed else '●', foreground=color, background=theme.PANEL,
                     font=theme.font(9)).pack(side='left', padx=(8, 2))
            tk.Label(chip, text=text, foreground=theme.INK, background=theme.PANEL,
                     font=theme.font(9)).pack(side='left', padx=(0, 8), pady=2)
        # Arranged once Tk has measured the new chips; flushing idle work here would run stale scale callbacks.
        self._arrange_legend()

    def select_metric(self, name):
        if self.view_mode.get() == 'Compare joints' and name == 'All readings':
            return
        self.focus.set(name)
        self.redraw_charts()

    def select_state_band(self, field, redraw=True):
        self.state_band.set('' if redraw and self.state_band.get() == field else field)
        for name, item in self.band_buttons.items():
            item.configure(style='Accent.TButton' if name == self.state_band.get() else 'TButton')
        if redraw:
            self.redraw_charts()

    def select_chart_mode(self, _event=None):
        value = self.chart_mode.get()
        self.view_mode.set('Compare joints' if value == 'Compare joints' else 'One joint')
        if value != 'Compare joints':
            self.joint.set(value.split(' · ', 1)[1])
        self.mode_combo.selection_clear()
        self.change_mode()

    def _sync_controls(self):
        comparing = self.view_mode.get() == 'Compare joints'
        self.chart_mode.set('Compare joints' if comparing else f'Joint · {self.joint.get()}')
        for name, tab in self.metric_tabs.items():
            tab.configure(style='TabActive.TButton' if name == self.focus.get() else 'Tab.TButton',
                          state='disabled' if comparing and name == 'All readings' else 'normal')
        (self.compare_bar.grid if comparing else self.compare_bar.grid_remove)()
        self.scale_combo.configure(state='readonly' if self.compare_layout.get() == 'Separate joints' else 'disabled')

    def shift_view(self, direction):
        if self.report is None or not self.charts.axes:
            return
        low, high = self.charts.axes[0].get_xlim()
        start, end = float(self.report.timestamps[0]), float(self.report.timestamps[-1])
        step = float(np.clip(direction * (high - low) * .25, start - low, end - high))
        self._set_view(low + step, high + step)

    def zoom_view(self, factor):
        """Zoom the time axis about the playhead when it is in view."""
        if self.report is None or not self.charts.axes:
            return
        low, high = self.charts.axes[0].get_xlim()
        start, end = float(self.report.timestamps[0]), float(self.report.timestamps[-1])
        center = self.charts.selected_time if low <= self.charts.selected_time <= high else (low + high) / 2
        span = float(np.clip((high - low) * factor, min(.02, end - start), max(end - start, .001)))
        low = float(np.clip(center - span * (center - low) / (high - low), start, max(start, end - span)))
        self._set_view(low, low + span)

    def _set_view(self, low, high):
        self.charts.axes[0].set_xlim(low, high)
        self.charts.backgrounds = None
        self.canvas.draw_idle()

    def change_mode(self, _event=None):
        comparing = self.view_mode.get() == 'Compare joints'
        if comparing:
            self._single_focus = self.focus.get()
            if self.focus.get() == 'All readings':
                self.focus.set('Position')
        else:
            self.focus.set(self._single_focus)
        self.redraw_charts()

    def redraw_charts(self, _event=None):
        self._sync_controls()
        if not self.report:
            return
        comparing = self.view_mode.get() == 'Compare joints'
        selected = tuple(j for j, variable in enumerate(self.compare_joints) if variable.get()) if comparing else None
        self.charts.draw(self.report, JOINTS.index(self.joint.get()), self.focus.get(),
                         compare_joints=selected, series=self.compare_series.get(),
                         separate_joints=self.compare_layout.get() == 'Separate joints',
                         shared_scale=self.compare_scale.get() == 'Shared',
                         x_reference=self.x_reference.get(), state_band=self.state_band.get() or None,
                         degrees=self.degrees.get())
        note = ''
        if self.focus.get() in ('Torque', 'All readings'):
            actual = any(self.report.source_header('torque', j) for j in range(6))
            target = any(self.report.source_header('torque', j, True) for j in range(6))
            note = ('Torque: only target recorded; actual unavailable. ' if target and not actual else
                    'Torque is shown only when explicitly recorded. ')
        self.view_notice.set(note + 'Missing channels stay unavailable.')
        self._update_legend()
        self.chart_viewport.yview_moveto(0)
        self._size_plot()
        self.toolbar.update()
        self.canvas.draw()
        self.update_time(self.timeline.elapsed)

    def reset_zoom(self):
        self.charts.reset_zoom(self.canvas)

    def hover(self, event):
        if self.timeline is None or self.loading or self.timeline.playing or self.toolbar.mode:
            return
        now = time.monotonic()
        if now - self._last_hover < .06:
            return
        self._last_hover = now
        if event.inaxes in self.charts.axes and event.xdata is not None:
            self.seek(event.xdata - self.report.timestamps[0])

    def graph_click(self, event):
        if self.timeline and not self.loading and not self.toolbar.mode and event.button == 1:
            if event.inaxes in self.charts.axes and event.xdata is not None:
                self.timeline.pause()
                self.seek(event.xdata - self.report.timestamps[0])

    def seek(self, value):
        if self.timeline:
            self.timeline.seek(float(value))
            self.update_time(self.timeline.elapsed)

    def scrub(self, value):
        if not self._setting_scale and self.timeline and not self.loading:
            # Tcl schedules a Scale command after programmatic .set(); do not
            # mistake that callback for a user drag and pause playback.
            if abs(float(value) - self.timeline.elapsed) < .0006:
                return
            self.timeline.pause()
            self.seek(value)

    def toggle_play(self):
        if self.timeline and not self.loading:
            if self.timeline.playing:
                self.timeline.pause()
            else:
                self.timeline.play()
            self._last_tick = time.monotonic()
            self.update_time(self.timeline.elapsed)

    def restart(self):
        if self.timeline and not self.loading:
            self.timeline.restart()
            self.update_time(self.timeline.elapsed)

    def change_speed(self, _event=None):
        if self.timeline:
            self.timeline.speed = float(self.speed.get().rstrip('×'))

    def update_time(self, elapsed):
        if not self.report:
            return
        report = self.report
        self._setting_scale = True
        self.scrubber.set(elapsed)
        self._setting_scale = False
        self.clock.set(f'{elapsed:.3f} / {report.duration:.3f} s')
        number = script_line_at(report, float(report.timestamps[0] + elapsed))
        self.script_position.set(f'Script line {number}' if number is not None else 'Script line unavailable')
        self.play_button.configure(text='Pause' if self.timeline.playing else 'Play')
        self.charts.move(float(report.timestamps[0] + elapsed), self.canvas)
        index = int(np.clip(np.searchsorted(report.time, elapsed), 0, report.sample_count-1))
        if index and abs(report.time[index-1] - elapsed) < abs(report.time[index] - elapsed):
            index -= 1
        self.sample_text.set(f'Sample {index+1:,} of {report.sample_count:,} · {report.timestamps[index]:.3f} s')
        self._update_robot(elapsed, index)
        comparing = self.view_mode.get() == 'Compare joints'
        joints = [j for j, variable in enumerate(self.compare_joints) if variable.get()] if comparing else [JOINTS.index(self.joint.get())]
        metrics = METRICS if self.focus.get() == 'All readings' else (self.focus.get().lower().replace(' ', '_'),)
        targets = (False, True) if not comparing or self.compare_series.get() == 'Actual + target' else (self.compare_series.get() == 'Target',)
        pieces = []
        for joint in joints:
            for metric in metrics:
                values = []
                for target in targets:
                    if not comparing and target and metric in ('temperature', 'voltage', 'control_mode') and report.source_header(metric, joint, True) is None:
                        continue
                    value = report.series(metric, joint, target)[index]
                    source = series_label(report, metric, joint, target)
                    prefix = '' if comparing and len(targets) == 1 else f'{source}: '
                    if not prefix and 'derived' in source:
                        prefix = 'Derived: '
                    values.append(prefix + (f'{value:.4g} {UNITS[metric]}' if np.isfinite(value) else 'unavailable'))
                pieces.append(f'{JOINTS[joint] if comparing else METRIC_LABELS[metric]} — ' + ', '.join(values))
        provenance = f' · {self.compare_series.get()}' if comparing else ''
        self.readout.set(f'Charted reading at this sample{provenance}\n' + '\n'.join(pieces))
        if self.sections['joints'][3]:
            degrees = self.degrees.get()
            for joint, name in enumerate(JOINTS):
                row = []
                for metric, target in (('position', False), ('position', True), ('velocity', False), ('current', False)):
                    value = report.series(metric, joint, target)[index]
                    if degrees and metric != 'current':
                        value = np.degrees(value)
                    row.append(f'{value:.4g}' if np.isfinite(value) else '—')
                self.joint_table.item(name, values=row)
            self.joint_table.heading('actual', text=f'Actual {metric_unit("position", degrees)}')
            self.joint_table.heading('velocity', text=metric_unit('velocity', degrees))
            self.joint_table.heading('current', text='A')
        callback = getattr(self.app, 'update_recorded_playback', None)
        if callback:
            callback(self)
        states = []
        for field, labels in STATE_FIELDS.items():
            values = report.columns.get(field)
            if values is not None and np.isfinite(values[index]):
                value = int(values[index])
                states.append(f'{field}: {labels.get(value, f"Unknown ({value})")}')
        self.state_text.set(' · '.join(states))
        if number != self.last_script_line or self.last_script_line is None:
            self.last_script_line = number
            self.show_script_line()

    def show_script_line(self, force=False):
        number = self.last_script_line
        self.script.tag_remove('current', '1.0', 'end')
        if number and 1 <= number <= len(self.script_lines):
            self.script.tag_add('current', f'{number}.0', f'{number + 1}.0')
            if self.follow_script.get() or force:
                # Keep one line of context above the highlighted statement.
                self.script.yview(max(number - 2, 0))

    def _update_robot(self, elapsed, index):
        if self.robot is None:
            return
        report = self.report
        self.robot.ghost_joints = None
        if self.target_overlay.get():
            target = [report.series('position', joint, True)[index] for joint in range(6)]
            if np.isfinite(target).all():
                self.robot.ghost_joints = target
        self.robot.trail = None
        if self._trail is not None and self.trail_mode.get() != 'Off':
            times, points = self._trail
            keep = (times <= elapsed) & (times >= elapsed - 5) if self.trail_mode.get() == 'Last 5 s' else slice(None)
            self.robot.trail = points[keep]
        sample = sample_at(report, elapsed)
        if sample is None:
            self.robot.grid_remove()
            self.robot_notice.configure(text='No valid recorded motion at this time (missing data or recording gap).')
            self.robot_notice.grid()
            return
        self.robot_notice.grid_remove()
        self.robot.grid()
        visible = bool(self.winfo_viewable())
        self.robot.update_robot(sample, report.installation, render=False)
        if visible:
            self.robot.redraw()

    def export_pdf(self):
        if self.report is None or self.loading or self.exporting:
            return
        report = self.report
        path = filedialog.asksaveasfilename(parent=self, title='Export complete flight report',
                                            defaultextension='.pdf', initialfile=f'{report.path.stem}_report.pdf',
                                            initialdir=start_folder(self.app, 'reports'),
                                            filetypes=[('PDF report', '*.pdf')])
        if not path:
            return
        self.exporting = True
        self.export_button.configure(state='disabled')
        self.status.set(f'Exporting complete recording: {report.name}…')
        def work():
            try:
                from motionstudio.flight.pdf import export_flight_pdf
                export_flight_pdf(report, path)
                self.events.put(('exported', str(path)))
            except Exception as exc:
                self.events.put(('export_error', str(exc)))
        self.executor.submit(work)

    def poll(self):
        if self.closed:
            return
        while not self.events.empty():
            event = self.events.get_nowait()
            if event[0] == 'discovered' and event[1] == self.discovery_generation:
                self.paths = list(dict.fromkeys(Path(p).resolve() for p in event[2]))
                self._update_library_summary()
                self._refresh_choices()
                if self.paths and self.report is None and not self.loading:
                    self.load_path(self.paths[0])
                elif not self.paths and self.report is None and not self.loading:
                    self.empty('No flight recordings in this project yet. Download or record one, or choose Folder… to open another folder.')
            elif event[0] == 'discovery_error' and event[1] == self.discovery_generation:
                self.library_summary.set('Could not read this folder · Choose another folder or reload.')
                self.status.set(f'Could not find recordings: {event[2]}')
            elif event[0] == 'loaded' and event[1] == self.generation:
                self.cache[event[2]] = event[3]
                while len(self.cache) > 3:
                    self.cache.popitem(last=False)
                self.set_report(event[3])
            elif event[0] == 'load_error' and event[1] == self.generation:
                self.loading = False
                if self.report:
                    self.flight.set(self._choice_label(self.report.path))
                    self.play_button.configure(state='normal')
                    self.restart_button.configure(state='normal')
                    self.scrubber.configure(state='normal')
                    self.export_button.configure(state='disabled' if self.exporting else 'normal')
                    self.status.set(f'Could not load recording: {event[2]} · Previous flight retained.')
                else:
                    self.empty(f'Could not load recording: {event[2]}')
            elif event[0] in ('exported', 'export_error'):
                self.exporting = False
                self.export_button.configure(state='normal' if self.report and not self.loading else 'disabled')
                self.status.set(('PDF saved: ' if event[0] == 'exported' else 'PDF export failed: ') + event[1])
        now = time.monotonic()
        delta = now - self._last_tick
        self._last_tick = now
        visible = self.winfo_viewable() or (self.app.is_recording_visible(self) if hasattr(self.app, 'is_recording_visible')
                                           else getattr(self.app, 'recorded_source', None) is self)
        if self.timeline and self.timeline.playing and not self.loading and visible:
            self.timeline.tick(delta)
            self.update_time(self.timeline.elapsed)
        self.after_id = self.after(30, self.poll)

    def empty(self, text):
        self.status.set(text)
        self.figure.clear()
        self.charts.report = None
        self.charts.axes = []
        self.charts.playheads = []
        self.charts.backgrounds = None
        self.figure.text(.5, .5, text, color=theme.MUTED, ha='center', wrap=True)
        self.canvas.draw_idle()

    def hide(self):
        if self.timeline:
            self.timeline.pause()
            self.update_time(self.timeline.elapsed)
        self.withdraw()
        self.app.root.lift()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.after_cancel(self.after_id)
        for name in ('_repaint_id', '_idle_draw_id', '_event_loop_id'):
            callback = getattr(self.canvas, name, None)
            if callback is not None:
                self.canvas.get_tk_widget().after_cancel(callback)
                setattr(self.canvas, name, None)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
