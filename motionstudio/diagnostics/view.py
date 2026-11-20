"""Client-facing Robot Status workspace backed by local diagnostic evidence."""
from concurrent.futures import ThreadPoolExecutor
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from motionstudio.diagnostics.health import METHOD
from motionstudio.diagnostics.pdf import APPENDIX_ALL_INCIDENTS, clean_warnings, incident_sections, unique_text
from motionstudio.diagnostics.transfer import DiagnosticSourceJob
from motionstudio.remote.connection import (active_robot_settings, robot_download_folder, should_select_robot,
                        simulator_connection_text, transfer_connection)
from motionstudio.ui.address_book import SavedAddressCombobox
from motionstudio.diagnostics.analysis import analyze_directory
from motionstudio.diagnostics.status import build_robot_status
from motionstudio.remote.ssh_trust import host_key_prompt
from motionstudio.ui import theme
from motionstudio.ui.theme import style_text
from motionstudio.backup.transfer import backup_filename
from motionstudio.core.paths import project_folder, start_folder
from motionstudio.ui.workspace_panel import WorkspacePanel
from motionstudio.ui import dialogs as messagebox
from motionstudio.core.about import APP_NAME


class _Cancelled(Exception):
    pass


class DiagnosticWindow(WorkspacePanel):
    def __init__(self, app, parent=None):
        super().__init__(app.root, page_host=parent,
                         on_show=lambda: app.navigate_panel(self), on_hide=getattr(app, 'show_workspace', None))
        self.app = app
        self.title(f'{APP_NAME} · Robot Status')
        self.geometry('1180x820')
        self.minsize(900, 650)
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.closed = False
        self.busy = False
        self.report = None
        self.robot_status = None
        self.download_job = None
        self.cancelled = threading.Event()
        self.events = queue.Queue()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='robot-diagnostics')
        self.source_mode = tk.StringVar(value='Local folder')
        self.connected_ip = None
        self.folder = tk.StringVar(value='No dataset selected')
        self.status = tk.StringVar(value='Choose a folder containing logs and flight recordings, or collect them from the robot.')
        self.notice = tk.StringVar(value='A client-ready assessment of recorded robot condition, each joint and program areas to review.')
        self.ip = tk.StringVar(value=self._setting('ip', ''))
        self.username = tk.StringVar(value=self._setting('username', 'root'))
        self.password = tk.StringVar(value=self._setting('password', ''))
        self.log_path = tk.StringVar(value='/root/log_history.txt')
        self.flight_path = tk.StringVar(value='/root/flightreports')
        self.log_utc_offset = tk.StringVar(value='')
        self.client_name = tk.StringVar(value='')
        self.robot_label = tk.StringVar(value='')
        self.prepared_by = tk.StringVar(value='')
        self.include_evidence = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        bar = ttk.Frame(self, padding=(18, 12), style='Toolbar.TFrame')
        bar.grid(row=0, column=0, sticky='ew')
        ttk.Label(bar, text='Robot Status', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(bar, text='How to', command=lambda: app.open_help('Robot Status')).pack(side='left', padx=14)
        self.export_button = ttk.Button(bar, text='Export client report…', command=self.export, state='disabled')
        self.export_button.pack(side='right')
        source = ttk.Frame(self, padding=16)
        source.grid(row=1, column=0, sticky='ew')
        source.columnconfigure(0, weight=1)
        controls = ttk.Frame(source)
        controls.grid(row=0, column=0, sticky='ew')
        self.selector = ttk.Combobox(controls, textvariable=self.source_mode, values=('Local folder', 'Robot'), width=13, state='readonly')
        self.selector.pack(side='left', padx=(0, 8))
        self.selector.bind('<<ComboboxSelected>>', self.change_source)
        self.source_button = ttk.Button(controls, text='Choose folder & analyze…', style='Accent.TButton', command=self.choose_source)
        self.source_button.pack(side='left')
        self.cancel_button = ttk.Button(controls, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='right')
        self.advanced_button = ttk.Button(controls, text='Advanced…', command=self.toggle_advanced)
        self.advanced_button.pack(side='right', padx=8)
        self.robot_controls = ttk.Frame(source)
        self.robot_controls.grid(row=1, column=0, sticky='ew', pady=(12, 0))
        self.entries = []
        for column, (label, variable, width) in enumerate((('Robot IP', self.ip, 16), ('SSH user', self.username, 10), ('Password', self.password, 18))):
            ttk.Label(self.robot_controls, text=label).grid(row=0, column=column*2, padx=(0, 6))
            entry = (SavedAddressCombobox(self.robot_controls, app, textvariable=variable, width=width)
                     if variable is self.ip else
                     ttk.Entry(self.robot_controls, textvariable=variable, width=width, show='•' if variable is self.password else ''))
            entry.grid(row=0, column=column*2+1, sticky='ew', padx=(0, 14))
            self.robot_controls.columnconfigure(column*2+1, weight=1)
            self.entries.append(entry)
        self.robot_controls.grid_remove()
        self.shared_connection = ttk.Label(source, style='Muted.TLabel', wraplength=850)
        self.shared_connection.grid(row=1, column=0, sticky='w', pady=(12, 0))
        self.shared_connection.grid_remove()
        self.advanced = ttk.Frame(source)
        self.advanced.grid(row=2, column=0, sticky='ew', pady=(10, 0))
        self.advanced.columnconfigure((1, 3), weight=1)
        for index, (label, variable) in enumerate((('Client name (optional)', self.client_name),
                                                ('Robot / asset label (optional)', self.robot_label),
                                                ('Prepared by (optional)', self.prepared_by),
                                                ('Remote log file (+ .bak)', self.log_path), ('Remote flight folder', self.flight_path),
                                                ('Log UTC offset (optional, e.g. +02:00)', self.log_utc_offset))):
            row, column = index % 3, (index // 3) * 2
            ttk.Label(self.advanced, text=label).grid(row=row, column=column, sticky='w', padx=(0, 12), pady=3)
            entry = ttk.Entry(self.advanced, textvariable=variable)
            entry.grid(row=row, column=column+1, sticky='ew', padx=(0, 12))
            self.entries.append(entry)
        self.evidence_option = ttk.Checkbutton(self.advanced, text='Include technical incident evidence and charts in the PDF', variable=self.include_evidence)
        self.evidence_option.grid(row=3, column=0, columnspan=4, sticky='w', pady=(8, 0))
        self.advanced.grid_remove()
        self.advanced_visible = False
        ttk.Entry(source, textvariable=self.folder, state='readonly').grid(row=3, column=0, sticky='ew', pady=(12, 6))
        ttk.Label(source, textvariable=self.notice, wraplength=1050).grid(row=4, column=0, sticky='w')
        panes = ttk.Panedwindow(self, orient='horizontal')
        panes.grid(row=2, column=0, sticky='nsew', padx=16)
        navigation = ttk.Frame(panes, width=285)
        navigation.rowconfigure(1, weight=1)
        navigation.columnconfigure(0, weight=1)
        ttk.Label(navigation, text='ROBOT STATUS').grid(row=0, column=0, sticky='w', pady=(0, 8))
        self.cases = ttk.Treeview(navigation, show='tree', selectmode='browse', height=18)
        self.cases.heading('#0', text='Cases')
        self.cases.column('#0', width=285, minwidth=170)
        self.cases.grid(row=1, column=0, sticky='nsew')
        case_scroll = ttk.Scrollbar(navigation, command=self.cases.yview)
        case_scroll.grid(row=1, column=1, sticky='ns')
        self.cases.configure(yscrollcommand=case_scroll.set)
        self.cases.bind('<<TreeviewSelect>>', self.show_selection)
        body = ttk.Frame(panes, padding=(14, 0, 0, 0))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.details = tk.Text(body, wrap='word', padx=18, pady=14, font=theme.font(11), spacing3=9)
        style_text(self.details)
        self.details.tag_configure('title', font=theme.font(19, 'bold'), spacing1=4, spacing3=15)
        self.details.tag_configure('heading', font=theme.font(12, 'bold'), spacing1=16, spacing3=8)
        self.details.grid(row=0, column=0, sticky='nsew')
        scroll = ttk.Scrollbar(body, command=self.details.yview)
        scroll.grid(row=0, column=1, sticky='ns')
        self.details.configure(yscrollcommand=scroll.set, state='disabled')
        panes.add(navigation, weight=1)
        panes.add(body, weight=3)
        footer = ttk.Frame(self, padding=(16, 10))
        footer.grid(row=3, column=0, sticky='ew')
        ttk.Label(footer, textvariable=self.status, wraplength=1050).pack(anchor='w')
        self.progress = ttk.Progressbar(footer, mode='indeterminate')
        self.progress.pack(fill='x', pady=(8, 0))
        self._display('Prepare a Robot Status report', [
            ('1. Choose the evidence', ['Use an existing folder, including subfolders, or pull logs and flight recordings from a robot into a new local dataset.']),
            ('2. Review the robot and each joint', ['Read the overall assessment, six joint summaries and specific program areas that warrant attention. The report states where evidence is insufficient.']),
            ('3. Share a clear client report', ['Export a concise PDF with the status and recommended next steps. Add client details or a technical evidence appendix in Advanced.'])])
        self.after_id = self.after(75, self.poll)

    def _setting(self, name, default):
        settings = getattr(self.app, 'settings', None)
        if settings is not None:
            return getattr(settings, name, default)
        variable = getattr(self.app, name, None)
        return variable.get() if variable is not None else default

    def change_source(self, _event=None):
        robot = self.source_mode.get() == 'Robot'
        self.sync_connection()
        self.source_button.configure(text='Collect & analyze…' if robot else 'Choose folder & analyze…')

    def sync_connection(self):
        settings = active_robot_settings(self.app)
        if should_select_robot(self.connected_ip, settings) and not self.busy:
            self.connected_ip = settings.ip
            if self.source_mode.get() != 'Robot':
                self.source_mode.set('Robot')
                self.change_source()
                return
        if settings is None:
            self.connected_ip = None
        robot = self.source_mode.get() == 'Robot'
        if settings is not None:
            for name in ('ip', 'username', 'password'):
                variable = getattr(self, name)
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

    def toggle_advanced(self):
        self.advanced_visible = not self.advanced_visible
        (self.advanced.grid if self.advanced_visible else self.advanced.grid_remove)()
        self.advanced_button.configure(text='Hide advanced' if self.advanced_visible else 'Advanced…')

    def set_busy(self, busy):
        self.busy = busy
        for widget in [self.source_button, self.advanced_button, self.evidence_option, *self.entries]:
            widget.configure(state='disabled' if busy else 'normal')
        self.selector.configure(state='disabled' if busy else 'readonly')
        self.cancel_button.configure(state='normal' if busy else 'disabled')
        self.export_button.configure(state='normal' if self.report is not None and not busy else 'disabled')
        self.progress.start(15) if busy else self.progress.stop()
        if not busy:
            self.progress.configure(value=0)

    def _reset_report(self):
        self.report = None
        self.robot_status = None
        self.cases.delete(*self.cases.get_children())
        self._display('Review in progress', [('Evidence collection and analysis', ['The assessment will appear here when analysis finishes.'])])

    def choose_source(self):
        if self.busy:
            return
        if self.source_mode.get() == 'Local folder':
            path = filedialog.askdirectory(parent=self, title='Folder with robot logs and flight recordings',
                                           initialdir=start_folder(self.app, 'status', create=False))
            if path:
                self.analyze(path)
            return
        try:
            connection = transfer_connection(self.app, self.ip.get(), self.username.get(), self.password.get(), self.log_path.get().strip())
            if active_robot_settings(self.app) is not None:
                destination = robot_download_folder(self.app, 'Robot Status', connection.ip)
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination = filedialog.askdirectory(parent=self, title='Save a new robot diagnostic dataset in…',
                                                      initialdir=start_folder(self.app, 'status'))
                if not destination:
                    return
            project = project_folder(self.app)
            job = DiagnosticSourceJob(connection=connection, destination=destination, flight_directory=self.flight_path.get().strip(),
                                      project=project, archive_name=backup_filename(project.name))
        except Exception as error:
            self.status.set(f'Cannot collect robot evidence: {error}')
            return
        self.cancelled.clear()
        self._reset_report()
        self.set_busy(True)
        self.notice.set('Files already saved in this project are reused; only what is missing is downloaded. Files on the robot are not changed.')
        self.folder.set(str(destination))
        self.status.set(f'Connecting to robot… Saving into {destination}')
        self.download_job = job
        job.start()

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise _Cancelled()

    def _submit(self, kind, work):
        self.cancelled.clear()
        self.set_busy(True)
        def task():
            try:
                result = work()
                self._check_cancel()
                self.events.put((kind, result))
            except _Cancelled:
                self.events.put(('cancelled', None))
            except Exception as error:
                self.events.put(('cancelled' if self.cancelled.is_set() else 'error', str(error)))
        self.executor.submit(task)

    def analyze(self, path, collected=''):
        self._reset_report()
        self.folder.set(str(path))
        self.notice.set('Comparing controller events with compatible recordings. Missing evidence will be identified in the review.')
        self.status.set((collected + ' ' if collected else '') + 'Analyzing local evidence…')
        log_utc_offset = self.log_utc_offset.get().strip()
        self._submit('report', lambda: analyze_directory(path, check_cancel=self._check_cancel,
                     log_utc_offset=log_utc_offset,
                     progress=lambda message: self.events.put(('progress', message))))

    def set_report(self, report):
        self.report = report
        self.robot_status = build_robot_status(report)
        health = self.robot_status.health
        self.folder.set(str(report.source))
        self.cases.delete(*self.cases.get_children())
        self.cases.insert('', 'end', iid='overview', text='Overall assessment')
        self.cases.insert('', 'end', iid='joints', text='Joint health', open=True)
        for index, joint in enumerate(self.robot_status.joints):
            self.cases.insert('joints', 'end', iid=f'joint-{index}', text=f'{joint.joint} · {joint.status}')
        self.cases.insert('', 'end', iid='findings', text=f'Findings ({len(health.findings)})', open=False)
        for index, finding in enumerate(health.findings):
            self.cases.insert('findings', 'end', iid=f'finding-{index}', text=f'{finding.id} · {finding.severity} · {finding.title}')
        self.cases.insert('', 'end', iid='programs', text='Where the program struggles')
        self.cases.insert('', 'end', iid='timeline', text='Event timeline')
        self.cases.insert('', 'end', iid='actions', text='Recommendations')
        self.cases.insert('', 'end', iid='evidence', text='Technical incident evidence', open=False)
        for index, incident in enumerate(report.incidents):
            if len(report.incidents) <= APPENDIX_ALL_INCIDENTS or incident.flight_names:
                self.cases.insert('evidence', 'end', iid=str(index), text=f'{incident.id} · {incident.title}')
        self.cases.insert('', 'end', iid='sources', text='Coverage & sources')
        self.cases.selection_set('overview')
        self.notice.set('Recorded evidence supports this assessment. It does not replace a physical inspection or establish remaining joint life.')
        self.status.set(f'Robot Status ready · {self.robot_status.overall_status}' + (' · Coverage warnings: see Coverage & sources' if clean_warnings(report.warnings) else ''))
        self.set_busy(False)
        self.show_selection()

    def _display(self, title, sections):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('end', title + '\n', 'title')
        for heading, values in sections:
            values = unique_text(values)
            if not values:
                continue
            self.details.insert('end', heading + '\n', 'heading')
            for value in values:
                self.details.insert('end', value + '\n\n')
        self.details.configure(state='disabled')
        self.details.yview_moveto(0)

    def show_selection(self, _event=None):
        if self.report is None or not self.cases.selection():
            return
        selected = self.cases.selection()[0]
        status = self.robot_status
        health = status.health
        if selected == 'overview':
            numbers = []
            if health.findings:
                concern = [row.joint for row in health.joints if row.status in ('Critical', 'Attention required')]
                numbers = [f'{health.bursts:,} logged events from {health.period}; {len(concern)} of 6 joints need attention; '
                           f'{health.matched_recordings} of {self.report.recording_count} recordings matched to events.',
                           'Findings by severity: ' + ' · '.join(f'{name} {count}' for name, count in health.counts())]
            gaps = clean_warnings(self.report.warnings)
            if len(gaps) > 3:
                gaps = gaps[:3] + [f'{len(gaps)-3} more in Coverage & sources.']
            self._display('Overall Assessment', [(status.overall_status, status.assessment), ('Key numbers', numbers),
                          ('Top risks', [f'{f.id} · {f.severity} · {f.title}: {f.what}' for f in health.findings[:3]]),
                          ('Gaps in the data', gaps)])
        elif selected == 'joints':
            rows = {row.joint: row for row in health.joints}
            self._display('Joint health', [(f'{joint.joint} · {joint.status}', [
                (rows[joint.joint].notes if joint.joint in rows else joint.summary) +
                (f' · {rows[joint.joint].events} events, trend {rows[joint.joint].trend.lower()}' if rows.get(joint.joint) and rows[joint.joint].events else '')])
                for joint in status.joints])
        elif selected.startswith('joint-'):
            joint = status.joints[int(selected.split('-')[1])]
            row = next((r for r in health.joints if r.joint == joint.joint), None)
            own = [f for f in health.findings if f.joint == joint.joint]
            self._display(f'{joint.joint} · {joint.status}', [('Assessment', [joint.summary]),
                          ('Findings', [f'{f.id} · {f.severity} · {f.category}: {f.count} events, {f.period}, trend {f.trend.lower()}. Where: {f.where}' for f in own]),
                          ('Recorded metrics', row.metrics if row else []),
                          ('Recommended checks', joint.recommendations), ('Limits of this assessment', joint.limitations)])
        elif selected == 'findings':
            self._display('Findings', [(f'{f.id} · {f.severity}', [f'{f.title}: {f.count} events, trend {f.trend.lower()}. {f.where}'])
                                       for f in health.findings] or [('None', ['No classified controller warnings in these records.'])])
        elif selected.startswith('finding-'):
            f = health.findings[int(selected.split('-')[1])]
            info = f.info
            sections = [('What', [f.what]), ('Where', [f.where])]
            if info:
                sections += [('Likely cause', [info[2]]), ('Why it matters', [info[3]]),
                             ('Recommended fix', [' '.join(t for t in (info[4], info[6], info[5]) if t)])]
            sections += [('Evidence', [f'Codes: {", ".join(f.codes)}', f'Trend: {f.trend} ({f.trend_counts[0]} → {f.trend_counts[1]} events, first → second half)'
                                       if f.trend in ('Rising', 'Falling', 'Stable') else '', *(f'Recording: {n}' for n in f.recordings)]),
                         ('Basis', [f.basis + (f'. Reference: {info[7]} ({info[8]})' if info else '')])]
            self._display(f'{f.id} · {f.title}', sections)
        elif selected == 'programs':
            sections = [(f'{h.rank}. {h.location} · {h.count} events', [h.types, 'Likely cause: ' + h.cause, 'Findings: ' + ', '.join(h.findings)
                                                                        + (' · Program Check: ' + ', '.join(h.program_refs) if h.program_refs else '')])
                        for h in health.hot_spots]
            for check in health.program_checks:
                sections.append((f'Program Check of {check["program"]} · score {check["score"]}/100',
                                 [f'Program saved in {check["recording"]}.'] +
                                 [f'{f["id"]} · {f["severity"]} · {f["category"]} · {f["location"]}: {f["issue"]}' for f in check['findings']]))
            if not health.hot_spots:
                sections += [(area.area, [f'Program: {area.program or "Not identified"}', area.summary] + area.recommendations)
                             for area in status.program_areas]
            self._display('Where the program struggles', sections or [('More evidence needed',
                          ['No program location is established by these files. A flight recording at the time of an event is needed.'])])
        elif selected == 'timeline':
            self._display('Event timeline', [(when, [f'{what} · {detail}' + (f' · {finding}' if finding else '')])
                                             for when, what, detail, finding in health.timeline]
                          or [('No events', ['No dated controller events in these records.'])])
        elif selected == 'actions':
            groups = [('Program changes', health.program), ('Maintenance and hardware', health.maintenance),
                      ('Application and environment', health.application), ('Further data needed', health.data_needed),
                      ('Monitoring', health.monitoring)]
            if not any(values for _, values in groups):
                groups = [('Next checks', status.actions)]
            self._display('Recommendations', [(title, [f'{n}. {v}' for n, v in enumerate(values, 1)]) for title, values in groups])
        elif selected == 'evidence':
            shown = len(self.cases.get_children('evidence'))
            self._display('Technical incident evidence', [('Supporting detail',
                          ['Select an incident to inspect its controller messages, measurements and matching confidence. This detail is optional in the client PDF.',
                           f'{shown} of {len(self.report.incidents)} incidents are listed' + (' (those with a flight recording); the rest are counted in Findings.'
                                                                                               if shown < len(self.report.incidents) else '.')])])
        elif selected == 'sources':
            self._display('Coverage & sources', [('Coverage', status.coverage + self.report.coverage),
                          ('Gaps in the data', clean_warnings(self.report.warnings)),
                          ('How findings are rated', METHOD),
                          ('Evidence files', self.report.sources), ('Technical references',
                           [f'{ref.get("title", "Reference")}\n{ref.get("url", "")}' for ref in self.report.references])])
        else:
            incident = self.report.incidents[int(selected)]
            self._display(f'{incident.id} · {incident.title}', [('Context', [incident.time or 'Time unavailable',
                          ', '.join(incident.codes) or 'No associated controller code', incident.confidence, incident.window_reason])]
                          + incident_sections(incident) + [('Source evidence', incident.provenance)])

    def export(self):
        if self.report is None or self.busy:
            return
        destination = filedialog.asksaveasfilename(parent=self, title='Export Robot Status report',
                      initialfile='robot_status_report.pdf', initialdir=start_folder(self.app, 'reports'), defaultextension='.pdf', filetypes=[('PDF report', '*.pdf')])
        if destination:
            from motionstudio.diagnostics.pdf import export_diagnostic_pdf
            report = self.report
            metadata = {'client': self.client_name.get().strip(), 'robot_label': self.robot_label.get().strip(),
                        'prepared_by': self.prepared_by.get().strip()}
            include_evidence = self.include_evidence.get()
            self.status.set('Creating Robot Status PDF…')
            self._submit('exported', lambda: export_diagnostic_pdf(report, destination, self._check_cancel,
                         metadata=metadata, include_evidence=include_evidence))

    def cancel(self):
        self.cancelled.set()
        if self.download_job is not None:
            self.download_job.cancel()
        if self.busy:
            self.status.set('Cancelling… completed downloads will be retained.')

    def poll(self):
        self.sync_connection()
        job = self.download_job
        if job is not None:
            while not job.events.empty():
                kind, result = job.events.get_nowait()
                if kind == 'host_key':
                    try:
                        title, message = host_key_prompt(result, 'Robot Status collection')
                        result.accepted = not self.cancelled.is_set() and messagebox.askyesno(
                            title, message, parent=self.app.root if self.embedded else self, default='no')
                    finally:
                        result.answered.set()
                elif kind == 'status':
                    self.status.set(str(result))
                elif kind in ('collected', 'cancelled', 'error'):
                    self.download_job = None
                    path = result.get('path')
                    if path:
                        self.folder.set(str(path))
                    if kind == 'collected' and not self.cancelled.is_set():
                        self.analyze(path, collected=result.get('message', ''))
                    else:
                        self.set_busy(False)
                        self.status.set(result.get('message') or 'Collection stopped.')
                        self._display('Collection stopped', [('Result', [result.get('message', '')]),
                                      ('Coverage', result.get('warnings', [])),
                                      ('Retained dataset', [f'{path}\nChoose this folder in Local folder mode to analyze retained files.'] if path else [])])
                    break
        while not self.events.empty():
            kind, result = self.events.get_nowait()
            if kind == 'progress':
                if not self.cancelled.is_set():
                    self.status.set(str(result))
                continue
            self.set_busy(False)
            if kind == 'report':
                if self.cancelled.is_set():
                    self.status.set('Cancelled. Source files have been retained.')
                else:
                    self.set_report(result)
            elif kind == 'exported':
                self.status.set(f'Report saved: {result}')
            elif kind == 'cancelled':
                self.status.set('Cancelled. Source files have been retained.')
            elif kind == 'error':
                self.status.set(f'Review failed: {result}')
                if self.report is None:
                    self._display('Review could not finish', [('Details', [str(result)])])
        if not self.closed:
            self.after_id = self.after(75, self.poll)

    def hide(self):
        self.cancel()
        self.withdraw()

    def shutdown(self):
        self.closed = True
        self.cancel()
        self.progress.stop()
        self.after_cancel(self.after_id)
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.destroy()
