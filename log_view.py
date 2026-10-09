"""Native desktop log analysis; charts never launch a browser."""
import csv
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from log_data import JOINTS, chart_series, select_events
from log_transfer import DEFAULT_LOG_PATH, LogConnection, LogJob
from ui_theme import BACKGROUND

COLORS = ('#187eab', '#c27c22', '#409580', '#995a9c', '#db6854', '#596eab')


class LogWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.configure(background=BACKGROUND)
        self.app = app
        self.title('UR Motion Studio · Logs & graphs')
        self.geometry('1240x850')
        self.minsize(1060, 740)
        self.job = None
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
        self.start_date = tk.StringVar()
        self.end_date = tk.StringVar()
        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        heading = ttk.Frame(self, padding=(22, 18))
        heading.grid(row=0, column=0, columnspan=2, sticky='ew')
        ttk.Label(heading, text='Logs & graphs', style='Title.TLabel').pack(side='left')
        ttk.Button(heading, text='Back to simulator', command=self.hide).pack(side='right')

        sidebar = ttk.Frame(self, padding=(22, 8, 22, 12), width=280)
        sidebar.grid(row=1, column=0, sticky='ns')
        ttk.Label(sidebar, text='ROBOT CONNECTION', style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        for label, variable in [('Robot IP address', self.ip), ('SSH username', self.username),
                                ('SSH password', self.password), ('Remote log file', self.remote_path)]:
            ttk.Label(sidebar, text=label).pack(anchor='w', pady=(4, 4))
            entry = ttk.Entry(sidebar, textvariable=variable, width=29,
                              show='•' if variable is self.password else '')
            entry.pack(fill='x')
        ttk.Label(sidebar, text='Includes matching .bak history.\nPassword stays in memory.',
                  style='Muted.TLabel').pack(anchor='w', pady=8)
        self.fetch_button = ttk.Button(sidebar, text='Fetch log & graph', style='Accent.TButton', command=self.fetch)
        self.fetch_button.pack(fill='x', pady=(4, 8))
        self.open_button = ttk.Button(sidebar, text='Open local log…', command=self.open_file)
        self.open_button.pack(fill='x')
        activity = ttk.Frame(sidebar)
        activity.pack(fill='x', pady=8)
        self.cancel_button = ttk.Button(activity, text='Cancel', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='right')
        self.progress = ttk.Progressbar(activity, mode='indeterminate', length=80)
        self.progress.pack(side='left', fill='x', expand=True, padx=(0, 8))
        ttk.Separator(sidebar).pack(fill='x', pady=12)
        ttk.Label(sidebar, text='EXPORT', style='Section.TLabel').pack(anchor='w', pady=(0, 12))
        self.csv_button = ttk.Button(sidebar, text='Save filtered incidents…', command=self.export_csv, state='disabled')
        self.csv_button.pack(fill='x')

        body = ttk.Frame(self, padding=(0, 8, 22, 10))
        body.grid(row=1, column=1, sticky='nsew')
        ttk.Label(body, textvariable=self.summary, style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, textvariable=self.coverage, style='Muted.TLabel', wraplength=730).pack(anchor='w', pady=(5, 14))
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
        self.figure = Figure(figsize=(7, 4), dpi=100, facecolor='#ffffff', layout='constrained')
        self.axes = self.figure.add_subplot()
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart)
        self.toolbar = NavigationToolbar2Tk(self.canvas, chart, pack_toolbar=False)
        self.toolbar.configure(background=BACKGROUND)
        for widget in self.toolbar.winfo_children():
            try:
                widget.configure(background=BACKGROUND)
                if isinstance(widget, (tk.Button, tk.Checkbutton)):
                    widget.configure(relief='flat', borderwidth=0, highlightthickness=0,
                                     activebackground='#e5edf3')
            except tk.TclError:
                pass
        self.toolbar.pack(side='bottom', fill='x')
        self.canvas.get_tk_widget().pack(fill='both', expand=True, pady=(8, 0))
        self.table = self.make_table(incidents, ('Time', 'Joint', 'Code', 'Type', 'Message'), (155, 90, 90, 130, 400))
        self.table.bind('<<TreeviewSelect>>', self.show_message)
        self.message = tk.StringVar(value='Select an incident to read its full message. Showing up to 500 latest matches.')
        ttk.Label(incidents, textvariable=self.message, wraplength=700).pack(fill='x', pady=8)
        ttk.Label(health, text='Full-log evidence · Independent of chart filters', style='Section.TLabel').pack(anchor='w')
        ttk.Label(health, text='Signals warrant investigation; they do not predict joint failure. Counts are not normalized by operating hours.',
                  wraplength=700, style='Muted.TLabel').pack(anchor='w', pady=8)
        self.health_table = self.make_table(health, ('Joint', 'Assessment', 'Incidents', 'Health', 'Peak stops / 8h'), (90, 220, 75, 65, 130))
        self.health_context = tk.StringVar()
        ttk.Label(health, textvariable=self.health_context, wraplength=700).pack(anchor='w', pady=10)
        ttk.Label(self, textvariable=self.status, padding=(22, 10), wraplength=1100).grid(row=2, column=0, columnspan=2, sticky='ew')
        self.draw_chart([])
        self.poll_id = self.after(100, self.poll)

    @staticmethod
    def make_table(parent, columns, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree = ttk.Treeview(frame, columns=columns, show='headings', height=7)
        for name, width in zip(columns, widths):
            tree.heading(name, text=name)
            tree.column(name, width=width, minwidth=60)
        tree.grid(row=0, column=0, sticky='nsew')
        ybar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        ybar.grid(row=0, column=1, sticky='ns')
        xbar = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        xbar.grid(row=1, column=0, sticky='ew')
        tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        return tree

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
            self.status.set(str(error))
            return
        self.begin(LogJob(connection=settings))

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
                elif kind in ('loaded', 'error', 'cancelled'):
                    cancelled = job.stopped.is_set()
                    self.finish()
                    if cancelled or kind == 'cancelled':
                        self.status.set('Cancelled · Previous log retained')
                    elif kind == 'error':
                        self.status.set('Log not loaded · ' + value)
                    else:
                        self.set_data(value)
                    break
                elif kind == 'status' and not job.stopped.is_set():
                    self.status.set(value)
        if not self.closed:
            self.poll_id = self.after(100, self.poll)

    def set_data(self, data):
        self.data = data
        self.code_combo.configure(values=('All codes',) + tuple(sorted({e['code'] for e in data.events})))
        self.health_table.delete(*self.health_table.get_children())
        for card in data.diagnostics['all']['cards']:
            self.health_table.insert('', 'end', values=(card['joint'], card['status'], card['total'], card['health'], card['peak']))
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
            self.status.set(str(error))
            return
        self.selected = selected
        self.summary.set(f'{len(selected):,} incidents  ·  {len({e["joint"] for e in selected})} joints  ·  {len({e["code"] for e in selected})} codes')
        meta = self.data.metadata
        warning = f'\n{meta["warning"]}' if meta.get('warning') else ''
        self.coverage.set(f'{self.data.source}\nLog coverage: {meta["first_date"] or "unavailable"} — {meta["last_date"] or "unavailable"}' + warning)
        self.table.delete(*self.table.get_children())
        for index, e in enumerate(selected[:500]):
            self.table.insert('', 'end', iid=str(index), values=(e['time'], e['joint'], e['code'], e['category'], e['message']))
        self.message.set(f'Showing {min(500, len(selected)):,} of {len(selected):,} matches. Select a row for its full message; CSV includes every match.')
        self.csv_button.configure(state='normal' if selected else 'disabled')
        self.draw_chart(selected)
        self.status.set(f'Ready · {self.data.skipped:,} malformed records skipped · {meta["unmapped"]:,} signals without a joint mapping · Monitored joint codes only')

    def draw_chart(self, events):
        axes = self.axes
        axes.clear()
        if not events:
            axes.text(.5, .5, 'No matching incidents' if self.data else 'Fetch or open a robot log to begin',
                      ha='center', va='center', transform=axes.transAxes, color='#536a7b', fontsize=13)
            axes.set_axis_off()
        else:
            axes.set_axis_on()
            labels, series, period = chart_series(events, self.period.get(), self.start_date.get().strip(),
                                                  self.end_date.get().strip(), self.split_code.get())
            x = list(range(len(labels)))
            bottom = [0] * len(labels)
            for index, (name, counts) in enumerate(series.items()):
                joint = name.split(' · ')[0]
                color = COLORS[JOINTS.index(joint)] if joint in JOINTS else '#73828c'
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
            axes.set_xticks(ticks, [labels[i].isoformat() for i in ticks], rotation=15, ha='right', fontsize=8)
            axes.set_ylabel('Incidents', color='#536a7b')
            axes.set_title(f'Joint incidents · {period}', loc='left', fontsize=11, color='#23394b')
            axes.yaxis.set_major_locator(MaxNLocator(integer=True))
            axes.set_ylim(bottom=0)
            axes.grid(axis='y', alpha=.18)
            axes.set_axisbelow(True)
            axes.spines[['top', 'right']].set_visible(False)
            axes.legend(loc='upper left', fontsize=8, ncols=min(3, len(series)), framealpha=.9)
        self.toolbar.update()
        self.canvas.draw_idle()

    def show_message(self, _event=None):
        selection = self.table.selection()
        if selection:
            event = self.selected[int(selection[0])]
            self.message.set(f'{event["code"]} · {event["description"]}\n{event["message"]}')

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
