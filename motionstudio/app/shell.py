"""Persistent workspace navigation and an optional side-by-side simulator."""
import tkinter as tk
import time
from tkinter import ttk

from motionstudio.ui import theme
from motionstudio.live.rtde_client import STALE_AFTER
from motionstudio.live.session import reconnect_reason


class WorkspaceShell(ttk.Frame):
    """Top navigation, one persistent page per workflow, and a status footer."""
    PAGES = (('home', 'Home'), ('simulator', 'Simulator'), ('live', 'Live monitor'),
             ('flights', 'Flight reports'), ('logs', 'Logs'), ('reports', 'Reports'),
             ('backups', 'Backups'), ('checklist', 'Checklist'), ('settings', 'Settings'))

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.pack(fill='both', expand=True)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        bar = ttk.Frame(self, padding=(14, 0), style='Panel.TFrame')
        bar.grid(row=0, column=0, sticky='ew')
        self.buttons = {}
        for name, label in self.PAGES:
            button = ttk.Button(bar, text=label, style='Nav.TButton', command=lambda page=name: app.show_page(page))
            button.pack(side='left')
            self.buttons[name] = button
        ttk.Button(bar, text='Help', style='Nav.TButton', command=app.open_help).pack(side='left')
        self.connection_button = ttk.Button(bar, text='Connect robot', command=app.edit_connection_page)
        self.connection_button.pack(side='right', pady=7)
        ttk.Separator(self).grid(row=0, column=0, sticky='sew')
        self.content = ttk.Frame(self)
        self.content.grid(row=1, column=0, sticky='nsew')
        ttk.Separator(self).grid(row=2, column=0, sticky='new')
        footer = ttk.Frame(self, padding=(18, 7), style='Panel.TFrame')
        footer.grid(row=2, column=0, sticky='ew')
        footer.columnconfigure(2, weight=1)
        self.indicator = tk.Canvas(footer, width=10, height=10, background=theme.PANEL, highlightthickness=0)
        self.indicator.grid(row=0, column=0, padx=(0, 8))
        self.dot = self.indicator.create_oval(1, 1, 9, 9, fill=theme.DISABLED, outline='')
        self.connection = tk.StringVar(value='Not connected')
        self.connection_label = ttk.Label(footer, style='Panel.TLabel')
        self.connection_label.grid(row=0, column=1, sticky='w')
        self.summary = tk.StringVar()
        ttk.Label(footer, textvariable=self.summary, style='Muted.Panel.TLabel', anchor='center').grid(
            row=0, column=2, sticky='ew', padx=16)
        # Where saves go is always visible; clicking it opens Settings to switch projects.
        self.project_label = ttk.Label(footer, style='Muted.Panel.TLabel', cursor='hand2')
        self.project_label.grid(row=0, column=3, padx=(0, 18))
        self.project_label.bind('<Button-1>', lambda _event: app.open_settings())
        self.update_project()
        ttk.Label(footer, text='UR MOTION STUDIO', style='Section.Panel.TLabel').grid(row=0, column=4)
        self.pages = {name: ttk.Frame(self.content) for name, _label in self.PAGES}
        self.active = None
        self._dot_state = None

    def update_project(self):
        self.project_label.configure(text=f'Project · {self.app.project}')

    def show(self, name):
        if self.active != name:
            if self.active is not None:
                self.pages[self.active].pack_forget()
            self.pages[name].pack(fill='both', expand=True)
            self.active = name
        for key, button in self.buttons.items():
            button.configure(style='NavActive.TButton' if key == name else 'Nav.TButton')

    def update_connection(self):
        app = self.app
        if app.settings is None:
            value, state = 'Not connected\nLocal files available', 'DISABLED'
        else:
            state = 'Connected' if app.session is not None and app.last_sample is not None else (
                'Connecting' if app.session is not None else 'Disconnected')
            if state == 'Connected' and reconnect_reason(app.session):
                state = 'Reconnecting'
            elif state == 'Connected' and time.monotonic() - app.last_sample.received_at >= STALE_AFTER:
                state = 'Slow link · waiting'
            value = f'{state}\n{app.settings.model} · {app.settings.ip}'
            state = {'Connected': 'SUCCESS', 'Disconnected': 'DISABLED'}.get(state, 'WARNING')
        if self.connection.get() != value:
            self.connection.set(value)
            self.connection_label.configure(text=value.replace('\n', ' · '))
            self.connection_button.configure(text='Connect robot' if app.session is None else 'Connection')
        # The token name is kept, so a theme switch repaints the dot correctly.
        if self._dot_state != (state, theme.MODE):
            self._dot_state = (state, theme.MODE)
            self.indicator.itemconfigure(self.dot, fill=getattr(theme, state))


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
        from motionstudio.viewer.mounting import MountingControls, installation_shown, robot_shown
        self.mounting_controls = MountingControls(self, lambda: installation_shown(app, self.source_mode.get(), self.source),
                                                  lambda: robot_shown(self.source_mode.get(), self.source))
        self.mounting_controls.pack(fill='x')
        self.holder = ttk.Frame(self)
        self.holder.pack(fill='both', expand=True)
        self.live = ttk.Frame(self.holder)
        self.caption = tk.StringVar(value='Connect a robot in the Simulator page to view live movement.')
        ttk.Label(self.live, textvariable=self.caption, wraplength=950, padding=(16, 10)).pack(fill='x')
        self.change_source()

    def change_source(self, _event=None):
        if self.source_mode.get() == 'Flight report':
            from motionstudio.viewer.playback import RecordedPlayback
            self.live.pack_forget()
            self.source = self.app._ensure_flights()
            if self.recorded is None:
                self.recorded = RecordedPlayback(self.holder, self.source)
            self.recorded.pack(fill='both', expand=True)
            self.recorded.sync()
            self.mounting_controls.mounting_changed()
        else:
            if self.recorded is not None:
                self.recorded.pack_forget()
            self.live.pack(fill='both', expand=True)
            self.sync_live(force=True)

    def sync_recorded(self, source):
        if self.source_mode.get() == 'Flight report' and self.source is source and self.recorded is not None:
            self.recorded.sync()
            self.mounting_controls.mounting_changed()

    def sync_live(self, force=False):
        if self.source_mode.get() != 'Live robot':
            return
        if force:
            self.mounting_controls.mounting_changed()
        app = self.app
        if app.settings is None:
            self.caption.set('Connect a robot in the Simulator page to view live movement.')
            return
        identity = (app.settings.ip, app.settings.model)
        if self.viewer is None or self.last_identity != identity:
            from motionstudio.viewer.robot_view import RobotView
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
                 'Slow link · Last received pose' if time.monotonic() - app.last_sample.received_at >= STALE_AFTER else
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
