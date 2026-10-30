"""Recorded-flight workspace using the same native renderer as the live viewer."""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk, filedialog

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from flight_charts import (FlightCharts, JOINTS, METRICS, METRIC_LABELS, UNITS, BACKGROUND, PANEL, GRID,
                           TEXT, MUTED, serialized_render, series_label, script_line_at)
from flight_data import discover_flights, load_flight
from flight_motion import PlaybackTimeline
from flight_transfer import default_library, discover_library
from ui_theme import ACCENT, TARGET, SELECTED, style_text, style_toolbar, status_color


class FlightCanvas(FigureCanvasTkAgg):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.get_tk_widget().configure(background=BACKGROUND, highlightthickness=0, borderwidth=0)
        self._repaint_id = None
        self.get_tk_widget().bind('<Expose>', self.refresh_pixels, add='+')

    def resize(self, event):
        # Moving the embedded window while scrolling also emits Configure.
        # Rebuilding the PhotoImage at unchanged dimensions flashes blank strips.
        if (event.width, event.height) == (self._tkphoto.width(), self._tkphoto.height()):
            self.refresh_pixels()
            return
        return super().resize(event)

    def refresh_pixels(self, _event=None):
        """Repair newly exposed Tk pixels from Agg, without rendering traces again."""
        if self._repaint_id is None:
            self._repaint_id = self.get_tk_widget().after_idle(self._refresh_pixels)

    @serialized_render
    def _refresh_pixels(self):
        self._repaint_id = None
        if self.get_tk_widget().winfo_exists() and hasattr(self, 'renderer'):
            self.blit()

    @serialized_render
    def draw(self):
        charts = getattr(self, 'charts', None)
        if charts is not None and charts.axes:
            # Set margins before drawing, including Tk's idle resize draws.
            # Scheduling another draw from draw_event can be coalesced away.
            charts._layout()
        return super().draw()


def label(parent, text=None, variable=None, *, muted=False, size=10, bold=False, **kwargs):
    kwargs.setdefault('anchor', 'w')
    return tk.Label(parent, text=text, textvariable=variable, background=parent.cget('background'),
                    foreground=MUTED if muted else TEXT, font=('TkDefaultFont', size, 'bold' if bold else 'normal'),
                    **kwargs)


def button(parent, text, command, *, accent=False, **kwargs):
    return ttk.Button(parent, text=text, command=command,
                      style='Accent.TButton' if accent else 'TButton', **kwargs)


class FlightWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, background=BACKGROUND)
        self.app = app
        self.title('UR Motion Studio · Flight reports')
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
        self._build()
        self.after_id = self.after(30, self.poll)
        self.refresh_library()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        heading = tk.Frame(self, background=PANEL, padx=20, pady=12)
        heading.grid(row=0, column=0, sticky='ew')
        label(heading, 'FLIGHT REPORTS', size=15, bold=True).pack(side='left')
        button(heading, 'How to', lambda: self.app.open_help('Flight reports')).pack(side='left', padx=14)
        button(heading, 'Open simulator', self.show_simulator).pack(side='right')
        self.export_button = button(heading, 'Export measurements…', self.export_pdf, accent=True, state='disabled')
        self.export_button.pack(side='right', padx=10)
        button(heading, 'Program Check…', lambda: self.app.open_program_check()).pack(side='right', padx=8)
        button(heading, 'Retrieve…', self.open_remote_reports).pack(side='right')
        self.body = tk.Frame(self, background=BACKGROUND, padx=12, pady=10)
        self.body.grid(row=1, column=0, sticky='nsew')
        self.body.columnconfigure(0, weight=1)
        self.body.rowconfigure(0, weight=1)
        self.pages = ttk.Notebook(self.body)
        self.pages.grid(row=0, column=0, sticky='nsew')
        chart_area = tk.Frame(self.pages, background=BACKGROUND, padx=8, pady=8)
        self.pages.add(chart_area, text='Graphs')
        self.chart_area = chart_area
        chart_area.bind('<Configure>', self._resize)
        chart_area.columnconfigure(0, weight=1)
        chart_area.rowconfigure(3, weight=1)
        files = tk.Frame(chart_area, background=PANEL, padx=10, pady=9)
        files.grid(row=0, column=0, sticky='ew')
        files.columnconfigure(1, weight=1)
        label(files, 'Flight report', bold=True).grid(row=0, column=0, padx=(0, 10))
        self.flight_combo = ttk.Combobox(files, textvariable=self.flight, state='readonly',
                                         width=33)
        self.flight_combo.grid(row=0, column=1, sticky='ew')
        self.flight_combo.bind('<<ComboboxSelected>>', self.select_flight)
        button(files, 'Open…', self.open_file).grid(row=0, column=2, padx=(10, 0))
        label(files, 'Working folder', bold=True).grid(row=1, column=0, sticky='w', pady=(8, 0), padx=(0, 10))
        ttk.Entry(files, textvariable=self.library, state='readonly').grid(row=1, column=1, sticky='ew', pady=(8, 0))
        button(files, 'Choose folder…', self.choose_folder).grid(row=1, column=2, padx=(10, 0), pady=(8, 0))
        button(files, 'Reload folder', self.refresh_library).grid(row=2, column=2, padx=(10, 0), pady=(6, 0))
        self.library_summary = tk.StringVar(value='Loading reports in this folder…')
        label(files, variable=self.library_summary, muted=True, size=9).grid(row=2, column=0, columnspan=2, sticky='w', pady=(6, 0))
        self.metadata_label = label(chart_area, variable=self.metadata, muted=True, size=9,
                                    justify='left', wraplength=750)
        self.metadata_label.grid(row=1, column=0, sticky='ew', pady=10)
        filters = tk.Frame(chart_area, background=BACKGROUND)
        filters.grid(row=2, column=0, sticky='ew', pady=(0, 5))
        row = tk.Frame(filters, background=BACKGROUND)
        row.pack(fill='x')
        label(row, 'Mode').pack(side='left', padx=(0, 6))
        self.mode_combo = ttk.Combobox(row, textvariable=self.view_mode,
                values=('One joint', 'Compare joints'), state='readonly', width=14)
        self.mode_combo.pack(side='left')
        self.mode_combo.bind('<<ComboboxSelected>>', self.change_mode)
        label(row, 'Joint').pack(side='left', padx=(12, 6))
        self.joint_combo = ttk.Combobox(row, textvariable=self.joint, values=JOINTS, state='readonly', width=10)
        self.joint_combo.pack(side='left')
        self.joint_combo.bind('<<ComboboxSelected>>', self.redraw_charts)
        button(row, 'Reset zoom', self.reset_zoom).pack(side='right')
        row = tk.Frame(filters, background=BACKGROUND)
        row.pack(fill='x', pady=(6, 0))
        label(row, 'View').pack(side='left', padx=(0, 6))
        self.focus_combo = ttk.Combobox(row, textvariable=self.focus, state='readonly', width=15,
                values=('All readings', *METRIC_LABELS.values()))
        self.focus_combo.pack(side='left')
        self.focus_combo.bind('<<ComboboxSelected>>', self.redraw_charts)
        label(row, 'Series').pack(side='left', padx=(12, 6))
        self.series_combo = ttk.Combobox(row, textvariable=self.compare_series, width=17,
                values=('Actual + target', 'Actual', 'Target'), state='disabled')
        self.series_combo.pack(side='left')
        self.series_combo.bind('<<ComboboxSelected>>', self.redraw_charts)
        label(row, 'Graphs').pack(side='left', padx=(12, 6))
        self.layout_combo = ttk.Combobox(row, textvariable=self.compare_layout, width=16,
                values=('Combined', 'Separate joints'), state='disabled')
        self.layout_combo.pack(side='left')
        self.layout_combo.bind('<<ComboboxSelected>>', self.redraw_charts)
        label(row, 'Scale').pack(side='left', padx=(12, 6))
        self.scale_combo = ttk.Combobox(row, textvariable=self.compare_scale, width=10,
                values=('Per joint', 'Shared'), state='disabled')
        self.scale_combo.pack(side='left')
        self.scale_combo.bind('<<ComboboxSelected>>', self.redraw_charts)
        self.joint_checks = tk.Frame(filters, background=BACKGROUND)
        self.joint_checks.pack(fill='x', pady=(6, 0))
        for column, (name, variable) in enumerate(zip(JOINTS, self.compare_joints)):
            ttk.Checkbutton(self.joint_checks, text=name, variable=variable,
                            command=self.redraw_charts).grid(row=0, column=column, sticky='w')
            self.joint_checks.columnconfigure(column, weight=1)
        self.joint_checks.pack_forget()
        self.view_notice_label = label(filters, variable=self.view_notice, muted=True, size=8,
                                       wraplength=750, justify='left')
        self.view_notice_label.pack(fill='x', pady=(6, 0))
        chart_frame = tk.Frame(chart_area, background=BACKGROUND, highlightbackground=GRID, highlightthickness=1)
        chart_frame.grid(row=3, column=0, sticky='nsew')
        self.figure = Figure(figsize=(9, 6), dpi=100, facecolor=BACKGROUND)
        self.charts = FlightCharts(self.figure)
        chart_frame.columnconfigure(0, weight=1)
        chart_frame.rowconfigure(0, weight=1)
        self.chart_viewport = tk.Canvas(chart_frame, background=BACKGROUND, highlightthickness=0, height=180,
                                        yscrollincrement=1)
        self.chart_viewport.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(chart_frame, orient='vertical', command=self.scroll_chart)
        scroll.grid(row=0, column=1, sticky='ns')
        self.chart_viewport.configure(yscrollcommand=scroll.set)
        self.canvas = FlightCanvas(self.figure, master=self.chart_viewport)
        self.canvas.charts = self.charts
        self.plot_window = self.chart_viewport.create_window(0, 0, anchor='nw', window=self.canvas.get_tk_widget())
        self.chart_viewport.bind('<Configure>', self._size_plot)
        plot = self.canvas.get_tk_widget()
        plot.configure(takefocus=True)
        plot.bind('<FocusIn>', lambda _: chart_frame.configure(highlightbackground=ACCENT))
        plot.bind('<FocusOut>', lambda _: chart_frame.configure(highlightbackground=GRID))
        plot.bind('<MouseWheel>', self.wheel_chart)
        plot.bind('<Button-4>', lambda e: self.scroll_chart('scroll', -48, 'units'))
        plot.bind('<Button-5>', lambda e: self.scroll_chart('scroll', 48, 'units'))
        plot.bind('<Next>', lambda e: self.scroll_chart('scroll', 1, 'pages'))
        plot.bind('<Prior>', lambda e: self.scroll_chart('scroll', -1, 'pages'))
        self.line_axis = tk.Canvas(chart_frame, background=BACKGROUND, height=42, highlightthickness=0)
        self.line_axis.grid(row=1, column=0, sticky='ew')
        self.line_axis.bind('<Configure>', lambda _: self.draw_line_axis())
        self.figure.text(.5, .5, 'Loading flight recordings…', color=MUTED, ha='center')
        self.canvas.mpl_connect('motion_notify_event', self.hover)
        self.canvas.mpl_connect('button_press_event', self.graph_click)
        self.canvas.mpl_connect('draw_event', self.chart_drawn)
        self.toolbar = NavigationToolbar2Tk(self.canvas, chart_area, pack_toolbar=False)
        self.toolbar.grid(row=4, column=0, sticky='ew')
        style_toolbar(self.toolbar)
        details = tk.Frame(self.pages, background=BACKGROUND, padx=18, pady=18)
        self.pages.add(details, text='Sample details')
        label(details, 'SELECTED SAMPLE', bold=True).pack(anchor='w', pady=(0, 12))
        self.readout_label = label(details, variable=self.readout, size=10, justify='left', wraplength=900)
        self.readout_label.pack(fill='x', anchor='n')
        details.bind('<Configure>', lambda e: self.readout_label.configure(wraplength=max(300, e.width-40)))
        self.side = tk.Frame(self.pages, background=PANEL, padx=18, pady=18)
        self.pages.add(self.side, text='Recording & script')
        self.side.columnconfigure(0, weight=1)
        self.side.rowconfigure(4, weight=1)
        self.full_metadata = tk.StringVar()
        label(self.side, variable=self.full_metadata, size=10, justify='left', wraplength=900).grid(row=0, column=0, sticky='ew', pady=(0, 14))
        label(self.side, variable=self.state_text, size=10, justify='left').grid(row=1, column=0, sticky='ew', pady=(0, 14))
        label(self.side, 'SYNCHRONIZED SCRIPT', muted=True, size=10, bold=True).grid(row=2, column=0, sticky='w', pady=(10, 5))
        self.script_heading = label(self.side, 'No recorded script line', muted=True, size=9)
        self.script_heading.grid(row=3, column=0, sticky='w', pady=(0, 8))
        self.script = tk.Text(self.side, background=BACKGROUND, foreground=TEXT, relief='flat',
                              wrap='word', height=8, width=32, font=('TkFixedFont', 10),
                              state='disabled', padx=12, pady=12)
        self.script.grid(row=4, column=0, sticky='nsew')
        style_text(self.script)
        self.script.tag_configure('current', foreground=TARGET, background=SELECTED)
        controls = tk.Frame(self, background=PANEL, padx=16, pady=10)
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
                                 showvalue=False, command=self.scrub, background=PANEL, foreground=TEXT,
                                 troughcolor=BACKGROUND, highlightthickness=1, sliderlength=15,
                                 highlightbackground=PANEL, highlightcolor=ACCENT, takefocus=True,
                                 activebackground=ACCENT, borderwidth=0, state='disabled')
        self.scrubber.grid(row=0, column=3, sticky='ew')
        label(controls, variable=self.script_position, size=10, width=25, anchor='e').grid(row=0, column=4, padx=(10, 0))
        self.status_label = label(self, variable=self.status, muted=True, size=9, padx=16, pady=7,
                                  wraplength=1350, justify='left')
        self.status_label.grid(row=3, column=0, sticky='ew')
        self._status_trace = self.status.trace_add('write', lambda *_:
            self.status_label.configure(foreground=status_color(self.status.get())))
        self.bind('<Configure>', self._resize)

    def _resize(self, event):
        if not hasattr(self, 'status_label'):
            return
        if event.widget in (self, self.chart_area):
            width = max(350, self.chart_area.winfo_width() - 8)
            self.metadata_label.configure(wraplength=width)
            self.readout_label.configure(wraplength=width)
            self.view_notice_label.configure(wraplength=width)
            self.status_label.configure(wraplength=max(600, self.winfo_width()-40))

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
            self.status.set('Wait for retrieval to finish or cancel it before changing the working folder.')
            return
        path = filedialog.askdirectory(parent=self, title='Choose flight-report working folder',
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
        self.metadata.set('Choose a flight report from this working folder.')
        self.full_metadata.set('')
        self.state_text.set('')
        self.script.configure(state='normal')
        self.script.delete('1.0', 'end')
        self.script.configure(state='disabled')
        self.script_heading.configure(text='No recorded script line')
        self.clock.set('0.000 / 0.000 s')
        self.script_position.set('Script line unavailable')
        self.readout.set('Hover a graph or scrub the timeline to inspect a sample.')
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
        from flight_retrieval_view import RobotFlightWindow
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
        name = filedialog.askopenfilename(parent=self, title='Open flight recording',
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

    def set_report(self, report):
        self.report = report
        self.timeline = PlaybackTimeline(report)
        self.speed.set('1×')
        if report.path not in self.paths:
            self.paths.append(report.path)
            self._refresh_choices()
        self.flight.set(self._choice_label(report.path))
        self.script_lines = report.script.splitlines()
        self.last_script_line = None
        meta = report.metadata
        detail = ' · '.join(f'{key.replace("_", " ").title()}: {value}' for key, value in meta.items()
                            if key in ('program', 'serial', 'software_version', 'incident_time', 'reporting_time'))
        self.metadata.set(f'{report.model or "Unknown model"} · {report.sample_count:,} samples · '
                          f'{report.duration:.3f} s')
        self.full_metadata.set(detail.replace(' · ', '\n') + '\n\n' + '\n'.join(report.warnings))
        self.play_button.configure(state='normal', text='Play')
        self.restart_button.configure(state='normal')
        self.scrubber.configure(to=report.duration, state='normal')
        self.export_button.configure(state='disabled' if self.exporting else 'normal')
        self.loading = False
        self.redraw_charts()
        self.update_time(0.)
        suffix = ' · '.join(report.warnings)
        self.status.set('Ready · Red bands mark recorded protective stops. Open simulator for full-size playback.' + (' See Recording & script for source warnings.' if suffix else ''))

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
            height = max(height, len(self.charts.axes) * 145 + 70)
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
        self.draw_line_axis()

    def draw_line_axis(self):
        if not hasattr(self, 'line_axis'):
            return
        axis = self.line_axis
        axis.delete('all')
        width = axis.winfo_width()
        if not self.charts.axes or self.report is None:
            axis.create_text(width/2, 24, text='Script line unavailable', fill=MUTED, font=('TkDefaultFont', 9))
            return
        plot = self.charts.axes[0]
        low, high = plot.get_xlim()
        left, right = plot.get_position().x0 * width, plot.get_position().x1 * width
        axis.create_line(left, 1, right, 1, fill=GRID)
        for timestamp in plot.get_xticks():
            if low <= timestamp <= high and high > low:
                x = left + (timestamp-low) / (high-low) * (right-left)
                number = script_line_at(self.report, timestamp)
                axis.create_line(x, 1, x, 5, fill=MUTED)
                axis.create_text(x, 13, text=str(number) if number is not None else '—', fill=MUTED, font=('TkDefaultFont', 8))
        axis.create_text(width/2, 32, text='Script line number · recorded source', fill=MUTED, font=('TkDefaultFont', 9))

    def change_mode(self, _event=None):
        comparing = self.view_mode.get() == 'Compare joints'
        if comparing:
            self._single_focus = self.focus.get()
            if self.focus.get() == 'All readings':
                self.focus.set('Position')
            self.focus_combo.configure(values=tuple(METRIC_LABELS.values()))
            self.joint_checks.pack(fill='x', pady=(6, 0), before=self.view_notice_label)
        else:
            self.focus_combo.configure(values=('All readings', *METRIC_LABELS.values()))
            self.focus.set(self._single_focus)
            self.joint_checks.pack_forget()
        self.joint_combo.configure(state='disabled' if comparing else 'readonly')
        self.series_combo.configure(state='readonly' if comparing else 'disabled')
        self.layout_combo.configure(state='readonly' if comparing else 'disabled')
        self.redraw_charts()

    def redraw_charts(self, _event=None):
        if not self.report:
            return
        comparing = self.view_mode.get() == 'Compare joints'
        self.scale_combo.configure(state='readonly' if comparing and self.compare_layout.get() == 'Separate joints' else 'disabled')
        selected = tuple(j for j, variable in enumerate(self.compare_joints) if variable.get()) if comparing else None
        self.charts.draw(self.report, JOINTS.index(self.joint.get()), self.focus.get(),
                         compare_joints=selected, series=self.compare_series.get(),
                         separate_joints=self.compare_layout.get() == 'Separate joints',
                         shared_scale=self.compare_scale.get() == 'Shared')
        note = ('Joint colors stay fixed; solid = actual, dashed = target. ' if comparing else
                'Blue = actual; green = target. Scroll for all readings. ')
        if comparing and self.compare_layout.get() == 'Separate joints':
            note += ('Shared value scales. ' if self.compare_scale.get() == 'Shared' else 'Each joint has its own value scale. ') + 'Scroll for all joints. '
        if self.focus.get() in ('Torque', 'All readings'):
            actual = any(self.report.source_header('torque', j) for j in range(6))
            target = any(self.report.source_header('torque', j, True) for j in range(6))
            note += ('Torque: only target recorded; actual unavailable. ' if target and not actual else
                     'Torque is shown only when explicitly recorded. ')
        self.view_notice.set(note + 'Missing channels stay unavailable.')
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
        self.readout.set(f'Nearest sample {index+1:,} · {report.timestamps[index]:.3f} s{provenance}\n' + '\n\n'.join(pieces))
        callback = getattr(self.app, 'update_recorded_playback', None)
        if callback:
            callback(self)
        states = []
        for field in ('Robot mode', 'Safety mode', 'Program state'):
            values = report.columns.get(field)
            if values is not None and np.isfinite(values[index]):
                value = int(values[index])
                note = ' · Protective stop' if field == 'Safety mode' and value == 3 else ''
                states.append(f'{field}: {value}{note}')
        self.state_text.set('\n'.join(states))
        if number != self.last_script_line or self.last_script_line is None:
            self.last_script_line = number
            self.script_heading.configure(text=f'Recorded script line {number}' if number is not None else 'Script line not recorded')
            self.script.configure(state='normal')
            self.script.delete('1.0', 'end')
            if number and 1 <= number <= len(self.script_lines):
                for n in range(max(1, number-2), min(len(self.script_lines), number+3)+1):
                    self.script.insert('end', f'{n:4d}  {self.script_lines[n-1]}\n', 'current' if n == number else '')
            else:
                self.script.insert('end', 'No matching source line in the recorded script.')
            self.script.configure(state='disabled')

    def export_pdf(self):
        if self.report is None or self.loading or self.exporting:
            return
        report = self.report
        path = filedialog.asksaveasfilename(parent=self, title='Export complete flight report',
                                            defaultextension='.pdf', initialfile=f'{report.path.stem}_report.pdf',
                                            filetypes=[('PDF report', '*.pdf')])
        if not path:
            return
        self.exporting = True
        self.export_button.configure(state='disabled')
        self.status.set(f'Exporting complete recording: {report.name}…')
        def work():
            try:
                from flight_pdf import export_flight_pdf
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
                    self.empty('No flight recordings in this folder. Choose folder… to find your reports.')
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
        if self.timeline and self.timeline.playing and not self.loading and (self.state() != 'withdrawn' or getattr(self.app, 'recorded_source', None) is self):
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
        self.figure.text(.5, .5, text, color=MUTED, ha='center', wrap=True)
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
        if self.canvas._repaint_id is not None:
            self.canvas.get_tk_widget().after_cancel(self.canvas._repaint_id)
            self.canvas._repaint_id = None
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
