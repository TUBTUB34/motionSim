"""Persistent native workspace for an operator's commissioning/inspection notes."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog
import webbrowser

from commissioning import (DEPLOYMENT_CHECKS, METADATA_FIELDS, RECORD_NOTICE, STATES,
                           export_record_csv, load_record, new_record, review_summary,
                           save_record, today_utc)
import ui_theme as theme
from ui_theme import ScrollableFrame, style_text, style_tree
from workspace_panel import WorkspacePanel
import ui_dialogs as messagebox


class CommissioningPage(WorkspacePanel):
    def __init__(self, app, parent=None):
        super().__init__(app.root, page_host=parent,
                         on_show=lambda: app.navigate_panel(self),
                         on_hide=getattr(app, 'show_workspace', None))
        self.app = app
        self.title('UR Motion Studio · Commissioning & inspection')
        self.geometry('1100x800')
        self.protocol('WM_DELETE_WINDOW', self.withdraw)
        self.record = new_record()
        self.saved_record = new_record()
        self.path = None
        self.selected = None
        self._loading = False
        self.checks = {check.key: check for check in DEPLOYMENT_CHECKS}
        self.metadata = {key: tk.StringVar() for key in METADATA_FIELDS}
        self.status = tk.StringVar(value=STATES[0])
        self.summary = tk.StringVar(value=review_summary(self.record))
        self.file_status = tk.StringVar(value='New checklist · Save a file to keep this review')
        self.detail_title = tk.StringVar()
        self.guidance = tk.StringVar()
        self.sources = tk.StringVar()
        self.detail_group = tk.StringVar()
        self.filter = tk.StringVar(value='All items')
        self.error = tk.StringVar()
        self._build()
        for value in self.metadata.values():
            value.trace_add('write', self._edited)
        self.status.trace_add('write', self._edited)
        self.error.trace_add('write', self._error_changed)
        self._populate()

    def _build(self):
        header = ttk.Frame(self, padding=(18, 16, 18, 10))
        header.pack(fill='x')
        ttk.Label(header, text='Commissioning & inspection', style='Title.TLabel').pack(anchor='w')
        ttk.Label(header, text='Keep one review file per robot or application. Record evidence and follow up on open actions.',
                  style='Muted.TLabel', wraplength=850).pack(anchor='w', pady=(5, 0))
        footer = ttk.Frame(self, padding=(18, 10), style='Toolbar.TFrame')
        footer.pack(side='bottom', fill='x')
        actions = ttk.Frame(footer, style='Toolbar.TFrame')
        actions.pack(fill='x')
        ttk.Button(actions, text='New', command=self.new).pack(side='left')
        ttk.Button(actions, text='Open…', command=self.open).pack(side='left', padx=(6, 0))
        ttk.Button(actions, text='Save as…', command=lambda: self.save(force_dialog=True)).pack(side='right')
        self.save_button = ttk.Button(actions, text='Save checklist', style='Accent.TButton', command=self.save)
        self.save_button.pack(side='right', padx=6)
        ttk.Button(actions, text='Export CSV…', command=self.export).pack(side='right')
        ttk.Label(footer, textvariable=self.file_status, style='Muted.Panel.TLabel', wraplength=850).pack(
            anchor='w', pady=(7, 0))
        self.error_label = ttk.Label(footer, textvariable=self.error, style='Error.TLabel',
                                     background=theme.PANEL, wraplength=850)
        scroll = ScrollableFrame(self, padding=(18, 0, 18, 16))
        scroll.pack(fill='both', expand=True)
        self.body = body = scroll.content
        fields = ttk.Frame(body)
        fields.pack(fill='x', pady=(0, 10))
        for col in range(3):
            fields.columnconfigure(col, weight=1, uniform='metadata')
        for index, (key, label) in enumerate((('project', 'Project / application'), ('robot', 'Robot model / serial'),
                ('reviewer', 'Reviewed by'), ('review_date', 'Review date (YYYY-MM-DD)'),
                ('next_review', 'Next review (YYYY-MM-DD)'))):
            row, col = divmod(index, 3)
            cell = ttk.Frame(fields)
            cell.grid(row=row, column=col, sticky='ew', padx=(0, 12), pady=(0, 8))
            ttk.Label(cell, text=label, style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
            ttk.Entry(cell, textvariable=self.metadata[key], width=20).pack(fill='x')
        ttk.Button(fields, text='Use today (UTC)', command=lambda: self.metadata['review_date'].set(today_utc())).grid(
            row=1, column=2, sticky='w', pady=(10, 0))
        ttk.Label(body, textvariable=self.summary, style='Section.TLabel').pack(anchor='w', pady=(0, 8))
        filter_row = ttk.Frame(body)
        filter_row.pack(fill='x', pady=(0, 8))
        ttk.Label(filter_row, text='Show').pack(side='left', padx=(0, 8))
        selector = ttk.Combobox(filter_row, textvariable=self.filter, state='readonly', width=22,
                                values=('All items', 'Open actions', 'Not reviewed'))
        selector.pack(side='left')
        selector.bind('<<ComboboxSelected>>', lambda _: self._populate())
        ttk.Label(filter_row, text='Select an item to add notes.', style='Muted.TLabel').pack(side='left', padx=12)
        review = ttk.Frame(body)
        review.pack(fill='both', expand=True)
        review.columnconfigure(0, weight=4, minsize=310)
        review.columnconfigure(1, weight=5, minsize=380)
        table_frame = ttk.Frame(review)
        table_frame.grid(row=0, column=0, sticky='nsew', padx=(0, 12))
        self.table = style_tree(ttk.Treeview(table_frame, columns=('title', 'status'), show='headings',
                                            height=12, selectmode='browse'))
        for column, title, width, minimum in (('title', 'Review item', 220, 170),
                                             ('status', 'Your review', 140, 120)):
            self.table.heading(column, text=title)
            self.table.column(column, width=width, minwidth=minimum, stretch=True)
        self.table.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(table_frame, orient='vertical', command=self.table.yview)
        bar.pack(side='right', fill='y')
        self.table.configure(yscrollcommand=bar.set)
        self.table.bind('<<TreeviewSelect>>', self._select)
        detail = ttk.Frame(review, padding=(12, 12), style='Card.TFrame')
        detail.grid(row=0, column=1, sticky='nsew')
        self.detail = detail
        ttk.Label(detail, textvariable=self.detail_group, style='Section.Panel.TLabel').pack(anchor='w', pady=(0, 5))
        ttk.Label(detail, textvariable=self.detail_title, style='Title.Panel.TLabel', wraplength=400).pack(anchor='w')
        ttk.Label(detail, textvariable=self.guidance, style='Panel.TLabel', wraplength=400, justify='left').pack(
            fill='x', pady=(8, 10))
        state_row = ttk.Frame(detail, style='Panel.TFrame')
        state_row.pack(fill='x')
        ttk.Label(state_row, text='Your review', style='Panel.TLabel').pack(side='left', padx=(0, 10))
        self.state_selector = ttk.Combobox(state_row, textvariable=self.status, values=STATES,
                                           state='readonly', width=20)
        self.state_selector.pack(side='left')
        ttk.Label(detail, text='Evidence, action owner and follow-up\n(no passwords)', style='Muted.Panel.TLabel').pack(
            anchor='w', pady=(10, 5))
        self.notes = style_text(tk.Text(detail, width=32, height=5, wrap='word', undo=True, font=('TkDefaultFont', 10)))
        self.notes.pack(fill='x')
        self.notes.bind('<<Modified>>', self._notes_changed)
        ttk.Label(detail, textvariable=self.sources, style='Muted.Panel.TLabel', wraplength=400).pack(
            anchor='w', pady=(8, 4))
        self.reference_button = ttk.Button(detail, text='Open UR reference', command=self.open_reference)
        self.reference_button.pack(anchor='w')
        ttk.Label(body, text=RECORD_NOTICE, style='Muted.TLabel', wraplength=850, justify='left').pack(
            fill='x', pady=(12, 0))
        body.bind('<Configure>', self._wrap)
        detail.bind('<Configure>', self._wrap_detail)

    def _wrap(self, event):
        width = max(250, event.width - 32)
        for widget in self.body.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=width)

    def _wrap_detail(self, event):
        for widget in self.detail.winfo_children():
            if isinstance(widget, ttk.Label):
                widget.configure(wraplength=max(200, event.width - 28))

    def _error_changed(self, *_):
        if self.error.get():
            self.error_label.pack(anchor='w', pady=(4, 0))
        else:
            self.error_label.pack_forget()

    def _sync(self):
        if self._loading:
            return
        self.record['metadata'] = {key: value.get() for key, value in self.metadata.items()}
        if self.selected:
            self.record['items'][self.selected] = {'status': self.status.get(), 'note': self.notes.get('1.0', 'end-1c')}

    @property
    def dirty(self):
        self._sync()
        return self.record != self.saved_record

    def _edited(self, *_):
        if self._loading:
            return
        self._sync()
        self.summary.set(review_summary(self.record))
        if self.selected and self.table.exists(self.selected):
            self.table.set(self.selected, 'status', self.status.get())
        name = str(self.path) if self.path else 'New checklist'
        state = 'Unsaved changes' if self.dirty else 'Saved' if self.path else 'Save a file to keep this review'
        self.file_status.set(name + ' · ' + state)

    def _notes_changed(self, _event=None):
        if self.notes.edit_modified():
            self.notes.edit_modified(False)
            self._edited()

    def _populate(self):
        self._sync()
        old_selection = self.selected
        self.table.delete(*self.table.get_children())
        for index, check in enumerate(DEPLOYMENT_CHECKS):
            status = self.record['items'][check.key]['status']
            if self.filter.get() == 'Open actions' and status != 'Action needed':
                continue
            if self.filter.get() == 'Not reviewed' and status != 'Not reviewed':
                continue
            self.table.insert('', 'end', iid=check.key, values=(check.title, status),
                              tags=('even' if index % 2 == 0 else 'odd',))
        rows = self.table.get_children()
        selected = old_selection if old_selection in rows else rows[0] if rows else None
        self.selected = None
        if selected:
            self.table.selection_set(selected)
            self.table.see(selected)
        self._load_selection(selected)

    def _select(self, _event=None):
        selection = self.table.selection()
        key = selection[0] if selection else None
        if key != self.selected:
            self._sync()
            self._load_selection(key)

    def _load_selection(self, key):
        self._loading = True
        self.selected = key
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        if key:
            check = self.checks[key]
            entry = self.record['items'][key]
            self.detail_title.set(check.title)
            self.detail_group.set(check.group)
            self.guidance.set(check.guidance)
            self.sources.set('Sources: ' + '; '.join(check.sources))
            self.status.set(entry['status'])
            self.notes.insert('1.0', entry['note'])
        else:
            self.detail_title.set('No items match this filter')
            self.detail_group.set('Review items')
            self.guidance.set('Choose All items to continue your review.')
            self.sources.set('')
            self.status.set(STATES[0])
        self.notes.edit_modified(False)
        self.notes.edit_reset()
        self.notes.configure(state='normal' if key else 'disabled')
        self.state_selector.configure(state='readonly' if key else 'disabled')
        self.reference_button.configure(state='normal' if key else 'disabled')
        self._loading = False

    def _replace(self, record, path=None):
        self._loading = True
        self.record = record
        self.saved_record = {**record, 'metadata': dict(record['metadata']),
                             'items': {key: dict(item) for key, item in record['items'].items()}}
        self.path = Path(path) if path else None
        self.selected = None
        for key, value in self.metadata.items():
            value.set(record['metadata'][key])
        self.filter.set('All items')
        self._loading = False
        self._populate()
        self._edited()
        self.error.set('')

    def confirm_discard(self):
        return not self.dirty or messagebox.askyesno('Unsaved checklist',
            'Discard unsaved checklist changes? Choose No to return and save your review.',
            parent=self, default='no')

    def new(self):
        if self.confirm_discard():
            self._replace(new_record())

    def open(self):
        path = filedialog.askopenfilename(parent=self, title='Open checklist',
            filetypes=(('UR review checklist', '*.json'), ('All files', '*.*')))
        if not path:
            return
        try:
            record = load_record(path)
        except (OSError, ValueError) as exc:
            self.error.set(f'Could not open checklist: {exc}')
            return
        if self.confirm_discard():
            self._replace(record, path)

    def save(self, force_dialog=False):
        self._sync()
        path = self.path
        if path is None or force_dialog:
            path = filedialog.asksaveasfilename(parent=self, title='Save checklist', defaultextension='.json',
                initialfile=path.name if path else 'robot-review.json',
                filetypes=(('UR review checklist', '*.json'),))
        if not path:
            return False
        try:
            save_record(path, self.record)
        except (OSError, ValueError) as exc:
            self.error.set(f'Could not save checklist: {exc}')
            return False
        self.path = Path(path)
        self.saved_record = {**self.record, 'metadata': dict(self.record['metadata']),
                             'items': {key: dict(item) for key, item in self.record['items'].items()}}
        self.error.set('')
        self._edited()
        return True

    def export(self):
        self._sync()
        path = filedialog.asksaveasfilename(parent=self, title='Export review checklist', defaultextension='.csv',
            initialfile='robot-review.csv', filetypes=(('CSV spreadsheet', '*.csv'),))
        if not path:
            return
        try:
            export_record_csv(path, self.record)
        except (OSError, ValueError) as exc:
            self.error.set(f'Could not export checklist: {exc}')
            return
        self.error.set('')
        self.file_status.set(f'Exported {path}' + (' · Checklist has unsaved changes' if self.dirty else ''))

    def open_reference(self):
        if self.selected:
            webbrowser.open(self.checks[self.selected].url)

    def shutdown(self):
        """No background jobs or robot connections are started by this page."""
