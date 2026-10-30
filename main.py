"""Launch the read-only UR robot viewer with python main.py."""
import math
import queue
import time
import tkinter as tk
from tkinter import ttk
import ui_dialogs as messagebox
from tkinter.scrolledtext import ScrolledText

from connection import make_settings
from kinematics import MODELS, VIEW_FRAMES
from session import RobotSession
from view import RobotView, DISPLAY_OPTIONS
from tooling import TOOLS, TCP_SOURCES
from status_view import StatusView
from telemetry import RUNTIME_STATES, enum_text, script_line_text
from model_catalog import model_summary, show_catalog
from ui_theme import FIELD, INK, WARNING, DANGER, ScrollableFrame, style_text, status_color

PAYLOAD_NOTICE = "Payload info will not be loaded without an SSH password. Blank username uses root."
DEFAULT_IP = "10.22.33.81"
DEFAULT_USERNAME = "root"


class HintEntry(ttk.Entry):
    """Display a hint without inserting it into the entry's actual value."""
    def __init__(self, parent, *, textvariable, hint="", **kwargs):
        super().__init__(parent, textvariable=textvariable, **kwargs)
        self.variable = textvariable
        self.hint = ttk.Label(self, text=hint, style="Hint.TLabel", cursor="xterm")
        self.hint.bind("<Button-1>", lambda _: self.focus_set())
        self.trace = textvariable.trace_add("write", self.update_hint)
        self.update_hint()

    def update_hint(self, *_):
        if self.variable.get():
            self.hint.place_forget()
        else:
            self.hint.place(x=11, rely=.5, anchor="w")

    def destroy(self):
        self.variable.trace_remove("write", self.trace)
        super().destroy()

