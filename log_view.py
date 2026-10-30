"""Native desktop log analysis; charts never launch a browser."""
import csv
import queue
from pathlib import Path
from uuid import uuid4
import tkinter as tk
from tkinter import ttk, filedialog, font
import ui_dialogs as messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from log_data import JOINTS, chart_series, select_events
from log_transfer import DEFAULT_LOG_PATH, LogConnection, LogJob
from backup_transfer import BackupJob
from ui_theme import (BACKGROUND, PANEL, INK, MUTED, BORDER,
                      CHART_SERIES, style_toolbar, style_tree, status_color)

COLORS = CHART_SERIES


class LogWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.configure(background=BACKGROUND)
        self.app = app
        self.title('UR Motion Studio · Logs & graphs')
        self.geometry('1240x850')
        self.minsize(1060, 740)
        self.job = None
        self.save_directory = None
        self.data = None
        self.selected = []
        self.closed = False
        self.protocol('WM_DELETE_WINDOW', self.hide)
        self.ip = tk.StringVar(value=app.ip.get() or '10.22.33.81')
        self.username = tk.StringVar(value=app.username.get() or 'root')
        self.password = tk.StringVar(value=app.password.get())
        self.remote_path = tk.StringVar(value=DEFAULT_LOG_PATH)
        self.status = tk.StringVar(value='Ready · Fetch a robot log or open a local file')
        self.summary = tk.StringVar(value='No log loaded')
        self.coverage = tk.StringVar(value='Your charts will appear here after loading a log.')
        self.joint = tk.StringVar(value='All joints')
        self.code = tk.StringVar(value='All codes')
        self.category = tk.StringVar(value='All incidents')
        self.period = tk.StringVar(value='Day')
        self.chart_kind = tk.StringVar(value='Bar')
        self.split_code = tk.BooleanVar()
        self.aggregate = tk.BooleanVar(value=False)
        self.backup_programs = tk.BooleanVar(value=True)
        self.backup_apps = tk.BooleanVar(value=True)
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, padding=(18, 14), style='Toolbar.TFrame')
        heading.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Label(heading, text='Logs & graphs', style='Panel.Title.TLabel').pack(side='left')
        ttk.Button(heading, text='Back to simulator', command=self.hide).pack(side='right')
        ttk.Button(heading, text='How to', command=lambda: self.app.open_help('Logs & graphs')).pack(side='right', padx=10)

        sidebar = ttk.Frame(self, padding=(16, 10), width=280, style='Panel.TFrame')
        sidebar.grid(row=1, column=0, sticky='ns', padx=(12, 14), pady=12)
        ttk.Label(sidebar, text='ROBOT CONNECTION', style='Panel.Section.TLabel').pack(anchor='w', pady=(0, 4))
        for label, variable in [('Robot IP address', self.ip), ('SSH username', self.username),
                                ('SSH password', self.password), ('Remote log file', self.remote_path)]:
            ttk.Label(sidebar, text=label, style='Panel.TLabel').pack(anchor='w', pady=(3, 2))
            entry = ttk.Entry(sidebar, textvariable=variable, width=29,
                              show='•' if variable is self.password else '')
            entry.pack(fill='x')
        ttk.Label(sidebar, text='Includes matching .bak history.\nPassword stays in memory.',
                  style='Panel.Muted.TLabel').pack(anchor='w', pady=6)
        self.fetch_button = ttk.Button(sidebar, text='Fetch log & graph', style='Accent.TButton', command=self.fetch)
        self.fetch_button.pack(fill='x', pady=(4, 6))
        self.open_button = ttk.Button(sidebar, text='Open local log…', command=self.open_file)
        self.open_button.pack(fill='x')
        backups = ttk.Frame(sidebar, style='Panel.TFrame')
        backups.pack(fill='x', pady=(8, 0))
        backups.columnconfigure((0, 1), weight=1)
        ttk.Label(backups, text='BACKUP CONTENTS', style='Section.TLabel').grid(row=0, column=0, columnspan=2, sticky='w')
        ttk.Checkbutton(backups, text='Programs', variable=self.backup_programs).grid(row=1, column=0, sticky='w')
        ttk.Checkbutton(backups, text='Apps', variable=self.backup_apps).grid(row=1, column=1, sticky='w')
        self.backup_button = ttk.Button(backups, text='Make backup…', command=self.make_backup)
        self.backup_button.grid(row=2, column=0, sticky='ew', padx=(0, 6))
        self.full_backup_button = ttk.Button(backups, text='Full backup…', command=lambda: self.make_backup(full=True))
        self.full_backup_button.grid(row=2, column=1, sticky='ew')
        activity = ttk.Frame(sidebar, style='Panel.TFrame')
        activity.pack(fill='x', pady=6)
        self.cancel_button = ttk.Button(activity, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='right')
        self.progress = ttk.Progressbar(activity, mode='indeterminate', length=80)
        self.progress.pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Separator(sidebar).pack(fill='x', pady=8)
        ttk.Label(sidebar, text='EXPORT', style='Panel.Section.TLabel').pack(anchor='w', pady=(0, 8))
        self.csv_button = ttk.Button(sidebar, text='Save filtered incidents…', command=self.export_csv, state='disabled')
        self.csv_button.pack(fill='x')

        body = ttk.Frame(self, padding=(0, 12, 12, 10))
        body.grid(row=1, column=1, sticky='nsew')
        self.summary_label = ttk.Label(body, textvariable=self.summary, style='Title.TLabel', wraplength=730)
        self.summary_label.pack(anchor='w')
        self.coverage_label = ttk.Label(body, textvariable=self.coverage, style='Muted.TLabel', wraplength=730)
        self.coverage_label.pack(anchor='w', pady=(5, 14))
        filters = ttk.Frame(body)
        filters.pack(fill='x')
        self.code_combo = None
        for col, (label, variable, values) in enumerate([
            ('Joint', self.joint, ('All joints',) + JOINTS),
            ('Incident type', self.category, ('All incidents', 'Collision', 'Position deviation', 'Payload warning', 'Joint health signal')),
            ('Code', self.code, ('All codes',))]):
            filters.columnconfigure(col, weight=1)
            ttk.Label(filters, text=label).grid(row=0, column=col, sticky='w')
            combo = ttk.Combobox(filters, textvariable=variable, values=values, state='readonly', width=18)
            combo.grid(row=1, column=col, sticky='ew', padx=(0, 8), pady=(4, 10))
            combo.bind('<<ComboboxSelected>>', self.refresh)
            if variable is self.code:
                self.code_combo = combo
        dates = ttk.Frame(body)
        dates.pack(fill='x', pady=(0, 12))
        for label, variable in [('From', self.start_date), ('To', self.end_date)]:
            ttk.Label(dates, text=label).pack(side='left', padx=(0, 6))
            entry = ttk.Entry(dates, textvariable=variable, width=12)
            entry.pack(side='left', padx=(0, 10))
            entry.bind('<Return>', self.refresh)
        ttk.Button(dates, text='Apply dates', command=self.refresh).pack(side='left')
        ttk.Button(dates, text='Reset filters', command=self.reset).pack(side='right')

        self.tabs = ttk.Notebook(body)
        self.tabs.pack(fill='both', expand=True)
        chart = ttk.Frame(self.tabs, padding=8)
        incidents = ttk.Frame(self.tabs, padding=8)
        health = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(chart, text='Incident trends')
        self.tabs.add(incidents, text='Incident details')
        self.tabs.add(health, text='Joint overview')
        options = ttk.Frame(chart)
        options.pack(fill='x')
        for variable, values in [(self.period, ('Day', 'Week', 'Month')), (self.chart_kind, ('Bar', 'Line', 'Scatter'))]:
            combo = ttk.Combobox(options, textvariable=variable, values=values, state='readonly', width=9)
            combo.pack(side='left', padx=(0, 8))
            combo.bind('<<ComboboxSelected>>', self.refresh)
        ttk.Checkbutton(options, text='Separate codes', variable=self.split_code, command=self.refresh).pack(side='left')
        ttk.Checkbutton(options, text='Aggregate', variable=self.aggregate, command=self.refresh).pack(side='left', padx=(8, 0))
        self.figure = Figure(figsize=(7, 4), dpi=100, facecolor=BACKGROUND, layout='constrained')
        self.axes = self.figure.add_subplot()
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart)
        self.toolbar = NavigationToolbar2Tk(self.canvas, chart, pack_toolbar=False)
        style_toolbar(self.toolbar)
        self.toolbar.pack(side='bottom', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True, pady=(8, 0))
        self.message = tk.StringVar(value='Select an incident to read its full message. Showing up to 500 latest matches.')
        self.message_label = ttk.Label(incidents, textvariable=self.message, wraplength=700)
        self.message_label.pack(side='bottom', fill='x', pady=8)
        self.table = self.make_table(incidents, ('Time', 'Joint', 'Code', 'Type', 'Message'), (175, 75, 80, 110, 310))
        self.table.bind('<<TreeviewSelect>>', self.show_message)
        ttk.Label(health, text='Full-log evidence · Independent of chart filters', style='Section.TLabel').pack(anchor='w')
        self.health_notice = ttk.Label(health, text='Signals warrant investigation; they do not predict joint failure. Counts are not normalized by operating hours.',
                                      wraplength=700, style='Muted.TLabel')
        self.health_notice.pack(anchor='w', pady=8)
        self.health_context = tk.StringVar()
        self.health_context_label = ttk.Label(health, textvariable=self.health_context, wraplength=700)
        self.health_context_label.pack(side='bottom', anchor='w', pady=10)
        self.health_table = self.make_table(health, ('Joint', 'Assessment', 'Incidents', 'Health', 'Peak stops / 8h'), (90, 220, 75, 65, 130))
        status_bar = ttk.Frame(self, style='Panel.TFrame')
        status_bar.grid(row=2, column=0, columnspan=2, sticky='ew')
        status_bar.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_bar, textvariable=self.status, padding=(18, 10),
                                      style='Panel.Muted.TLabel', wraplength=1100)
        self.status_label.grid(row=0, column=0, sticky='ew')
        ttk.Button(status_bar, text='Status details…', command=self.show_status_details).grid(
            row=0, column=1, padx=(0, 12), pady=8)
        self._status_trace = self.status.trace_add('write', lambda *_:
            self.status_label.configure(foreground=status_color(self.status.get())))
        self.bind('<Configure>', self._resize)
        self.draw_chart([])
        self.poll_id = self.after(100, self.poll)

    @staticmethod
    def make_table(parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=5)
        style_tree(tree)
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=60)
        tree.grid(row=0, column=0, sticky='nsew')
        ybar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        ybar.grid(row=0, column=1, sticky='ns')
        xbar = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        xbar.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        def fit_columns(width):
            # Incident messages remain available below the table; reserve useful
            # widths for every column instead of forcing horizontal scrolling.
            available = max(1, width - 4)
            fixed_time = getattr(tree, '_time_width', 175) if 'Time' in columns else 0
            total = sum(width for name, width in zip(columns, widths) if name != 'Time')
            for name, width in zip(columns, widths):
                tree.column(name, width=fixed_time if name == 'Time' else
                            max(60, int((available-fixed_time) * width / total)))
        tree.bind('<Configure>', lambda event: fit_columns(event.width))
        tree._fit_columns = fit_columns
        return tree

    def _resize(self, event):
        if event.widget is self:
            width = max(500, self.winfo_width() - 380)
            for widget in (self.summary_label, self.coverage_label, self.message_label,
                           self.health_notice, self.health_context_label):
                widget.configure(wraplength=width)
            self.status_label.configure(wraplength=max(300, self.winfo_width()-210))

    def show_status_details(self):
        messagebox.showinfo('Transfer status', self.status.get(), parent=self)

    def hide(self):
        self.withdraw()
        self.app.root.lift()

    def fetch(self):
        if self.job:
            return
        try:
            settings = LogConnection(self.ip.get().strip(), self.username.get().strip(),
                                     self.password.get(), self.remote_path.get().strip())
        except ValueError as error:
            self.status.set('Connection error: ' + str(error))
            messagebox.showerror('Cannot fetch robot logs', str(error), parent=self)
            return
        save_path = None
        if messagebox.askyesno('Save robot logs?', 'Would you also like to save the downloaded log files on this PC?', parent=self):
            save_path = filedialog.asksaveasfilename(
                parent=self, title='Save robot logs', initialfile='log_history.txt',
                initialdir=str(self.save_directory) if self.save_directory else None,
                defaultextension='.txt', filetypes=[('Robot log', '*.txt')]) or None
            if save_path:
                partner = Path(save_path).with_suffix('.bak')
                if partner == Path(save_path):
                    self.status.set('Choose a .txt filename to save both log files.')
                    return
                if partner.exists() and not messagebox.askyesno(
                        'Replace saved backup log?', f'Also replace {partner} if the robot has a backup log?',
                        parent=self, default='no'):
                    return
                self.save_directory = Path(save_path).parent
        self.begin(LogJob(connection=settings, save_path=save_path))

    def make_backup(self, *, full=False):
        if self.job:
            self.status.set('A transfer is already running. Wait for it to finish or cancel it before starting a backup.')
            return
        try:
            settings = LogConnection(self.ip.get().strip(), self.username.get().strip(), self.password.get(),
                                     self.remote_path.get().strip() if full else DEFAULT_LOG_PATH)
            flight_directory = '/root/flightreports'
            retrieval = getattr(self.app, 'flight_retrieval_window', None)
            if full and retrieval is not None and retrieval.winfo_exists():
                flight_directory = retrieval.directory.get().strip()
            sources = ('/programs', '/data/apps') if full else tuple(
                path for path, selected in (('/programs', self.backup_programs),
                                           ('/data/apps', self.backup_apps)) if selected.get())
            job = BackupJob(connection=settings, sources=sources, full=full, flight_directory=flight_directory)
        except ValueError as error:
            self.status.set('Connection error: ' + str(error) + (' Then choose Full backup….' if full else ''))
            messagebox.showerror('Cannot make robot backup', str(error), parent=self)
            return
        if full:
            folder = filedialog.askdirectory(parent=self, title='Save full robot backup in folder',
                initialdir=str(self.save_directory) if self.save_directory else None, mustexist=True)
            if not folder:
                self.status.set('Full backup cancelled · No robot archive created.')
                return
            self.save_directory = Path(folder)
        self.begin(job)

    def open_file(self):
        if self.job:
            return
        path = filedialog.askopenfilename(parent=self, title='Open robot log',
                                          filetypes=[('Robot logs', '*.txt *.bak *.log'), ('All files', '*')])
        if path:
            self.begin(LogJob(path=path))

    def begin(self, job):
        self.job = job
        self.status.set('Starting…')
        self.fetch_button.configure(state='disabled')
        self.open_button.configure(state='disabled')
        self.backup_button.configure(state='disabled')
        self.full_backup_button.configure(state='disabled')
        self.cancel_button.configure(state='normal')
        self.progress.start(12)
        job.start()

    def cancel(self):
        if self.job:
            self.job.cancel()
            self.cancel_button.configure(state='disabled')
            self.status.set('Cancelling…')

    def finish(self):
        self.job = None
        self.progress.stop()
        self.fetch_button.configure(state='normal')
        self.open_button.configure(state='normal')
        self.backup_button.configure(state='normal')
        self.full_backup_button.configure(state='normal')
        self.cancel_button.configure(state='disabled')

    def poll(self):
        job = self.job
        if job:
            while True:
                try:
                    kind, value = job.events.get_nowait()
                except queue.Empty:
                    break
                if kind == 'host_key':
                    if not job.stopped.is_set():
                        title = 'Replace saved robot SSH key?' if value.replacement else 'Verify robot SSH host'
                        prompt = (value.fingerprint + '\n\nRemove the old key and update to the new one?'
                                  if value.replacement else
                                  f'{value.hostname}\n\n{value.fingerprint}\n\nVerify this fingerprint with your robot administrator. Trust for this connection?')
                        value.accepted = messagebox.askyesno(title, prompt, parent=self, default='no')
                    value.answered.set()
                elif kind == 'backup_destination':
                    try:
                        if not job.stopped.is_set():
                            if self.save_directory:
                                destination = self.save_directory / value.filename
                                while destination.exists():
                                    destination = self.save_directory / f'{Path(value.filename).stem}-{uuid4().hex[:8]}.tar'
                                value.path = str(destination)
                            else:
                                value.path = filedialog.asksaveasfilename(
                                    parent=self, title='Save robot backup', initialfile=value.filename,
                                    defaultextension='.tar', filetypes=[('Tar archive', '*.tar')]) or None
                                if value.path:
                                    self.save_directory = Path(value.path).parent
                    finally:
                        value.answered.set()
                elif kind == 'backup_finished':
                    self.finish()
                    self.status.set(value)
                    break
                elif kind in ('loaded', 'error', 'cancelled'):
                    cancelled = job.stopped.is_set()
                    self.finish()
                    if cancelled or kind == 'cancelled':
                        self.status.set('Cancelled · Previous log retained')
                    elif kind == 'error':
                        self.status.set('Log not loaded · ' + value)
                    else:
                        self.set_data(value)
                        if value.metadata.get('save_error'):
                            self.status.set('Graph loaded · Could not save logs: ' + value.metadata['save_error'])
                        elif value.metadata.get('saved_logs'):
                            self.status.set('Graph loaded · Saved logs: ' + ' + '.join(value.metadata['saved_logs']))
                    break
                elif kind == 'status' and not job.stopped.is_set():
                    self.status.set(value)
        if not self.closed:
            self.poll_id = self.after(100, self.poll)

    def set_data(self, data):
        self.data = data
        self.code_combo.configure(values=('All codes',) + tuple(sorted({e['code'] for e in data.events})))
        self.health_table.delete(*self.health_table.get_children())
        for index, card in enumerate(data.diagnostics['all']['cards']):
            self.health_table.insert('', 'end', values=(card['joint'], card['status'], card['total'], card['health'], card['peak']),
                                     tags=('odd' if index % 2 else 'even',))
        self.health_context.set(f"{data.diagnostics['all']['payload_context']:,} payload-context records (C162A0). "
                                'Peak stops use rolling wall-clock windows within each log session.')
        self.reset()

    def reset(self):
        self.joint.set('All joints')
        self.code.set('All codes')
        self.category.set('All incidents')
        self.start_date.set('')
        self.end_date.set('')
        self.refresh()

    def refresh(self, _event=None):
        if self.data is None:
            return
        try:
            selected = select_events(self.data.events, self.joint.get(), self.code.get(), self.category.get(),
                                     self.start_date.get().strip(), self.end_date.get().strip())
        except ValueError as error:
            self.status.set('Date filter error: ' + str(error))
            return
        self.selected = selected
        self.summary.set(f'{len(selected):,} incidents  ·  {len({e["joint"] for e in selected})} joints  ·  {len({e["code"] for e in selected})} codes')
        meta = self.data.metadata
        warning = f'\n{meta["warning"]}' if meta.get('warning') else ''
        self.coverage.set(f'{self.data.source}\nLog coverage: {meta["first_date"] or "unavailable"} — {meta["last_date"] or "unavailable"}' + warning)
        self.table.delete(*self.table.get_children())
        # Preserve fractional timestamps at native font sizes; give the message
        # column the remainder because its full value is shown below the table.
        table_font = font.nametofont('TkDefaultFont', root=self)
        self.table._time_width = max(175, max((table_font.measure(e['time']) + 16 for e in selected[:500]), default=175))
        self.table._fit_columns(self.table.winfo_width())
        for index, e in enumerate(selected[:500]):
            self.table.insert('', 'end', iid=str(index), values=(e['time'], e['joint'], e['code'], e['category'], e['message']),
                              tags=('odd' if index % 2 else 'even',))
        self.message.set(f'Showing {min(500, len(selected)):,} of {len(selected):,} matches. Select a row for its full message; CSV includes every match.')
        self.csv_button.configure(state='normal' if selected else 'disabled')
        self.draw_chart(selected)
        self.status.set(f'Ready · {self.data.skipped:,} malformed records skipped · {meta["unmapped"]:,} signals without a joint mapping · Monitored joint codes only')

    def draw_chart(self, events):
        axes = self.axes
        axes.clear()
        axes.set_facecolor(BACKGROUND)
        axes.figure.set_facecolor(BACKGROUND)
        axes.tick_params(colors=MUTED)
        for spine in axes.spines.values():
            spine.set_edgecolor(BORDER)
        if not events:
            axes.text(.5, .5, 'No matching incidents' if self.data else 'Fetch or open a robot log to begin',
                      ha='center', va='center', transform=axes.transAxes, color=MUTED, fontsize=13)
            axes.set_axis_off()
        else:
            axes.set_axis_on()
            labels, series, period = chart_series(events, self.period.get(), self.start_date.get().strip(),
                                                  self.end_date.get().strip(), self.split_code.get(),
                                                  aggregate=self.aggregate.get())
            x = list(range(len(labels)))
            bottom = [0] * len(labels)
            for index, (name, counts) in enumerate(series.items()):
                joint = name.split(' · ')[0]
                color = COLORS[JOINTS.index(joint)] if joint in JOINTS else MUTED
                if self.chart_kind.get() == 'Bar':
                    nonzero = [i for i, count in enumerate(counts) if count]
                    axes.bar(nonzero, [counts[i] for i in nonzero], bottom=[bottom[i] for i in nonzero],
                             color=color, label=name, width=.8,
                             hatch=('', '//', '..')[index % 3] if self.split_code.get() else None)
                    bottom = [a + b for a, b in zip(bottom, counts)]
                elif self.chart_kind.get() == 'Line':
                    axes.plot(x, counts, color=color, label=name, linewidth=1.8, marker='.', markersize=4)
                else:
                    axes.scatter(x, counts, color=color, label=name, s=18)
            ticks = list(range(0, len(labels), max(1, (len(labels) + 6) // 7)))
            axes.set_xticks(ticks, [str(labels[i]) for i in ticks], rotation=15, ha='right', fontsize=8)
            axes.set_ylabel('Cumulative incidents' if self.aggregate.get() else 'Incidents', color=MUTED)
            title = 'Cumulative joint incidents' if self.aggregate.get() else 'Joint incidents'
            axes.set_title(f'{title} · {period}', loc='left', fontsize=11, color=INK)
            axes.yaxis.set_major_locator(MaxNLocator(integer=True))
            axes.set_ylim(bottom=0)
            axes.grid(axis='y', color=BORDER, linewidth=.7)
            axes.set_axisbelow(True)
            axes.spines[['top', 'right']].set_visible(False)
            axes.legend(loc='upper left', fontsize=8, ncols=min(3, len(series)), framealpha=.9,
                        facecolor=PANEL, edgecolor=BORDER, labelcolor=INK)
        self.toolbar.update()
        self.canvas.draw_idle()

    def show_message(self, _event=None):
        selection = self.table.selection()
        if selection:
            event = self.selected[int(selection[0])]
            self.message.set(f'{event["time"]} · {event["joint"]} · {event["code"]} · {event["category"]}\n'
                             f'{event["description"]}\n{event["message"]}')

    def export_csv(self):
        path = filedialog.asksaveasfilename(parent=self, title='Save filtered incidents', defaultextension='.csv',
                                           initialfile='robot-incidents.csv', filetypes=[('CSV', '*.csv')])
        if not path:
            return
        fields = ('time', 'joint', 'code', 'category', 'description', 'message')
        try:
            with open(path, 'w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(fields)
                for event in self.selected:
                    writer.writerow([self.csv_cell(event[key]) for key in fields])
            self.status.set(f'Saved {len(self.selected):,} incidents to {path}')
        except OSError as error:
            self.status.set(f'Could not save CSV: {error}')

    @staticmethod
    def csv_cell(value):
        value = str(value)
        return "'" + value if value.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else value

    def shutdown(self):
        self.closed = True
        if self.job:
            self.job.cancel()
        self.after_cancel(self.poll_id)
        self.destroy()
