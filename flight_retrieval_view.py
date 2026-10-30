"""Native, read-only robot flight browser; all network and parsing work is queued."""
from datetime import datetime, timezone
from dataclasses import replace
from pathlib import Path
import queue
import tkinter as tk
from tkinter import ttk, filedialog

import ui_dialogs as messagebox
from flight_transfer import FlightTransfer, DEFAULT_FLIGHT_DIRECTORY, default_library
from log_transfer import LogConnection
from ui_theme import BACKGROUND, MUTED, style_tree, status_color


def byte_text(value):
    if value is None:
        return 'Unknown'
    value = max(0, value)
    if value >= 1048576:
        return f'{value / 1048576:.2f} MiB'
    return f'{value / 1024:.1f} KiB'


def flight_date(entry):
    try:
        return datetime.strptime(entry.name, 'recording%Y%m%d_%H_%M_%S.zip').strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        return datetime.fromtimestamp(entry.mtime, timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if entry.mtime else 'Unavailable'


class RobotFlightWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root, background=BACKGROUND)
        self.app = app
        self.title('UR Motion Studio · Retrieve flight reports')
        self.geometry('1120x790')
        self.minsize(920, 740)
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.closed = False
        self.controller = None
        self._controller_key = None
        self._session = None
        self._host = None
        self._busy = False
        self._failed = False
        self._last_state = None
        self.entries = {}
        self.connection_status = tk.StringVar(value='Disconnected · Connect to a robot from the main window.')
        self.status = tk.StringVar(value='Ready · Remote recordings are read-only.')
        self.identity = tk.StringVar(value='Robot identity is verified when the file connection opens.')
        self.username = tk.StringVar(value='root')
        self.password = tk.StringVar()
        self.directory = tk.StringVar(value=DEFAULT_FLIGHT_DIRECTORY)
        self.library = tk.StringVar(value=str(getattr(app, 'flight_library_root', default_library())))
        self.file_progress_text = tk.StringVar(value='No transfer running')
        self.overall_progress_text = tk.StringVar(value='0 files · 0 bytes')
        self.summary = tk.StringVar(value='Refresh to list available flight reports.')
        self._build()
        self.after_id = self.after(100, self.poll)
        self._sync_connection()

    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        heading = ttk.Frame(self, style='Toolbar.TFrame', padding=(18, 14))
        heading.grid(row=0, column=0, sticky='ew')
        ttk.Label(heading, text='Robot flight reports', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(heading, text='Back to graphs', command=self.hide).pack(side='right')
        ttk.Button(heading, text='How to', command=lambda: self.app.open_help('Robot flight retrieval')).pack(side='right', padx=10)
        connection = ttk.Frame(self, padding=(18, 12, 18, 0))
        connection.grid(row=1, column=0, sticky='ew')
        connection.columnconfigure(0, weight=1)
        self.connection_label = ttk.Label(connection, textvariable=self.connection_status, style='Section.TLabel', wraplength=850)
        self.connection_label.grid(row=0, column=0, sticky='w')
        ttk.Button(connection, text='Show connection', command=self.show_connection).grid(row=0, column=1, padx=(12, 0))
        self.identity_label = ttk.Label(connection, textvariable=self.identity, style='Muted.TLabel', wraplength=850)
        self.identity_label.grid(row=1, column=0, columnspan=2, sticky='w', pady=(5, 8))
        settings = ttk.Frame(self, padding=(18, 0, 18, 12))
        settings.grid(row=2, column=0, sticky='ew')
        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(3, weight=1)
        ttk.Label(settings, text='SSH username').grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.user_entry = ttk.Entry(settings, textvariable=self.username, width=18)
        self.user_entry.grid(row=0, column=1, sticky='ew', padx=(0, 16))
        ttk.Label(settings, text='SSH password').grid(row=0, column=2, sticky='w', padx=(0, 8))
        self.password_entry = ttk.Entry(settings, textvariable=self.password, show='•', width=18)
        self.password_entry.grid(row=0, column=3, sticky='ew')
        ttk.Label(settings, text='Robot folder').grid(row=1, column=0, sticky='w', pady=(8, 0))
        self.directory_entry = ttk.Entry(settings, textvariable=self.directory)
        self.directory_entry.grid(row=1, column=1, columnspan=3, sticky='ew', pady=(8, 0))
        ttk.Label(settings, text='Working folder').grid(row=2, column=0, sticky='w', pady=(8, 0))
        ttk.Entry(settings, textvariable=self.library, state='readonly').grid(row=2, column=1, columnspan=2, sticky='ew', padx=(0, 12), pady=(8, 0))
        self.folder_button = ttk.Button(settings, text='Choose folder…', command=self.choose_folder)
        self.folder_button.grid(row=2, column=3, sticky='e', pady=(8, 0))
        body = ttk.Frame(self, padding=(18, 0))
        body.grid(row=3, column=0, sticky='nsew')
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        actions = ttk.Frame(body)
        actions.grid(row=0, column=0, sticky='ew', pady=(0, 8))
        self.refresh_button = ttk.Button(actions, text='Refresh reports', command=self.refresh)
        self.refresh_button.pack(side='left')
        self.download_button = ttk.Button(actions, text='Download selected', style='Accent.TButton', command=self.download_selected)
        self.download_button.pack(side='left', padx=8)
        self.new_button = ttk.Button(actions, text='Download all new', command=self.download_new)
        self.new_button.pack(side='left')
        self.retry_button = ttk.Button(actions, text='Retry failed', command=self.retry)
        self.retry_button.pack(side='right')
        ttk.Label(body, textvariable=self.summary, style='Muted.TLabel').grid(row=1, column=0, sticky='w', pady=(0, 6))
        table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky='nsew')
        table.columnconfigure(0, weight=1)
        table.rowconfigure(0, weight=1)
        columns = ('name', 'date', 'size', 'state')
        self.tree = ttk.Treeview(table, columns=columns, show='headings', selectmode='extended', height=5)
        style_tree(self.tree)
        for key, title, width in zip(columns, ('Recording', 'Flight date / modified', 'Size', 'Local status'), (350, 190, 100, 160)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=80, stretch=key in ('name', 'state'))
        self.tree.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self._update_controls())
        self.tree.bind('<Control-a>', self.select_all)
        ttk.Label(body, text='Ctrl/Shift selects multiple files. Flight dates use the robot filename; fallback modified dates are UTC.',
                  style='Muted.TLabel', wraplength=870).grid(row=3, column=0, sticky='w', pady=(6, 8))
        transfer = ttk.Frame(self, padding=(18, 10), style='Panel.TFrame')
        transfer.grid(row=4, column=0, sticky='ew', pady=(8, 0))
        transfer.columnconfigure(0, weight=1)
        ttk.Label(transfer, textvariable=self.file_progress_text, style='Panel.TLabel').grid(row=0, column=0, sticky='w')
        self.file_progress = ttk.Progressbar(transfer, mode='determinate', maximum=100)
        self.file_progress.grid(row=1, column=0, sticky='ew', pady=(6, 10))
        ttk.Label(transfer, textvariable=self.overall_progress_text, style='Muted.Panel.TLabel').grid(row=2, column=0, sticky='w')
        self.overall_progress = ttk.Progressbar(transfer, mode='determinate', maximum=100)
        self.overall_progress.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        self.cancel_button = ttk.Button(transfer, text='Cancel transfer', command=self.cancel)
        self.cancel_button.grid(row=0, column=1, rowspan=4, padx=(16, 0))
        self.status_label = ttk.Label(self, textvariable=self.status, style='Muted.TLabel', padding=(18, 10), wraplength=870)
        self.status_label.grid(row=5, column=0, sticky='ew')
        self.bind('<Configure>', self._resize)

    def _resize(self, event):
        if event.widget is self:
            self.status_label.configure(wraplength=max(650, event.width-36))
            self.identity_label.configure(wraplength=max(650, event.width-36))

    def _connection_state(self):
        session = getattr(self.app, 'session', None)
        settings = getattr(self.app, 'settings', None)
        if session is None or settings is None or session.stop_event.is_set():
            return 'disconnected', session, settings
        return ('connected' if session.sample() is not None else 'connecting'), session, settings

    def _sync_connection(self):
        state, session, settings = self._connection_state()
        if session is not self._session:
            if self.controller:
                self.controller.close()
            self.controller = None
            self._controller_key = None
            self._session = session
            self._last_state = None
            self._busy = self._failed = False
            self.entries.clear()
            self.tree.delete(*self.tree.get_children())
            self.identity.set('Robot identity will be verified for this connection.')
            self.summary.set('Refresh to list this robot’s flight reports.')
            if settings is not None:
                self._host = settings.ip
                username, password = settings.username or 'root', settings.password
                logs = getattr(self.app, 'log_window', None)
                if not password and logs is not None and logs.winfo_exists() and logs.ip.get() == settings.ip:
                    username, password = logs.username.get(), logs.password.get()
                self.username.set(username)
                self.password.set(password)
        if state != self._last_state:
            if state == 'disconnected':
                if self.controller:
                    self.controller.close()
                    self.controller = None
                self._busy = False
                self.connection_status.set('Disconnected · Connect to a robot from the main window.')
                self.status.set('Disconnected · Completed local reports remain available. Reconnect, then refresh.')
            elif state == 'connecting':
                self.connection_status.set(f'Connecting to {settings.ip} · Waiting for robot telemetry…')
                self.status.set('Connecting · Wait for robot telemetry before refreshing reports.')
            else:
                self.connection_status.set(f'Connected to {settings.ip} · File transfer uses the existing verified SSH workflow.')
                self.status.set('Ready · Refresh reports to open the file connection. Credentials stay in memory.')
            self._last_state = state
        self._update_controls()

    def _update_controls(self):
        connected = self._connection_state()[0] == 'connected'
        busy = self._busy or bool(self.controller and self.controller.busy)
        available = connected and not busy
        self.refresh_button.configure(state='normal' if available else 'disabled')
        self.download_button.configure(state='normal' if available and self.tree.selection() else 'disabled')
        self.new_button.configure(state='normal' if available and any(not e.downloaded for e in self.entries.values()) else 'disabled')
        self.retry_button.configure(state='normal' if available and self._failed else 'disabled')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        for control in (self.user_entry, self.password_entry, self.directory_entry, self.folder_button):
            control.configure(state='disabled' if busy else 'normal')
        self.status_label.configure(foreground=status_color(self.status.get()))
        self.connection_label.configure(foreground=status_color(self.connection_status.get()))

    def is_busy(self):
        return self._busy or bool(self.controller and self.controller.busy)

    def _ensure_controller(self):
        state, session, settings = self._connection_state()
        if state != 'connected':
            self.status.set('Connect to the robot from the main window before retrieving reports.')
            return False
        try:
            directory = self.directory.get().strip()
            if not directory.startswith('/') or any(c in directory for c in '\x00\r\n'):
                raise ValueError('Enter an absolute robot report folder.')
            connection = LogConnection(settings.ip, self.username.get().strip() or 'root', self.password.get(), directory)
            key = (session, connection, self.library.get())
            if self.controller is None or key != self._controller_key:
                if self.controller:
                    self.controller.close()
                self.controller = FlightTransfer(connection, library=Path(self.library.get()), directory=directory)
                self.controller.start()
                self._controller_key = key
                self.entries.clear()
                self.tree.delete(*self.tree.get_children())
            return True
        except (ValueError, OSError) as error:
            self.status.set(str(error).replace(self.password.get(), '[redacted]') if self.password.get() else str(error))
            self._update_controls()
            return False

    def refresh(self):
        if self._ensure_controller() and self.controller.refresh(directory=self.directory.get().strip()):
            self._busy = True
            self._failed = False
            self.status.set('Loading available flight reports…')
            self._update_controls()

    def _download(self, entries):
        previous = self.controller
        if not self._ensure_controller():
            return
        if self.controller is not previous:
            self.status.set('Connection settings changed · Refresh and select reports again.')
            self._update_controls()
            return
        if self.controller.download(entries):
            self._busy = True
            self._failed = False
            self.file_progress.configure(value=0)
            self.overall_progress.configure(value=0)
            self.status.set('Preparing selected recordings…')
            self._update_controls()

    def download_selected(self):
        selected = [self.entries[key] for key in self.tree.selection() if key in self.entries]
        if selected:
            self._download(selected)

    def download_new(self):
        self._download(None)

    def retry(self):
        previous = self.controller
        if not self._ensure_controller():
            return
        if self.controller is not previous:
            self._failed = False
            self.status.set('Connection settings changed · Refresh and select reports again.')
            self._update_controls()
            return
        if self.controller.retry():
            self._busy = True
            self._failed = False
            self.status.set('Retrying failed recordings…')
            self._update_controls()

    def cancel(self):
        if self.controller:
            self.controller.cancel()
            self.status.set('Cancelling · Incomplete files will not enter the library.')
            self._update_controls()

    def choose_folder(self):
        if self.is_busy():
            self.status.set('Wait for retrieval to finish or cancel it before changing the working folder.')
            return
        path = filedialog.askdirectory(parent=self, title='Choose flight-report working folder',
                                       initialdir=self.library.get(), mustexist=True)
        if path:
            self.set_library(path)

    def set_library(self, path, *, notify=True):
        path = Path(path).resolve()
        if path == Path(self.library.get()).resolve():
            return
        if self.controller:
            self.controller.close()
            self.controller = None
        self._controller_key = None
        self._busy = self._failed = False
        self.library.set(str(path))
        self.app.flight_library_root = path
        self.entries.clear()
        self.tree.delete(*self.tree.get_children())
        self.summary.set('Refresh to check downloaded reports in this working folder.')
        self.file_progress.configure(value=0)
        self.overall_progress.configure(value=0)
        self.file_progress_text.set('No transfer running')
        self.overall_progress_text.set('0 files · 0 bytes')
        self.status.set('Working folder changed · Refresh to check which reports are already downloaded here.')
        if notify:
            viewer = getattr(self.app, 'flight_window', None)
            if viewer is not None and viewer.winfo_exists():
                viewer.set_library(path, notify=False)
        self._update_controls()

    def select_all(self, _event=None):
        self.tree.selection_set(self.tree.get_children())
        return 'break'

    def _update_summary(self):
        entries = list(self.entries.values())
        total = sum(entry.size for entry in entries)
        self.summary.set(f'{len(entries)} reports · {byte_text(total)} · {sum(not entry.downloaded for entry in entries)} new or changed')

    def _handle_event(self, kind, payload):
        if kind == 'host_key':
            try:
                payload.accepted = messagebox.askyesno('Verify robot SSH host',
                    f'Robot: {payload.hostname}\n\n{payload.fingerprint}\n\nTrust this robot identity?',
                    parent=self, default='no')
            finally:
                payload.answered.set()
        elif kind == 'state':
            self.status.set(str(payload))
        elif kind == 'ready':
            serial = payload.get('serial') or 'Not available before reading a recording'
            self.identity.set(f'Robot serial: {serial} · SSH identity: {payload["fingerprint"]}')
        elif kind == 'listed':
            self.entries = {entry.path: entry for entry in payload}
            self.tree.delete(*self.tree.get_children())
            for index, entry in enumerate(payload):
                self.tree.insert('', 'end', iid=entry.path, values=(entry.name, flight_date(entry), byte_text(entry.size),
                    'Downloaded · verified' if entry.downloaded else 'New / changed'), tags=('even' if index % 2 else 'odd',))
            self._update_summary()
            self.status.set('No flight reports found in this folder. Check the folder and refresh.' if not payload else 'Ready · Select recordings or download all new reports.')
        elif kind == 'progress':
            done, size = payload['file_bytes'], payload['file_total']
            overall, total = payload['overall_bytes'], payload['overall_total']
            self.file_progress.configure(value=100 * done / size if size else 0)
            self.overall_progress.configure(value=100 * overall / total if total else 0)
            self.file_progress_text.set(f'{payload["index"]}/{payload["count"]} · {payload["name"]} · {byte_text(done)} / {byte_text(size)}')
            rate, eta = payload.get('rate'), payload.get('eta')
            speed = f' · {byte_text(rate)}/s' if rate else ''
            remaining = f' · ~{int(eta)} s remaining' if eta is not None and rate else ''
            self.overall_progress_text.set(f'Overall: {byte_text(overall)} / {byte_text(total)}{speed}{remaining}')
            self.status.set('Downloading · Files are added only after integrity checks and parsing finish.')
        elif kind == 'downloaded':
            entry, path = payload['entry'], Path(payload['path'])
            self.entries[entry.path] = replace(entry, downloaded=True, local_path=path)
            if self.tree.exists(entry.path):
                self.tree.set(entry.path, 'state', 'Downloaded · verified')
            self._update_summary()
            # No disk reading or parsing here; FlightWindow schedules it itself.
            viewer = getattr(self.app, 'flight_window', None)
            if viewer is not None and viewer.winfo_exists():
                viewer.add_download(path)
            self.status.set(f'{"Already verified" if payload.get("skipped") else "Downloaded"}: {entry.name}')
        elif kind == 'error':
            self._failed = True
            self._last_error = payload['message']
            self.status.set(f'Failed · {payload["message"]} · Refresh or retry after resolving the problem.')
            if payload.get('path') and self.tree.exists(payload['path']):
                self.tree.set(payload['path'], 'state', 'Failed · retry available')
        elif kind == 'finished':
            self._busy = False
            failed_paths = getattr(self.controller, 'failed', [])
            self._failed = bool(failed_paths)
            for path in failed_paths:
                if self.tree.exists(path):
                    self.tree.set(path, 'state', ('Cancelled' if payload.get('cancelled') else 'Failed') + ' · retry available')
            self.overall_progress_text.set(self.overall_progress_text.get().split(' · ')[0])
            if payload.get('cancelled'):
                self.status.set('Cancelled · Completed reports are available; refresh or retry the remaining files.')
            elif payload.get('failed') and payload.get('operation') != 'list':
                detail = getattr(self, '_last_error', 'Transfer interrupted')
                self.status.set(f'Failed · {payload.get("failed")} reports incomplete; {payload.get("succeeded", 0)} available. {detail} · Retry failed or refresh.')
            elif payload.get('operation') != 'list' and not self._failed:
                self.status.set(f'Completed · {payload.get("succeeded", 0)} reports processed in {payload.get("elapsed", 0):.1f} s. Ready in the flight selector.')
            self._update_controls()

    def poll(self):
        if self.closed:
            return
        self._sync_connection()
        if self.controller:
            for _ in range(100):
                try:
                    kind, payload = self.controller.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_event(kind, payload)
        self._update_controls()
        self.after_id = self.after(100, self.poll)

    def show_connection(self):
        self.withdraw()
        self.app.root.deiconify()
        self.app.root.lift()

    def hide(self):
        self.withdraw()
        viewer = getattr(self.app, 'flight_window', None)
        if viewer is not None and viewer.winfo_exists():
            viewer.deiconify()
            viewer.lift()

    def shutdown(self):
        if self.closed:
            return
        self.closed = True
        self.after_cancel(self.after_id)
        if self.controller:
            self.controller.close()
        self.destroy()
