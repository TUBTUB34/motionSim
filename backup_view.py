"""Dedicated backup workspace; navigation never starts a robot operation."""
from pathlib import Path
from datetime import datetime, timezone
import re
from uuid import uuid4
import queue
import tkinter as tk
from tkinter import ttk, filedialog

from backup_transfer import BackupJob
from connection import active_robot_settings, transfer_connection, simulator_connection_text
from address_book import SavedAddressCombobox
from workspace_panel import WorkspacePanel
from ui_theme import style_text
import ui_dialogs as messagebox


def backup_filename(project, date=None):
    """A portable project/date name; the caller handles existing destinations."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]+', '-', project.strip()).strip(' .-')
    if not name:
        raise ValueError('Enter a project name for this backup.')
    # Leave room for the date and collision suffix under common filename limits.
    while len(name.encode('utf-8')) > 140:
        name = name[:-1]
    return f'{name}-{date or datetime.now(timezone.utc).date().isoformat()}.tar'


class BackupPage(WorkspacePanel):
    def __init__(self, app, parent=None):
        super().__init__(app.root, page_host=parent, on_show=lambda: app.navigate_panel(self),
                         on_hide=getattr(app, 'show_workspace', None))
        self.app = app
        self.job = None
        self.closed = False
        self.title('UR Motion Studio · Backups')
        self.geometry('1000x760')
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.ip = tk.StringVar()
        self.username = tk.StringVar(value='root')
        self.password = tk.StringVar()
        self.directory = tk.StringVar()
        self.project_name = tk.StringVar()
        self.remote_log = tk.StringVar(value='/root/log_history.txt')
        self.remote_flights = tk.StringVar(value='/root/flightreports')
        self.full = tk.BooleanVar(value=True)
        self.programs = tk.BooleanVar(value=True)
        self.apps = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value='Choose a destination, then create a backup.')
        self.contents = tk.StringVar()
        self.advanced_visible = False
        self.entries = []
        self.use_connection()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        heading = ttk.Frame(self, style='Toolbar.TFrame', padding=(20, 14))
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='Backups', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(heading, text='How to', command=lambda: app.open_help('Backups')).pack(side='right')
        controls = ttk.Frame(self, padding=20)
        controls.grid(row=1, column=0, sticky='ew')
        controls.columnconfigure(1, weight=1)
        connection = ttk.Frame(controls)
        self.credentials = connection
        connection.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 16))
        for column, (caption, variable, width) in enumerate((('Robot IP', self.ip, 16), ('SSH user', self.username, 12), ('Password', self.password, 18))):
            ttk.Label(connection, text=caption).grid(row=0, column=column*2, padx=(0, 6))
            entry = (SavedAddressCombobox(connection, app, textvariable=variable, width=width)
                     if variable is self.ip else
                     ttk.Entry(connection, textvariable=variable, width=width, show='•' if variable is self.password else ''))
            entry.grid(row=0, column=column*2+1, padx=(0, 12), sticky='ew')
            connection.columnconfigure(column*2+1, weight=1)
            self.entries.append(entry)
        self.connection_button = ttk.Button(controls, text='Use simulator connection', command=self.use_connection)
        self.connection_button.grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 16))
        self.shared_connection = ttk.Label(controls, wraplength=800, style='Muted.TLabel')
        self.shared_connection.grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 16))
        self.shared_connection.grid_remove()
        ttk.Label(controls, text='Save in folder').grid(row=2, column=0, sticky='w', padx=(0, 14))
        destination = ttk.Entry(controls, textvariable=self.directory)
        destination.grid(row=2, column=1, sticky='ew')
        self.entries.append(destination)
        self.folder_button = ttk.Button(controls, text='Choose folder…', command=self.choose_folder)
        self.folder_button.grid(row=2, column=2, padx=(10, 0))
        self.full_control = ttk.Checkbutton(controls, text='Full robot backup', variable=self.full, command=self.update_contents)
        self.full_control.grid(row=3, column=0, columnspan=3, sticky='w', pady=(16, 6))
        ttk.Label(controls, textvariable=self.contents, style='Muted.TLabel', wraplength=820).grid(row=4, column=0, columnspan=3, sticky='w')
        action = ttk.Frame(controls)
        ttk.Label(controls, text='Project name').grid(row=5, column=0, sticky='w', pady=(14, 0))
        project_entry = ttk.Entry(controls, textvariable=self.project_name)
        project_entry.grid(row=5, column=1, columnspan=2, sticky='ew', pady=(14, 0))
        self.entries.append(project_entry)
        self.filename_preview = ttk.Label(controls, style='Muted.TLabel', wraplength=700)
        self.filename_preview.grid(row=6, column=1, columnspan=2, sticky='w', pady=(4, 0))
        self.project_name.trace_add('write', self.update_filename)
        self.update_filename()
        action.grid(row=7, column=0, columnspan=3, sticky='ew', pady=(18, 0))
        self.start_button = ttk.Button(action, text='Create backup', style='Accent.TButton', command=self.make_backup)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(action, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=10)
        self.advanced_button = ttk.Button(action, text='Advanced…', command=self.toggle_advanced)
        self.advanced_button.pack(side='right')
        self.advanced = ttk.Frame(controls)
        self.advanced.grid(row=8, column=0, columnspan=3, sticky='ew', pady=(14, 0))
        self.advanced.columnconfigure(1, weight=1)
        for row, (caption, variable) in enumerate((('Remote log file', self.remote_log), ('Remote flight folder', self.remote_flights))):
            ttk.Label(self.advanced, text=caption).grid(row=row, column=0, sticky='w', padx=(0, 14))
            entry = ttk.Entry(self.advanced, textvariable=variable)
            entry.grid(row=row, column=1, sticky='ew', pady=3)
            self.entries.append(entry)
        subset = ttk.Frame(self.advanced)
        subset.grid(row=2, column=0, columnspan=2, sticky='w')
        ttk.Label(subset, text='When full backup is off:').pack(side='left', padx=(0, 12))
        self.programs_control = ttk.Checkbutton(subset, text='Programs', variable=self.programs, command=self.update_contents)
        self.programs_control.pack(side='left')
        self.apps_control = ttk.Checkbutton(subset, text='Apps', variable=self.apps, command=self.update_contents)
        self.apps_control.pack(side='left', padx=10)
        self.advanced.grid_remove()
        history = ttk.Frame(self, padding=(20, 0, 20, 12))
        history.grid(row=2, column=0, sticky='nsew')
        history.columnconfigure(0, weight=1)
        history.rowconfigure(1, weight=1)
        ttk.Label(history, text='Activity', style='Section.TLabel').grid(row=0, column=0, sticky='w', pady=(0, 8))
        self.activity = tk.Text(history, wrap='word', height=6, padx=12, pady=12)
        style_text(self.activity)
        self.activity.grid(row=1, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(history, command=self.activity.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.activity.configure(yscrollcommand=scroll.set, state='disabled')
        footer = ttk.Frame(self, padding=(20, 0, 20, 16))
        footer.grid(row=3, column=0, sticky='ew')
        ttk.Label(footer, textvariable=self.status, wraplength=950).pack(anchor='w')
        self.progress = ttk.Progressbar(footer, mode='indeterminate')
        self.progress.pack(fill='x', pady=(10, 0))
        self.update_contents()
        self.sync_connection()
        self.after_id = self.after(100, self.poll)

    def sync_connection(self):
        settings = active_robot_settings(self.app)
        if settings is not None:
            for name in ('ip', 'username', 'password'):
                variable = getattr(self, name)
                value = getattr(settings, name) or ('root' if name == 'username' else '')
                if variable.get() != value:
                    variable.set(value)
            self.shared_connection.configure(text=simulator_connection_text(settings))
            self.credentials.grid_remove()
            self.connection_button.grid_remove()
            self.shared_connection.grid()
        else:
            self.shared_connection.grid_remove()
            self.credentials.grid()
            self.connection_button.grid()

    def update_filename(self, *_):
        try:
            text = 'File: ' + backup_filename(self.project_name.get()) + ' · UTC date'
        except ValueError:
            text = 'Saved as ProjectName-YYYY-MM-DD.tar'
        self.filename_preview.configure(text=text)

    def use_connection(self):
        if self.job is not None:
            return
        settings = getattr(self.app, 'settings', None)
        for name in ('ip', 'username', 'password'):
            variable = getattr(self.app, name, None)
            value = getattr(settings, name, '') if settings is not None else variable.get() if variable else ''
            getattr(self, name).set(value or ('root' if name == 'username' else ''))

    def update_contents(self):
        self.contents.set('Programs, apps, current and rotated logs, and flight reports in one TAR data archive. Missing sources are reported. This is not a controller image or the UR Support File.'
                          if self.full.get() else 'Selected folders only: ' + ', '.join(name for name, var in [('Programs', self.programs), ('Apps', self.apps)] if var.get()))
        if not self.full.get() and not self.advanced_visible:
            self.toggle_advanced()

    def select_full_backup(self):
        if self.job is None:
            self.full.set(True)
            self.update_contents()

    def toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        (self.advanced.grid if self.advanced_visible else self.advanced.grid_remove)()
        self.advanced_button.configure(text='Hide advanced' if self.advanced_visible else 'Advanced…')

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self, title='Save robot backups in folder', mustexist=True)
        if path:
            self.directory.set(path)

    def _busy(self, value):
        for widget in [*self.entries, self.folder_button, self.start_button, self.full_control,
                       self.programs_control, self.apps_control, self.connection_button]:
            widget.configure(state='disabled' if value else 'normal')
        self.cancel_button.configure(state='normal' if value else 'disabled')
        if value:
            self.progress.start(15)
        else:
            self.progress.stop()
            self.progress.configure(value=0)

    def _record(self, text):
        self.status.set(str(text))
        self.activity.configure(state='normal')
        self.activity.insert('end', str(text) + '\n\n')
        self.activity.see('end')
        self.activity.configure(state='disabled')

    def make_backup(self):
        if self.job is not None:
            return
        try:
            filename = backup_filename(self.project_name.get())
            connection = transfer_connection(self.app, self.ip.get(), self.username.get(), self.password.get(), self.remote_log.get().strip())
            if not self.directory.get().strip():
                self.choose_folder()
            if not self.directory.get().strip():
                self.status.set('Choose a destination before creating a backup.')
                return
            destination = Path(self.directory.get()).expanduser()
            if not destination.is_dir():
                raise ValueError('Choose an existing local destination folder.')
            sources = tuple(path for path, variable in [('/programs', self.programs), ('/data/apps', self.apps)] if variable.get())
            job = BackupJob(connection=connection, sources=sources, full=self.full.get(), flight_directory=self.remote_flights.get().strip())
        except ValueError as error:
            self.status.set(str(error))
            return
        self._destination = destination
        self._filename = filename
        self.job = job
        self._busy(True)
        self._record(f'Starting {filename} from {connection.ip}. The temporary robot archive is removed only after a successful local save.')
        job.start()

    def cancel(self):
        if self.job is not None:
            self.job.cancel()
            self.cancel_button.configure(state='disabled')
            self.status.set('Cancelling backup…')

    def poll(self):
        self.sync_connection()
        job = self.job
        if job is not None:
            while True:
                try:
                    kind, value = job.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'host_key':
                    try:
                        value.accepted = not job.stopped.is_set() and messagebox.askyesno(
                            'Verify robot SSH identity', f'{value.hostname}\n\n{value.fingerprint}\n\n' +
                            ('Replace the previously saved SSH key with this verified identity?' if value.replacement else 'Trust this robot identity?'),
                            parent=self, default='no')
                    finally:
                        value.answered.set()
                elif kind == 'backup_destination':
                    try:
                        if not job.stopped.is_set():
                            destination = self._destination / self._filename
                            while destination.exists():
                                destination = self._destination / f'{Path(self._filename).stem}-{uuid4().hex[:8]}.tar'
                            value.path = str(destination)
                    finally:
                        value.answered.set()
                elif kind == 'backup_finished':
                    self._record(value)
                    self.job = None
                    self._busy(False)
                    break
                elif kind == 'status' and not job.stopped.is_set():
                    self._record(value)
        if not self.closed:
            self.after_id = self.after(100, self.poll)

    def shutdown(self):
        self.closed = True
        self.cancel()
        self.progress.stop()
        self.after_cancel(self.after_id)
        self.destroy()
