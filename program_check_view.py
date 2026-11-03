"""URP-first Program Check from local files or a robot source snapshot."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import math
import threading
import tkinter as tk
from tkinter import ttk, filedialog
from program_check import discover_programs, review_program, ProgramPair, ProgramSource, REFERENCES
from ui_theme import style_text
from connection import active_robot_settings, transfer_connection, simulator_connection_text
from address_book import SavedAddressCombobox
from workspace_panel import WorkspacePanel
import ui_dialogs as messagebox


class ProgramCheckWindow(WorkspacePanel):
    def __init__(self, app, parent=None):
        super().__init__(app.root, page_host=parent, on_show=lambda: app.navigate_panel(self), on_hide=getattr(app, 'show_workspace', None))
        self.app = app
        self.title('UR Motion Studio · Program Check')
        self.geometry('1180x820')
        self.minsize(900, 650)
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.closed = False
        self.review = None
        self.pairs = []
        self.reviews = []
        self.review_errors = {}
        self.cancelled = threading.Event()
        self.download_job = None
        self.source_mode = tk.StringVar(value='Local folder')
        self.robot_ip = tk.StringVar()
        self.robot_username = tk.StringVar(value='root')
        self.robot_password = tk.StringVar()
        self.warning = tk.StringVar()
        self.working_folder = tk.StringVar(value='No folder selected')
        self.advanced_visible = False
        self.busy = False
        self.cancellable = False
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='program-check')
        self.selected = tk.StringVar()
        self.profile = tk.StringVar(value='Auto (URP version)')
        self.status = tk.StringVar(value='Choose a local folder or pull programs from a robot, then select a URP.')
        self.source = tk.StringVar(value='No program selected')
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        bar = ttk.Frame(self, padding=(18, 12), style='Toolbar.TFrame')
        bar.grid(row=0, column=0, sticky='ew')
        ttk.Label(bar, text='PROGRAM CHECK', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(bar, text='How to', command=lambda: app.open_help('Program Check')).pack(side='left', padx=14)
        if not self.embedded:
            ttk.Button(bar, text='Robot Status…', command=app.open_diagnostics).pack(side='left')
        self.export_button = ttk.Button(bar, text='Export report…', command=self.export, state='disabled')
        self.export_button.pack(side='right')
        selectors = ttk.Frame(self, padding=16)
        selectors.grid(row=1, column=0, sticky='ew')
        selectors.columnconfigure(1, weight=1)
        actions = ttk.Frame(selectors)
        actions.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 12))
        self.source_selector = ttk.Combobox(actions, textvariable=self.source_mode, values=('Local folder', 'Robot'), state='readonly', width=12)
        self.source_selector.pack(side='left', padx=(0, 8))
        self.source_selector.bind('<<ComboboxSelected>>', self.change_source)
        self.folder_button = ttk.Button(actions, text='Choose folder…', command=self.choose_source, style='Accent.TButton')
        self.folder_button.pack(side='left', padx=(0, 10))
        self.input_buttons = [self.folder_button]
        self.advanced_button = ttk.Button(actions, text='Advanced…', command=self.toggle_advanced)
        self.advanced_button.pack(side='right')
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='right', padx=8)
        ttk.Entry(actions, textvariable=self.working_folder, state='readonly').pack(side='left', fill='x', expand=True)
        self.robot_controls = ttk.Frame(selectors)
        self.robot_controls.grid(row=1, column=0, columnspan=3, sticky='ew', pady=(0, 10))
        self.robot_entries = []
        for column, (label, variable, width) in enumerate((('Robot IP', self.robot_ip, 16), ('SSH user', self.robot_username, 10), ('Password', self.robot_password, 16))):
            ttk.Label(self.robot_controls, text=label).grid(row=0, column=column*2, padx=(0, 8))
            entry = (SavedAddressCombobox(self.robot_controls, app, textvariable=variable, width=width)
                     if variable is self.robot_ip else
                     ttk.Entry(self.robot_controls, textvariable=variable, width=width, show='•' if variable is self.robot_password else ''))
            entry.grid(row=0, column=column*2+1, sticky='ew', padx=(0, 12))
            self.robot_controls.columnconfigure(column*2+1, weight=1)
            self.robot_entries.append(entry)
        self.robot_controls.grid_remove()
        self.shared_connection = ttk.Label(selectors, style='Muted.TLabel', wraplength=850)
        self.shared_connection.grid(row=1, column=0, columnspan=3, sticky='w', pady=(0, 10))
        self.shared_connection.grid_remove()
        ttk.Label(selectors, text='Program (URP)').grid(row=2, column=0, padx=(0, 10))
        self.combo = ttk.Combobox(selectors, textvariable=self.selected, state='readonly')
        self.combo.grid(row=2, column=1, sticky='ew')
        self.combo.bind('<<ComboboxSelected>>', self.select_result)
        self.run_button = ttk.Button(selectors, text='Generate report', command=self.run, state='disabled')
        self.run_button.grid(row=2, column=2, padx=(10, 0))
        self.advanced = ttk.LabelFrame(selectors, text='Optional overrides and alternate sources', padding=10)
        self.advanced.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(10, 0))
        self.advanced.columnconfigure(1, weight=1)
        ttk.Label(self.advanced, text='UR guidance').grid(row=0, column=0, padx=(0, 10))
        self.profile_combo = ttk.Combobox(self.advanced, textvariable=self.profile, state='readonly', values=('Auto (URP version)', 'CB-Series / e-Series', 'Other / unconfirmed'))
        self.profile_combo.grid(row=0, column=1, sticky='ew')
        self.profile_combo.bind('<<ComboboxSelected>>', self.invalidate)
        self.limit_vars = {}
        self.limit_entries = {}
        specifications = ttk.Frame(self.advanced)
        specifications.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(8, 0))
        for index, (key, title) in enumerate((('joint_velocity', 'Joint velocity (rad/s)'),
                                             ('tcp_velocity', 'TCP velocity (m/s)'),
                                             ('joint_acceleration', 'Joint acceleration (rad/s²)'),
                                             ('tcp_acceleration', 'TCP acceleration (m/s²)'))):
            row, column = divmod(index, 2)
            variable = self.limit_vars[key] = tk.StringVar()
            ttk.Label(specifications, text=title).grid(row=row, column=column*2, sticky='w', padx=(0, 8), pady=3)
            entry = self.limit_entries[key] = ttk.Entry(specifications, textvariable=variable, width=12)
            entry.grid(row=row, column=column*2+1, sticky='ew', padx=(0, 18), pady=3)
            specifications.columnconfigure(column*2+1, weight=1)
            variable.trace_add('write', lambda *_: self.invalidate())
        ttk.Label(specifications, text='Optional specifications · Blank velocity: unassessed. Blank acceleration: selected UR guidance.',
                  style='Muted.TLabel').grid(row=2, column=0, columnspan=4, sticky='w', pady=(4, 0))
        alternate = ttk.Frame(self.advanced)
        alternate.grid(row=2, column=0, columnspan=2, sticky='w', pady=(8, 0))
        for title, callback in (('Backup / configuration archive…', self.choose_zip), ('URP / script files…', self.choose_files)):
            button = ttk.Button(alternate, text=title, command=callback)
            button.pack(side='left', padx=(0, 8))
            self.input_buttons.append(button)
        reset = ttk.Button(alternate, text='Restore automatic guidance', command=self.reset_advanced)
        reset.pack(side='left')
        self.input_buttons.append(reset)
        self.advanced.grid_remove()
        notices = ttk.Frame(self, padding=(18, 0, 18, 10))
        notices.grid(row=2, column=0, sticky='ew')
        source_label = ttk.Label(notices, textvariable=self.source, wraplength=1080, style='Muted.TLabel')
        source_label.pack(fill='x')
        warning_label = ttk.Label(notices, textvariable=self.warning, wraplength=1080, style='Warning.TLabel')
        warning_label.pack(fill='x')
        self.bind('<Configure>', lambda e: [label.configure(wraplength=max(600, e.width-36)) for label in (source_label, warning_label)] if e.widget is self else None)
        panes = ttk.Panedwindow(self, orient='vertical')
        self.panes = panes
        self._pane_size = None
        panes.bind('<Configure>', self.resize_panes)
        panes.grid(row=3, column=0, sticky='nsew', padx=18)
        top = ttk.Frame(panes)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(1, weight=1)
        filter_bar = ttk.Frame(top)
        filter_bar.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 8))
        ttk.Label(filter_bar, text='Show checks').pack(side='left', padx=(0, 8))
        self.finding_filter = tk.StringVar(value='All checks')
        finding_filter = ttk.Combobox(filter_bar, textvariable=self.finding_filter, state='readonly',
                                     values=('All checks', 'Review recommended', 'Manual review'), width=22)
        finding_filter.pack(side='left')
        finding_filter.bind('<<ComboboxSelected>>', self.filter_findings)
        ttk.Label(filter_bar, text='PDF includes every check', style='Muted.TLabel').pack(side='right')
        self.tree = ttk.Treeview(top, columns=('status', 'line', 'summary'), show='tree headings', height=10)
        self.tree.heading('#0', text='Check / source')
        self.tree.heading('status', text='Result')
        self.tree.heading('line', text='Line')
        self.tree.heading('summary', text='Finding')
        self.tree.column('#0', width=265, minwidth=245, stretch=False)
        self.tree.column('status', width=180, minwidth=165, stretch=False)
        self.tree.column('line', width=50, minwidth=45, stretch=False)
        self.tree.column('summary', width=460, minwidth=200)
        self.tree.grid(row=1, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(top, command=self.tree.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind('<<TreeviewSelect>>', self.show_finding)
        panes.add(top, weight=3)
        bottom = ttk.Frame(panes)
        bottom.rowconfigure(1, weight=1)
        bottom.columnconfigure(0, weight=1)
        ttk.Label(bottom, text='EVIDENCE & UR REFERENCE', style='Section.TLabel', padding=(0, 10)).grid(row=0, column=0, sticky='w')
        self.details = tk.Text(bottom, wrap='word', height=9, relief='flat', padx=12, pady=12)
        style_text(self.details)
        self.details.grid(row=1, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(bottom, command=self.details.yview)
        scroll.grid(row=1, column=1, sticky='ns')
        self.details.configure(yscrollcommand=scroll.set)
        panes.add(bottom, weight=2)
        self.set_details('Choose a local folder or select Robot and pull its programs.\n\nSelect a URP to generate its report using the matching script and referenced installation. A missing installation produces a warning; the script review still runs. Advanced settings are optional.')
        ttk.Label(self, textvariable=self.status, wraplength=850, padding=16, style='Muted.TLabel').grid(row=4, column=0, sticky='ew')
        self.after_id = self.after(75, self.poll)

    def toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        if self.advanced_visible:
            self.advanced.grid()
        else:
            self.advanced.grid_remove()
        self.advanced_button.configure(text='Hide advanced' if self.advanced_visible else 'Advanced…')

    def reset_advanced(self):
        self.profile.set('Auto (URP version)')
        for variable in self.limit_vars.values():
            variable.set('')
        self.invalidate()

    def cancel(self):
        self.cancelled.set()
        if self.download_job is not None:
            self.download_job.cancel()
        self.status.set('Cancelling…')

    def change_source(self, _event=None):
        if self.busy:
            return
        self.clear_sources()
        self.working_folder.set('No folder selected')
        if self.source_mode.get() == 'Robot':
            settings = getattr(self.app, 'settings', None)
            for target, name in ((self.robot_ip, 'ip'), (self.robot_username, 'username'), (self.robot_password, 'password')):
                value = getattr(settings, name, '') if settings is not None else getattr(self.app, name).get()
                if value:
                    target.set(value)
            self.robot_controls.grid()
            self.folder_button.configure(text='Pull programs…')
            self.status.set('Pull /programs from the robot, then choose a URP. A local copy is kept for later review.')
        else:
            self.robot_controls.grid_remove()
            self.folder_button.configure(text='Choose folder…')
            self.status.set('Choose the folder containing your URP, script and installation files.')
        self.sync_connection()

    def sync_connection(self):
        settings = active_robot_settings(self.app)
        robot = self.source_mode.get() == 'Robot'
        if settings is not None:
            for variable, name in ((self.robot_ip, 'ip'), (self.robot_username, 'username'), (self.robot_password, 'password')):
                value = getattr(settings, name) or ('root' if name == 'username' else '')
                if variable.get() != value:
                    variable.set(value)
        if robot and settings is not None:
            self.robot_controls.grid_remove()
            self.shared_connection.configure(text=simulator_connection_text(settings))
            self.shared_connection.grid()
        else:
            self.shared_connection.grid_remove()
            (self.robot_controls.grid if robot else self.robot_controls.grid_remove)()

    def choose_source(self):
        if self.source_mode.get() == 'Robot':
            self.pull_programs()
        else:
            self.choose_folder()

    def pull_programs(self):
        if self.busy:
            return
        try:
            connection = transfer_connection(self.app, self.robot_ip.get(), self.robot_username.get(), self.robot_password.get())
        except ValueError as exc:
            self.status.set(str(exc))
            return
        destination = filedialog.askdirectory(parent=self, title='Choose where to save the robot programs folder', mustexist=True)
        if not destination:
            return
        from program_transfer import ProgramSourceJob
        self.clear_sources()
        self.cancelled.clear()
        self.download_job = ProgramSourceJob(connection=connection, destination=Path(destination))
        self.cancellable = True
        self.set_busy(True)
        self.status.set('Connecting to robot and retrieving program sources…')
        self.download_job.start()

    def clear_sources(self):
        self.pairs = []
        self.combo.configure(values=())
        self.selected.set('')
        self.invalidate()
        self.source.set('No program selected')
        self.set_details('Select a source folder, then choose a URP. Its script and installation will be matched automatically.')
        self.set_busy(False)

    def resize_panes(self, event):
        size = (event.width, event.height)
        if size != self._pane_size and event.height > 100 and len(self.panes.panes()) == 2:
            self._pane_size = size
            self.panes.sashpos(0, int(event.height * .57))

    def set_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def set_busy(self, busy):
        self.busy = busy
        for button in self.input_buttons:
            button.configure(state='disabled' if busy else 'normal')
        for entry in self.limit_entries.values():
            entry.configure(state='disabled' if busy else 'normal')
        for entry in self.robot_entries:
            entry.configure(state='disabled' if busy else 'normal')
        self.source_selector.configure(state='disabled' if busy else 'readonly')
        self.combo.configure(state='disabled' if busy else 'readonly')
        self.profile_combo.configure(state='disabled' if busy else 'readonly')
        self.run_button.configure(state='normal' if 0 <= self.combo.current() < len(self.pairs) and not busy else 'disabled')
        self.export_button.configure(state='normal' if self.review and not busy else 'disabled')
        self.cancel_button.configure(state='normal' if busy and self.cancellable else 'disabled')

    def invalidate(self, _event=None):
        self.review = None
        self.reviews = [None] * len(self.pairs)
        self.review_errors = {}
        self.warning.set('')
        self.tree.delete(*self.tree.get_children())
        self.export_button.configure(state='disabled')
        index = self.combo.current()
        if 0 <= index < len(self.pairs):
            pair = self.pairs[index]
            origin = pair.urp or pair.script
            kinds = 'URP + script' if pair.urp and pair.script else ('URP only' if pair.urp else 'Script only')
            self.source.set(f'{origin.path.name if origin.member else origin.path.parent} · {kinds} · Confirm the files were saved together.')
            note = 'Settings changed · Generate report to apply them.' if pair.script else 'Missing generated .script. Generate report for a limited metadata review.'
            self.status.set(note)
            self.set_details(note + '\n\n' + '\n'.join(source.identity for source in (pair.urp, pair.script) if source) + '\n\nSource matching uses the same folder and filename stem. Confirm the URP and script were saved together.')

    def submit(self, kind, work):
        self.set_busy(True)
        def task():
            try:
                result = work()
                if kind in ('sources', 'review') and self.cancelled.is_set():
                    self.events.put(('cancelled', None))
                else:
                    self.events.put((kind, result))
            except Exception as exc:
                self.events.put(('error', str(exc)))
        self.executor.submit(task)

    def scan(self, path):
        if self.busy:
            return
        self.clear_sources()
        self.working_folder.set(str(Path(path).resolve()))
        self.source.set('Finding URP, script and installation files…')
        self.status.set('Finding URPs in this folder and its subfolders…')
        self.cancelled.clear()
        self.cancellable = True
        self.submit('sources', lambda: [pair for pair in discover_programs(path)
                                       if pair.urp or Path(path).suffix.lower() == '.script'])

    def choose_folder(self):
        path = filedialog.askdirectory(parent=self, title='Choose folder containing URP, script and installation files', mustexist=True)
        if path:
            self.scan(path)

    def choose_zip(self):
        path = filedialog.askopenfilename(parent=self, title='Select robot backup or configuration archive',
                                        filetypes=[('Robot archives', '*.zip *.tar *.tar.gz *.tgz'),
                                                   ('TAR backup', '*.tar *.tar.gz *.tgz'), ('ZIP archive', '*.zip')])
        if path:
            self.scan(path)

    def choose_files(self):
        paths = filedialog.askopenfilenames(parent=self, title='Select a URP and its saved script (or either file)', filetypes=[('UR program source', '*.urp *.script')])
        if not paths:
            return
        if len(paths) == 1:
            self.scan(paths[0])
        elif len(paths) == 2 and {Path(p).suffix.lower() for p in paths} == {'.urp', '.script'}:
            sources = {Path(p).suffix.lower()[1:]: ProgramSource(Path(p)) for p in paths}
            installations = tuple(ProgramSource(p) for p in sources['urp'].path.parent.glob('*.installation') if p.is_file() and not p.is_symlink())
            self.set_pairs([ProgramPair(sources['urp'].path.stem, **sources, installations=installations)])
            self.combo.current(0)
            self.run()
        else:
            self.status.set('Select one URP and one script, or use Programs folder for multiple programs.')

    def set_pairs(self, pairs):
        self.pairs = pairs
        labels = []
        for pair in pairs:
            origin = pair.urp or pair.script
            location = origin.member or str(origin.path)
            if not origin.member:
                try:
                    location = str(origin.path.relative_to(self.working_folder.get()))
                except ValueError:
                    pass
            labels.append(f'{pair.name} — {location}')
        self.combo.configure(values=labels)
        self.selected.set('Select a URP…' if pairs else '')
        self.invalidate()
        self.set_busy(False)
        if pairs:
            self.source.set(f'{len(pairs)} program(s) found · Select a URP to generate its report.')
            self.status.set('Choose a program above. Matching script and installation are loaded automatically.')
        else:
            self.source.set('No URP files found')
            self.status.set('Choose a folder containing URPs, or use Advanced for a standalone script.')

    def read_settings(self):
        limits = {}
        for key, variable in self.limit_vars.items():
            entry = self.limit_entries[key]
            entry.state(['!invalid'])
            if variable.get().strip():
                try:
                    value = float(variable.get())
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError
                    limits[key] = value
                except ValueError:
                    if not self.advanced_visible:
                        self.toggle_advanced()
                    entry.state(['invalid'])
                    entry.focus_set()
                    self.status.set('Enter a positive, finite specification in the labeled units, or leave the field blank.')
                    return None
        return self.profile.get(), limits

    def run(self):
        index = self.combo.current()
        if self.busy or not 0 <= index < len(self.pairs):
            return
        settings = self.read_settings()
        if settings is None:
            self.set_busy(False)
            return
        pair = self.pairs[index]
        self.review = None
        self.reviews[index] = None
        self.review_errors.pop(index, None)
        self.warning.set('')
        self.tree.delete(*self.tree.get_children())
        self.status.set(f'Reviewing {pair.name} · Matching script and installation…')
        self.cancelled.clear()
        self.cancellable = True
        self.submit('review', lambda: (index, review_program(pair, *settings)))

    def select_result(self, _event=None):
        if self.busy:
            return
        index = self.combo.current()
        self.review = None
        self.warning.set('')
        self.tree.delete(*self.tree.get_children())
        if 0 <= index < len(self.pairs):
            self.source.set((self.pairs[index].urp or self.pairs[index].script).identity)
        if 0 <= index < len(self.reviews) and self.reviews[index] is not None:
            self.show_review(self.reviews[index])
            self.set_busy(False)
        elif 0 <= index < len(self.pairs):
            self.run()

    def show_review(self, review):
        self.review = review
        self.filter_findings()
        flagged = [finding for finding in review.findings if finding.status == 'Review recommended']
        manual = sum(status == 'Manual review' for _, status in review.checklist)
        self.status.set(f'{review.pair.name} · {len(flagged)} recommendations · {manual} checks need manual review · Export this report or choose another URP.')
        warnings = []
        if not review.metadata.get('reviewInstallation'):
            warnings.append('Installation unavailable: ' + review.metadata.get('reviewInstallationNote', 'No matching installation in the selected files.') + ' Report generated without installation settings.')
        if not review.pair.script:
            warnings.append('Matching .script missing; only a limited metadata review is available.')
        self.warning.set(' '.join(warnings))
        self.source.set(f'{(review.pair.urp or review.pair.script).identity} · Installation: {review.metadata.get("reviewInstallation", "not matched")}')
        highlights = '\n'.join(f'Line {finding.line or "—"}: {finding.detail}' for finding in flagged[:3])
        self.set_details(f'Review complete · {len(flagged)} findings need attention.\n\n' + (self.warning.get() + '\n\n' if warnings else '') + (highlights + '\n\n' if highlights else '') + 'Select a check or expand it to inspect the source evidence.\n\n' + review.profile + '\n\nManual review means the source cannot establish the result. Recommendations are not safety limits or certification.')

    def filter_findings(self, _event=None):
        self.tree.delete(*self.tree.get_children())
        if self.review is None:
            return
        selected_status = self.finding_filter.get()
        for index, (category, status) in enumerate(self.review.checklist):
            findings = [(number, finding) for number, finding in enumerate(self.review.findings)
                        if finding.category == category
                        and (selected_status == 'All checks' or finding.status == selected_status)]
            if selected_status != 'All checks' and status != selected_status and not findings:
                continue
            parent = self.tree.insert('', 'end', iid=f'category{index}', text=category,
                                      values=(status, '', ''), open=selected_status != 'All checks')
            for number, finding in findings:
                self.tree.insert(parent, 'end', iid=f'finding{number}', text='Source evidence',
                                 values=(finding.status, finding.line or '—', finding.detail))
        if _event is not None:
            count = len(self.tree.get_children())
            self.set_details(f'{count} matching checks. Select a check or source finding to read its evidence.\n\n'
                             'Export always includes the complete report, regardless of this filter.')

    def show_finding(self, _event=None):
        selected = self.tree.selection()
        if not selected or not self.review:
            return
        item = selected[0]
        if item.startswith('finding'):
            finding = self.review.findings[int(item[7:])]
            title, url = REFERENCES[finding.reference]
            self.set_details(f'{finding.category} — {finding.status}\n\n{finding.detail}\n\n' + (f'Script line {finding.line}:\n{finding.evidence}\n\n' if finding.line else '') + f'{title}\n{url}')
        else:
            category, status = self.review.checklist[int(item[8:])]
            findings = [f for f in self.review.findings if f.category == category]
            self.set_details(f'{category} — {status}\n\nExpand this check to see {len(findings)} source finding(s), including line numbers and the applicable UR reference.')

    def export(self):
        if not self.review or self.busy:
            return
        review = self.review
        destination = filedialog.asksaveasfilename(parent=self, title='Export Program Check PDF', defaultextension='.pdf', initialfile=f'{review.pair.name}_program_check.pdf', filetypes=[('PDF report', '*.pdf')])
        if destination:
            from program_check_pdf import export_program_pdf
            self.cancellable = False
            self.status.set('Creating Program Check PDF…')
            self.submit('exported', lambda: export_program_pdf(review, destination))

    def poll(self):
        self.sync_connection()
        if self.download_job is not None:
            job = self.download_job
            while not job.events.empty():
                kind, result = job.events.get_nowait()
                if kind == 'host_key':
                    try:
                        result.accepted = False if self.cancelled.is_set() else messagebox.askyesno(
                            'Verify robot SSH host',
                            f'Robot: {result.hostname}\n\n{result.fingerprint}\n\nTrust this robot identity?',
                            parent=self, default='no')
                    finally:
                        result.answered.set()
                elif kind == 'status':
                    self.status.set(str(result))
                elif kind in ('downloaded', 'error', 'cancelled'):
                    self.download_job = None
                    self.cancellable = False
                    self.set_busy(False)
                    if kind == 'downloaded':
                        self.scan(result)
                    else:
                        self.status.set('Robot download cancelled.' if kind == 'cancelled' else f'Robot download failed: {result}')
        while not self.events.empty():
            kind, result = self.events.get_nowait()
            if kind == 'progress':
                self.status.set(result)
                continue
            if kind == 'sources':
                self.set_pairs(result)
            elif kind == 'review':
                index, review = result
                self.reviews[index] = review
                self.show_review(review)
            elif kind == 'exported':
                self.status.set(f'Program report saved: {result}')
            elif kind == 'error':
                self.status.set(f'Program Check failed: {result}')
                self.set_details(str(result))
                self.review_errors[self.combo.current()] = str(result)
            elif kind == 'cancelled':
                self.status.set('Cancelled · Choose a URP or generate its report again.')
            self.cancellable = False
            self.set_busy(False)
        if not self.closed:
            self.after_id = self.after(75, self.poll)

    def shutdown(self):
        self.closed = True
        self.cancelled.set()
        if self.download_job is not None:
            self.download_job.cancel()
        self.after_cancel(self.after_id)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