class SimulatorApp:
    def __init__(self, root):
        self.root = root
        self.log_window = None
        self.flight_window = None
        self.flight_retrieval_window = None
        self.help_window = None
        self.program_check_window = None
        self.recorded_panel = None
        self.recorded_source = None
        self.simulator_tab = None
        self.simulator_source = tk.StringVar(value='Live robot')
        for sequence, action in (('<F1>', self.open_help),
                                 ('<Control-Key-1>', self.show_workspace),
                                 ('<Control-Key-2>', self.open_logs),
                                 ('<Control-Key-3>', self.open_flights),
                                 ('<Control-Key-4>', self.open_program_check)):
            root.bind_all(sequence, lambda event, action=action: self.workspace_shortcut(action), add='+')
        self.session = None
        self.settings = None
        self.installation_data = None
        self.last_sample = None
        self.last_status_update = 0.
        self.program_text = tk.StringVar(value="Waiting for program telemetry…")
        from ui_theme import apply_theme
        apply_theme(root)
        root.title("UR Motion Studio")
        root.geometry("700x760")
        root.minsize(660, 720)
        self.ip = tk.StringVar()
        self.username = tk.StringVar()
        self.password = tk.StringVar()
        self.installation = tk.StringVar()
        self.directory = tk.StringVar()
        self.model = tk.StringVar(value="UR15")
        self.model_details = tk.StringVar(value=model_summary(self.model.get()))
        self.model.trace_add('write', lambda *_: self.model_details.set(model_summary(self.model.get())))
        self.view_frame = tk.StringVar(value=VIEW_FRAMES[0])
        self.display_options = {key: tk.BooleanVar(value=(key == "grid")) for key in DISPLAY_OPTIONS}
        self.tool = tk.StringVar(value="None")
        self.tool_tcp_source = tk.StringVar(value="Installation TCP")
        self.gripper_opening = tk.DoubleVar(value=.5)
        self.notice = tk.StringVar(value=PAYLOAD_NOTICE)
        self.error = tk.StringVar()
        self.status = tk.StringVar(value="Disconnected")
        self.status_label = None
        self.status.trace_add('write', self.update_status_style)
        self.details = tk.StringVar()
        self.details_view = None
        self.details.trace_add("write", self.update_details)
        self.joints = tk.StringVar()
        self.tcp_text = tk.StringVar()
        self.username.trace_add("write", self.update_notice)
        self.password.trace_add("write", self.update_notice)
        self.closed = False
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.show_setup()
        self.poll_id = self.root.after(33, self.poll)

    def clear(self):
        if self.recorded_source and self.recorded_source.timeline:
            self.recorded_source.timeline.pause()
        self.recorded_panel = self.recorded_source = None
        self.simulator_tab = None
        self.details_view = None
        self.status_label = None
        for widget in self.root.winfo_children():
            if widget not in (self.log_window, self.flight_window, self.flight_retrieval_window, self.help_window, self.program_check_window):
                widget.destroy()

    def update_status_style(self, *_):
        if self.status_label is not None:
            self.status_label.configure(foreground=status_color(self.status.get()))

    def update_details(self, *_):
        if self.details_view is not None:
            self.details_view.configure(state="normal")
            self.details_view.delete("1.0", "end")
            self.details_view.insert("1.0", self.details.get())
            self.details_view.configure(state="disabled")

    def update_notice(self, *_):
        available = self.password.get()
        self.notice.set("" if available else PAYLOAD_NOTICE)

    def show_setup(self):
        self.clear()
        self.root.geometry("1000x780")
        self.root.minsize(760, 720)
        heading = ttk.Frame(self.root, padding=(22, 16), style='Toolbar.TFrame')
        heading.pack(fill='x')
        ttk.Label(heading, text='UR MOTION STUDIO', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(heading, text='How to', command=self.open_help).pack(side='right', padx=(14, 0))
        ttk.Label(heading, text='ROBOT CONNECTION', style='Section.Panel.TLabel').pack(side='right')
        shell = ttk.Frame(self.root, padding=(22, 20))
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=3)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(0, weight=1)
        panel = ttk.Frame(shell, padding=20, style='Card.TFrame')
        panel.grid(row=0, column=0, sticky='nsew', padx=(0, 16))
        panel.columnconfigure(1, weight=1)
        ttk.Label(panel, text="Connect to your robot", style="Title.Panel.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        intro = ttk.Label(panel, text="Live robot visualization and log analysis in one desktop app.",
                          style='Muted.Panel.TLabel', wraplength=430)
        intro.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))
        fields = [
            ("Robot IP address", self.ip, DEFAULT_IP), ("Username", self.username, DEFAULT_USERNAME),
            ("Password (optional)", self.password, ""), ("Installation name", self.installation, "default"),
            ("Installation folder", self.directory, "/programs"),
        ]
        self.setup_entries = {}
        for row, (label, variable, hint) in enumerate(fields, start=2):
            ttk.Label(panel, text=label, style='Panel.TLabel').grid(row=row, column=0, sticky="w", padx=(0, 14), pady=7)
            entry = HintEntry(panel, textvariable=variable, hint=hint,
                              show="*" if variable is self.password else "")
            entry.grid(row=row, column=1, sticky="ew", pady=7)
            self.setup_entries[str(variable)] = entry
            entry.bind('<KeyRelease>', lambda _event, item=entry: item.state(['!invalid']))
            if variable is self.ip:
                entry.focus_set()
        ttk.Label(panel, text="Robot model", style='Panel.TLabel').grid(row=7, column=0, sticky="w", pady=7)
        ttk.Combobox(panel, textvariable=self.model, values=tuple(MODELS), state="readonly").grid(
            row=7, column=1, sticky="ew", pady=8)
        model_info = ttk.Frame(panel, style='Panel.TFrame')
        model_info.grid(row=8, column=0, columnspan=2, sticky='ew', pady=10)
        model_detail = ttk.Label(model_info, textvariable=self.model_details, style='Muted.Panel.TLabel', wraplength=430)
        model_detail.pack(anchor='w', fill='x')
        ttk.Button(model_info, text='Compare robot sizes', command=lambda: show_catalog(self.root, self.model)).pack(anchor='w', pady=(10, 0))
        notice = ttk.Label(panel, textvariable=self.notice, wraplength=430, foreground=WARNING, style='Panel.TLabel')
        notice.grid(
            row=9, column=0, columnspan=2, sticky="w", pady=8)
        error = ttk.Label(panel, textvariable=self.error, wraplength=430, foreground=DANGER, style='Panel.TLabel')
        error.grid(
            row=10, column=0, columnspan=2, sticky="w", pady=8)
        ttk.Button(panel, text="Connect to robot", command=self.submit, style="Accent.TButton").grid(
            row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        offline = ttk.Frame(shell, padding=16, style='Card.TFrame')
        offline.grid(row=0, column=1, sticky='nsew')
        ttk.Label(offline, text='OFFLINE ANALYSIS', style='Section.Panel.TLabel').pack(anchor='w', pady=(0, 12))
        offline_hint = ttk.Label(offline, text='Explore recorded data without a robot connection.',
                                style='Muted.Panel.TLabel', wraplength=210)
        offline_hint.pack(anchor='w', fill='x', pady=(0, 18))
        ttk.Button(offline, text="Logs & graphs", command=self.open_logs).pack(fill='x', pady=(0, 10))
        ttk.Button(offline, text="Flight reports & playback", command=self.open_flights).pack(fill='x')
        ttk.Button(offline, text="Program Check & PDF", command=self.open_program_check).pack(fill="x", pady=(10, 0))
        ttk.Separator(offline).pack(fill='x', pady=20)
        offline_note = ttk.Label(offline, text='Open saved logs or flight recordings. Inspect measurements, replay motion and export reports.',
                                style='Muted.Panel.TLabel', wraplength=210)
        offline_note.pack(anchor='w', fill='x')
        ttk.Label(offline, text='Keyboard navigation\nCtrl+1  Simulator / connection\nCtrl+2  Logs & graphs\nCtrl+3  Flight reports\nCtrl+4  Program Check\nF1  Help',
                  style='Muted.Panel.TLabel').pack(anchor='w', pady=(20, 0))
        def resize_setup(event):
            if event.widget is panel:
                width = max(240, event.width - 40)
                for item in (intro, model_detail, notice, error):
                    item.configure(wraplength=width)
        panel.bind('<Configure>', resize_setup)
        offline.bind('<Configure>', lambda e: [item.configure(wraplength=max(150, e.width-32))
                                               for item in (offline_hint, offline_note)] if e.widget is offline else None)
        self.root.bind("<Return>", self.submit)

    def open_help(self, topic='Getting started'):
        from help_view import HelpWindow
        if self.help_window is None or not self.help_window.winfo_exists():
            self.help_window = HelpWindow(self)
        self.help_window.show_topic(topic)

    def open_program_check(self):
        if self.program_check_window is None or not self.program_check_window.winfo_exists():
            from program_check_view import ProgramCheckWindow
            self.program_check_window = ProgramCheckWindow(self)
        self.program_check_window.deiconify()
        self.program_check_window.lift()

    def open_recorded_playback(self, source):
        from playback_view import RecordedPlayback
        if self.simulator_tab is None or not self.simulator_tab.winfo_exists():
            self.clear()
            self.root.unbind('<Return>')
            self.root.geometry('1180x800')
            self.root.minsize(980, 620)
            bar = ttk.Frame(self.root, padding=(16, 8), style='Toolbar.TFrame')
            bar.pack(fill='x')
            ttk.Button(bar, text='Connection setup', command=self.show_setup).pack(side='left')
            ttk.Button(bar, text='Program Check', command=self.open_program_check).pack(side='right')
            ttk.Button(bar, text='How to', command=lambda: self.open_help('Recorded playback')).pack(side='right', padx=8)
            self.tabs = ttk.Notebook(self.root)
            self.tabs.pack(fill='both', expand=True, padx=12, pady=(0, 12))
            self.simulator_tab = ttk.Frame(self.tabs)
            self.tabs.add(self.simulator_tab, text='Simulator')
            self.build_simulator_selector()
            self.live_simulator = ttk.Frame(self.simulator_tab, padding=24)
            ttk.Label(self.live_simulator, text='No live robot connection', style='Title.TLabel').pack(pady=(24, 12))
            ttk.Label(self.live_simulator, text='Connect to view live motion, or switch back to Flight report.',
                      style='Muted.TLabel').pack(pady=(0, 16))
            ttk.Button(self.live_simulator, text='Connection setup', command=self.show_setup).pack()
        if self.recorded_panel is not None and self.recorded_panel.winfo_exists() and self.recorded_source is not source:
            self.recorded_panel.destroy()
            self.recorded_panel = None
        if self.recorded_panel is None or not self.recorded_panel.winfo_exists():
            self.recorded_panel = RecordedPlayback(self.simulator_tab, source)
        self.recorded_source = source
        self.simulator_source.set('Flight report')
        self.select_simulator_source()
        self.tabs.select(self.simulator_tab)
        self.root.deiconify()
        self.root.lift()

    def build_simulator_selector(self):
        bar = ttk.Frame(self.simulator_tab, padding=(12, 8), style='Toolbar.TFrame')
        bar.pack(fill='x')
        ttk.Label(bar, text='Motion source', style='Panel.TLabel').pack(side='left', padx=(0, 10))
        self.source_selector = ttk.Combobox(bar, textvariable=self.simulator_source,
            values=('Live robot', 'Flight report'), state='readonly', width=18)
        self.source_selector.pack(side='left')
        self.source_selector.bind('<<ComboboxSelected>>', self.select_simulator_source)
        ttk.Button(bar, text='Choose flight report…', command=self.open_flights).pack(side='right')
        ttk.Button(bar, text='Full backup…', command=self.make_full_backup).pack(side='right', padx=8)

    def select_simulator_source(self, _event=None):
        if self.simulator_source.get() == 'Flight report':
            if self.recorded_panel is None or not self.recorded_panel.winfo_exists():
                from flight_view import FlightWindow
                from playback_view import RecordedPlayback
                if self.flight_window is None or not self.flight_window.winfo_exists():
                    self.flight_window = FlightWindow(self)
                    self.flight_window.withdraw()
                self.recorded_source = self.flight_window
                self.recorded_panel = RecordedPlayback(self.simulator_tab, self.recorded_source)
            self.live_simulator.pack_forget()
            self.recorded_panel.pack(fill='both', expand=True)
            self.recorded_panel.sync()
        else:
            if self.recorded_panel is not None and self.recorded_panel.winfo_exists():
                if self.recorded_source.timeline:
                    self.recorded_source.timeline.pause()
                    self.recorded_source.update_time(self.recorded_source.timeline.elapsed)
                self.recorded_panel.pack_forget()
            self.live_simulator.pack(fill='both', expand=True)
            if hasattr(self, 'viewer') and self.viewer.winfo_exists():
                self.viewer.redraw()

    def update_recorded_playback(self, source):
        if self.recorded_source is source and self.recorded_panel is not None and self.recorded_panel.winfo_exists():
            self.recorded_panel.sync()

    def open_flights(self):
        if self.flight_window is None or not self.flight_window.winfo_exists():
            from flight_view import FlightWindow
            self.flight_window = FlightWindow(self)
        self.flight_window.deiconify()
        self.flight_window.lift()

    def workspace_shortcut(self, action):
        if self.root.grab_current() is None:
            action()
        return 'break'

    def show_workspace(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_set()

    def open_logs(self):
        if self.log_window is None or not self.log_window.winfo_exists():
            from log_view import LogWindow
            self.log_window = LogWindow(self)
        self.log_window.deiconify()
        self.log_window.lift()

    def make_full_backup(self):
        """Use the active robot and the shared cancellable transfer workspace."""
        self.open_logs()
        window = self.log_window
        if window.job is None and self.settings is not None:
            changed_host = window.ip.get().strip() != self.settings.ip
            window.ip.set(self.settings.ip)
            window.username.set(self.settings.username or 'root')
            if self.settings.password or changed_host:
                window.password.set(self.settings.password)
            if changed_host:
                from log_transfer import DEFAULT_LOG_PATH
                window.remote_path.set(DEFAULT_LOG_PATH)
        window.make_backup(full=True)

    def submit(self, _event=None):
        if self.session is not None:
            return
        try:
            self.settings = make_settings(self.ip.get().strip() or DEFAULT_IP,
                                          self.username.get().strip() or DEFAULT_USERNAME, self.password.get(),
                                          self.installation.get(), self.directory.get(), self.model.get())
        except ValueError as error:
            self.error.set(str(error))
            message = str(error)
            variable = (self.ip if 'IP address' in message else
                        self.installation if 'filename' in message else
                        self.directory if 'absolute robot path' in message else None)
            if variable is not None:
                entry = self.setup_entries.get(str(variable))
                if entry is not None:
                    entry.state(['invalid'])
                    entry.focus_set()
            return
        self.error.set("")
        self.installation.set(self.settings.installation)
        self.directory.set(self.settings.directory)
        self.installation_data = self.last_sample = None
        self.status.set("Connecting to RTDE…")
        self.details.set("Installation will load after RTDE connects." if self.settings.can_load_payload else PAYLOAD_NOTICE)
        self.joints.set("Waiting for joints…")
        self.tcp_text.set("")
        self.program_text.set("Waiting for program telemetry…")
        self.last_status_update = 0.
        self.show_view()
        self.session = RobotSession(self.settings)
        self.session.start()

    def show_view(self):
        self.root.unbind("<Return>")
        self.clear()
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        bar = ttk.Frame(self.root, padding=(18, 12), style='Toolbar.TFrame')
        bar.pack(fill="x")
        ttk.Label(bar, text=f"{self.settings.model}  ·  {self.settings.ip}",
                  style='Title.Panel.TLabel').pack(side="left")
        ttk.Button(bar, text="Edit connection", command=self.edit_connection).pack(side="right", padx=6)
        self.connect_button = ttk.Button(bar, text="Disconnect", command=self.toggle_connection)
        self.connect_button.pack(side="right")
        ttk.Button(bar, text="Logs & graphs", command=self.open_logs).pack(side="right", padx=6)
        ttk.Button(bar, text="Program Check", command=self.open_program_check).pack(side="right", padx=6)
        ttk.Button(bar, text="Flight reports", command=self.open_flights).pack(side="right", padx=6)
        ttk.Button(bar, text="How to", command=self.open_help).pack(side="right")
        self.status_label = ttk.Label(self.root, textvariable=self.status, padding=(18, 8), style='Muted.TLabel')
        self.status_label.pack(fill="x")
        self.update_status_style()
        ttk.Label(self.root, text="Nominal model geometry · Read-only visualization · Saved payload is not a dynamics simulation\n"
                  "© 2023 Universal Robots A/S. Use hereof is subject to Universal Robots A/S’ Terms and Conditions for Use of Graphical Documentation.",
                  padding=(10, 6), wraplength=950, style="Muted.TLabel").pack(side="bottom", fill="x")
        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=12)
        self.simulator_tab = ttk.Frame(self.tabs)
        self.status_panel = StatusView(self.tabs)
        self.options_tab = ScrollableFrame(self.tabs, padding=16)
        options_tab = self.options_tab.content
        self.tabs.add(self.simulator_tab, text="Simulator")
        self.tabs.add(self.status_panel, text="Status")
        self.tabs.add(self.options_tab, text="View options")
        self.tabs.bind("<<NotebookTabChanged>>", self.tab_changed)
        self.simulator_source.set('Live robot')
        self.build_simulator_selector()
        self.live_simulator = ttk.Frame(self.simulator_tab)
        self.live_simulator.pack(fill='both', expand=True)
        program_bar = ttk.Frame(self.live_simulator, padding=(12, 8), style='Toolbar.TFrame')
        program_bar.pack(side='bottom', fill="x")
        ttk.Label(program_bar, textvariable=self.program_text, style='Section.Panel.TLabel').pack(anchor="w")
        ttk.Label(program_bar, text="Controller URScript line · Different from the PolyScope program-tree line",
                  style="Muted.Panel.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(options_tab, text='View options', style='Title.TLabel').pack(anchor='w', padx=12, pady=(0, 4))
        ttk.Label(options_tab, text='Camera alignment, recorded overlays and tool visualization',
                  style='Muted.TLabel').pack(anchor='w', padx=12, pady=(0, 16))
        controls = ttk.Frame(options_tab, padding=(12, 0, 12, 12))
        controls.pack(fill="x")
        ttk.Label(controls, text="Align view to:").pack(side="left", padx=(0, 8))
        self.frame_selector = ttk.Combobox(controls, textvariable=self.view_frame,
                                           values=VIEW_FRAMES, state="readonly", width=20)
        self.frame_selector.pack(side="left")
        self.frame_selector.bind("<<ComboboxSelected>>", self.change_view_frame)
        overlays = ttk.LabelFrame(options_tab, text="Display overlays", padding=(8, 4))
        overlays.pack(fill="x", padx=12, pady=(0, 10))
        for index, (key, label) in enumerate(DISPLAY_OPTIONS.items()):
            ttk.Checkbutton(overlays, text=label, variable=self.display_options[key],
                            command=self.update_display_options).grid(
                row=index // 2, column=index % 2, sticky="w", padx=(0, 12), pady=2)
        for column in range(2):
            overlays.columnconfigure(column, weight=1)
        ttk.Button(overlays, text="Hide all overlays",
                   command=lambda: self.set_all_overlays(False)).grid(row=0, column=2, sticky="ew", pady=2)
        ttk.Button(overlays, text="Show all overlays",
                   command=lambda: self.set_all_overlays(True)).grid(row=1, column=2, sticky="ew", pady=2)
        tool_controls = ttk.LabelFrame(options_tab, text='Tool preview', padding=12)
        tool_controls.pack(fill="x")
        tool_controls.columnconfigure(1, weight=1)
        ttk.Label(tool_controls, text="Preview tool:").grid(row=0, column=0, sticky='w', padx=(0, 12))
        tool_selector = ttk.Combobox(tool_controls, textvariable=self.tool, values=TOOLS,
                                    state="readonly", width=29)
        tool_selector.grid(row=0, column=1, sticky='w')
        tool_selector.bind("<<ComboboxSelected>>", self.update_tool)
        ttk.Label(tool_controls, text="Gripper: closed").grid(row=1, column=0, sticky='w', padx=(0, 12), pady=12)
        gripper = ttk.Frame(tool_controls)
        gripper.grid(row=1, column=1, sticky='ew')
        self.gripper_slider = ttk.Scale(gripper, from_=0, to=1, length=180,
                                       variable=self.gripper_opening, command=self.update_tool)
        self.gripper_slider.pack(side="left")
        ttk.Label(gripper, text="open").pack(side="left", padx=8)
        ttk.Label(gripper, text="Visual preview only", style='Muted.TLabel').pack(side="left", padx=12)
        tcp_controls = tool_controls
        ttk.Label(tcp_controls, text="Tool working point:").grid(row=2, column=0, sticky='w', padx=(0, 12))
        tcp_selector = ttk.Combobox(tcp_controls, textvariable=self.tool_tcp_source,
                                   values=TCP_SOURCES, state="readonly", width=20)
        tcp_selector.grid(row=2, column=1, sticky='w')
        tcp_selector.bind("<<ComboboxSelected>>", self.update_tool)
        ttk.Label(tcp_controls, text="Live TCP aligns the working point; tool keeps its flange orientation.",
                  style='Muted.TLabel', wraplength=650).grid(row=3, column=0, columnspan=2, sticky='w', pady=(10, 0))
        content = ttk.Frame(self.live_simulator)
        content.pack(fill="both", expand=True)
        self.viewer = RobotView(content, self.settings.model)
        self.update_tool()
        self.viewer.visibility.update({key: value.get() for key, value in self.display_options.items()})
        self.viewer.pack(side="left", fill="both", expand=True)
        self.viewer.set_view_frame(self.view_frame.get())
        self.sidebar_scroll = ScrollableFrame(content, padding=16, width=300, style='Panel.TFrame')
        # Reserve the readable sidebar before the expanding viewport claims
        # its requested width in a compact window.
        self.sidebar_scroll.pack(side="right", fill="y", before=self.viewer)
        self.sidebar_scroll.grid_propagate(False)
        sidebar = self.sidebar_scroll.content
        ttk.Label(sidebar, text="Joint positions (degrees)", style='Section.Panel.TLabel').pack(anchor="w")
        ttk.Label(sidebar, textvariable=self.joints, style='Panel.TLabel', font=("TkFixedFont", 10), justify="left").pack(anchor="w", pady=(8, 12))
        ttk.Label(sidebar, text="Live TCP in robot base (m / rad)", style='Section.Panel.TLabel', wraplength=235).pack(anchor="w")
        ttk.Label(sidebar, textvariable=self.tcp_text, style='Panel.TLabel', justify="left", wraplength=235).pack(anchor="w", pady=(8, 12))
        ttk.Label(sidebar, text="Installation", style='Section.Panel.TLabel').pack(anchor="w")
        ttk.Label(sidebar, text=self.settings.remote_path, style='Muted.Panel.TLabel', wraplength=235).pack(anchor="w", pady=6)
        self.details_view = ScrolledText(sidebar, width=28, height=4, wrap="word",
                                         background=FIELD, foreground=INK,
                                         font=("TkDefaultFont", 10), relief="flat", padx=6, pady=6)
        self.details_view.pack(fill="both", expand=True, pady=6)
        style_text(self.details_view)
        self.update_details()
        ttk.Button(sidebar, text="Reset camera", command=self.viewer.reset_camera).pack(anchor="w", pady=16)

    def tab_changed(self, _event=None):
        if not hasattr(self, "viewer") or not self.viewer.winfo_exists():
            return
        self.last_status_update = 0.
        if self.tabs.select() == str(self.simulator_tab) and self.simulator_source.get() == 'Live robot':
            self.viewer.redraw()

    def update_telemetry(self, sample, fresh):
        prefix = "" if fresh else "Last received · "
        values = sample.telemetry
        self.program_text.set(prefix + enum_text(values.get("runtime_state"), RUNTIME_STATES)
                              + "  ·  " + script_line_text(values))
        self.status_panel.update_sample(sample, fresh)

    def change_view_frame(self, _event=None):
        self.viewer.set_view_frame(self.view_frame.get())

    def update_display_options(self):
        self.viewer.visibility.update({key: value.get() for key, value in self.display_options.items()})
        self.viewer.redraw()

    def update_tool(self, _event=None):
        self.viewer.tool = self.tool.get()
        self.viewer.tool_tcp_source = self.tool_tcp_source.get()
        self.viewer.gripper_opening = self.gripper_opening.get()
        self.gripper_slider.configure(state="normal" if "gripper" in self.tool.get().lower() else "disabled")
        self.viewer.redraw()

    def set_all_overlays(self, visible):
        for value in self.display_options.values():
            value.set(visible)
        self.update_display_options()

    def disconnect(self, message="Disconnected · Last received pose retained"):
        if self.session:
            self.session.stop()
            self.session = None
        self.status.set(message)
        if self.last_sample:
            self.update_telemetry(self.last_sample, False)
        self.viewer.set_stream_state("Disconnected · Last received pose")
        self.connect_button.configure(text="Reconnect")

    def toggle_connection(self):
        if self.session:
            self.disconnect()
        else:
            self.submit()

    def edit_connection(self):
        self.disconnect()
        self.show_setup()

    def describe_installation(self, data):
        lines = []
        if data.tcp is not None:
            lines.append(f"TCP: {data.tcp_name or 'active'}")
            lines.append("Offset (m): " + ", ".join(f"{x:.4f}" for x in data.tcp[:3]))
            lines.append("Rotation vector (rad): " + ", ".join(f"{x:.3f}" for x in data.tcp[3:]))
        if data.payload_mass is not None:
            name = f" ({data.payload_name})" if data.payload_name else ""
            lines.append(f"Payload: {data.payload_mass:g} kg{name}")
        if data.payload_cog is not None:
            lines.append("CoG at flange (m): " + ", ".join(f"{x:.4f}" for x in data.payload_cog))
        if data.mounting is not None:
            base, tilt = (math.degrees(x) for x in data.mounting)
            lines.append(f"Mounting: base {base:.1f}°, tilt {tilt:.1f}°")
        lines.extend(data.warnings)
        lines.append("Saved settings may differ from the running program.")
        return "\n\n".join(lines)

    def poll(self):
        session = self.session
        if session is not None:
            while self.session is session:
                try:
                    kind, data = session.events.get_nowait()
                except queue.Empty:
                    break
                if kind == "connected":
                    self.status.set("Connected · Receiving live joints")
                    if self.settings.can_load_payload:
                        self.details.set("Downloading installation via SCP…")
                elif kind == "error":
                    self.disconnect("Connection lost / failed: " + data)
                elif kind == "installation_error":
                    self.details.set(data + "\n\nShowing robot base coordinates; saved TCP and payload unavailable.")
                elif kind == "installation":
                    self.installation_data = data
                    self.details.set(self.describe_installation(data))
                    if self.last_sample:
                        self.viewer.update_robot(self.last_sample, data,
                            render=self.simulator_source.get() == 'Live robot')
                elif kind == "host_key":
                    data.accepted = messagebox.askyesno("Verify robot SSH host",
                        f"SSH connection to {data.hostname}.\n\nHost fingerprint:\n{data.fingerprint}\n\n"
                        "Verify this fingerprint with your robot administrator. Trust it for this connection?",
                        parent=self.root)
                    data.answered.set()
            if self.session is session:
                sample = session.sample()
                if sample and sample is not self.last_sample:
                    self.last_sample = sample
                    self.viewer.update_robot(sample, self.installation_data,
                                             render=self.tabs.select() == str(self.simulator_tab)
                                             and self.simulator_source.get() == 'Live robot')
                    names = ("Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3")
                    self.joints.set("\n".join(f"{name:9} {math.degrees(q):8.2f}°" for name, q in zip(names, sample.joints)))
                    self.tcp_text.set("\n".join(f"{name:2}  {v: .5f}" for name, v in zip(("X", "Y", "Z", "RX", "RY", "RZ"), sample.tcp_pose)))
                if sample:
                    age = time.monotonic() - sample.received_at
                    if time.monotonic() - self.last_status_update >= .2:
                        self.update_telemetry(sample, age < 1)
                        self.last_status_update = time.monotonic()
                    self.viewer.set_stream_state("Live joint geometry" if age < 1 else "Stale · Last received pose")
                    self.status.set(f"Live · RTDE · data age {age*1000:.0f} ms" if age < 1
                                    else f"Stale data · No update for {age:.1f} s · Last pose retained")
        if not self.closed:
            self.poll_id = self.root.after(33, self.poll)

    def close(self):
        self.closed = True
        if self.log_window is not None:
            self.log_window.shutdown()
        if self.flight_window is not None:
            self.flight_window.shutdown()
        if self.flight_retrieval_window is not None:
            self.flight_retrieval_window.shutdown()
        if self.help_window is not None:
            self.help_window.shutdown()
        if self.program_check_window is not None:
            self.program_check_window.shutdown()
        if self.session:
            self.session.stop()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()

if __name__ == "__main__":
    import sys
    root = tk.Tk()
    app = SimulatorApp(root)
    if '--smoke-test' in sys.argv:
        try:
            from desktop_smoke import check
            check(app)
        except Exception:
            import traceback
            if sys.stderr is not None:
                traceback.print_exc()
            sys.exit(1)
        finally:
            app.close()
    else:
        root.mainloop()
