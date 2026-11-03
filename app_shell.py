"""Persistent workspace navigation and an optional side-by-side simulator."""
import tkinter as tk
import time
from tkinter import ttk


class WorkspaceShell(ttk.Frame):
    PAGES = (('home', 'Home'), ('simulator', 'Simulator'), ('flights', 'Flight reports'),
             ('logs', 'Logs'), ('backups', 'Backups'), ('reports', 'Reports'),
             ('checklist', 'Checklist'))

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.pack(fill='both', expand=True)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        rail = ttk.Frame(self, padding=(12, 18), style='Panel.TFrame', width=176)
        rail.grid(row=0, column=0, sticky='ns')
        rail.grid_propagate(False)
        rail.columnconfigure(0, weight=1)
        footer = len(self.PAGES) + 2
        rail.rowconfigure(footer - 1, weight=1)
        ttk.Label(rail, text='UR MOTION\nSTUDIO', style='Title.Panel.TLabel').grid(
            row=0, column=0, sticky='w', padx=4, pady=(0, 28))
        self.buttons = {}
        for row, (name, label) in enumerate(self.PAGES, 1):
            button = ttk.Button(rail, text=label, command=lambda page=name: app.show_page(page))
            button.grid(row=row, column=0, sticky='ew', pady=(0, 7))
            self.buttons[name] = button
        ttk.Separator(rail).grid(row=footer, column=0, sticky='ew', pady=12)
        self.connection = tk.StringVar(value='Not connected')
        ttk.Label(rail, textvariable=self.connection, style='Muted.Panel.TLabel',
                  wraplength=145).grid(row=footer+1, column=0, sticky='w', padx=4, pady=(0, 14))
        self.theme_toggle = ttk.Checkbutton(rail, text='Dark mode', variable=app.dark_mode,
                                            command=app.toggle_theme)
        self.theme_toggle.grid(row=footer+2, column=0, sticky='w', pady=(0, 12))
        self.theme_toggle.bind('<Return>', lambda _event: (self.theme_toggle.invoke(), 'break')[1])
        ttk.Button(rail, text='Connection', command=app.edit_connection_page).grid(row=footer+3, column=0, sticky='ew')
        ttk.Button(rail, text='Help', command=app.open_help).grid(row=footer+4, column=0, sticky='ew', pady=(8, 0))
        self.content = ttk.Frame(self)
        self.content.grid(row=0, column=1, sticky='nsew')
        self.pages = {name: ttk.Frame(self.content) for name, _label in self.PAGES}
        self.active = None

    def show(self, name):
        if self.active != name:
            if self.active is not None:
                self.pages[self.active].pack_forget()
            self.pages[name].pack(fill='both', expand=True)
            self.active = name
        for key, button in self.buttons.items():
            button.configure(style='Accent.TButton' if key == name else 'TButton')

    def update_connection(self):
        app = self.app
        if app.settings is None:
            value = 'Not connected\nLocal files available'
        else:
            state = 'Connected' if app.session is not None and app.last_sample is not None else (
                'Connecting' if app.session is not None else 'Disconnected')
            if state == 'Connected' and time.monotonic() - app.last_sample.received_at >= 1:
                state = 'Stale telemetry'
            value = f'{state}\n{app.settings.model} · {app.settings.ip}'
        if self.connection.get() != value:
            self.connection.set(value)


class SimulatorPopout(tk.Toplevel):
    """A second camera surface, sharing data and playback rather than a connection."""
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title('UR Motion Studio · Separate simulator')
        self.geometry('1050x760')
        self.minsize(760, 560)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.source_mode = tk.StringVar(value=app.simulator_source.get())
        self.source = None
        self.recorded = None
        self.viewer = None
        self.last_sample = None
        self.last_identity = None
        bar = ttk.Frame(self, padding=(14, 10), style='Toolbar.TFrame')
        bar.pack(fill='x')
        ttk.Label(bar, text='Motion source', style='Panel.TLabel').pack(side='left', padx=(0, 10))
        selector = ttk.Combobox(bar, textvariable=self.source_mode,
                               values=('Live robot', 'Flight report'), state='readonly', width=16)
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', self.change_source)
        ttk.Button(bar, text='Flight reports', command=app.open_flights).pack(side='right')
        ttk.Button(bar, text='Reset camera', command=self.reset_camera).pack(side='right', padx=8)
        self.holder = ttk.Frame(self)
        self.holder.pack(fill='both', expand=True)
        self.live = ttk.Frame(self.holder)
        self.caption = tk.StringVar(value='Connect a robot in the Simulator page to view live movement.')
        ttk.Label(self.live, textvariable=self.caption, wraplength=950, padding=(16, 10)).pack(fill='x')
        self.change_source()

    def change_source(self, _event=None):
        if self.source_mode.get() == 'Flight report':
            from playback_view import RecordedPlayback
            self.live.pack_forget()
            self.source = self.app._ensure_flights()
            if self.recorded is None:
                self.recorded = RecordedPlayback(self.holder, self.source)
            self.recorded.pack(fill='both', expand=True)
            self.recorded.sync()
        else:
            if self.recorded is not None:
                self.recorded.pack_forget()
            self.live.pack(fill='both', expand=True)
            self.sync_live(force=True)

    def sync_recorded(self, source):
        if self.source_mode.get() == 'Flight report' and self.source is source and self.recorded is not None:
            self.recorded.sync()

    def sync_live(self, force=False):
        if self.source_mode.get() != 'Live robot':
            return
        app = self.app
        if app.settings is None:
            self.caption.set('Connect a robot in the Simulator page to view live movement.')
            return
        identity = (app.settings.ip, app.settings.model)
        if self.viewer is None or self.last_identity != identity:
            from view import RobotView
            if self.viewer is not None:
                self.viewer.destroy()
            self.viewer = RobotView(self.live, app.settings.model)
            self.viewer.pack(fill='both', expand=True)
            self.last_identity = identity
            self.viewer.visibility.update({key: value.get() for key, value in app.display_options.items()})
            self.viewer.tool = app.tool.get()
            self.viewer.tool_tcp_source = app.tool_tcp_source.get()
            self.viewer.gripper_opening = app.gripper_opening.get()
            self.viewer.set_view_frame(app.view_frame.get())
            force = True
        self.caption.set(f'{app.settings.model} · {app.settings.ip} · {app.status.get()}')
        if app.last_sample is not None and (force or app.last_sample is not self.last_sample):
            self.viewer.update_robot(app.last_sample, app.installation_data, render=bool(self.winfo_viewable()))
            self.last_sample = app.last_sample
        state = ('Disconnected · Last received pose' if app.session is None else
                 'Waiting for live joints' if app.last_sample is None else
                 'Stale · Last received pose' if time.monotonic() - app.last_sample.received_at >= 1 else
                 'Live joint geometry')
        self.viewer.set_stream_state(state)

    def reset_camera(self):
        if self.source_mode.get() == 'Flight report' and self.recorded is not None:
            self.recorded.reset_camera()
        elif self.viewer is not None:
            self.viewer.reset_camera()

    def close(self):
        self.app.simulator_popout = None
        self.destroy()
