"""Task entry points and an honest summary of the current desktop session."""
from pathlib import Path
import time
import tkinter as tk
from tkinter import ttk

from ui_theme import ScrollableFrame


def workspace_summary(app):
    """Describe only loaded evidence; a connected robot is not an assessment."""
    summaries = {}
    for name, attribute, result_attribute in (
        ('Logs', 'log_window', 'data'),
        ('Flight recording', 'flight_window', 'report'),
        ('Flight downloads', 'flight_retrieval_window', None),
        ('Program review', 'program_check_window', 'review'),
        ('Robot Status', 'diagnostic_window', 'report'),
        ('Backup', 'backup_page', None),
    ):
        panel = getattr(app, attribute, None)
        if panel is None:
            summaries[name] = 'Not opened'
            continue
        if (getattr(panel, 'busy', False) is True or getattr(panel, 'loading', False)
                or getattr(panel, 'exporting', False) or getattr(panel, 'job', None) is not None):
            summaries[name] = panel.status.get()
            continue
        result = getattr(panel, result_attribute, None) if result_attribute else None
        if result is None:
            summaries[name] = panel.status.get() if result_attribute is None else 'No evidence loaded'
        elif name == 'Logs':
            summaries[name] = f'{len(result.events):,} monitored incidents · {result.source}'
        elif name == 'Flight recording':
            summaries[name] = Path(result.path).name
        elif name == 'Program review':
            summaries[name] = result.pair.name
        else:
            summaries[name] = 'Evidence loaded · Open to review coverage'
    return summaries


class HomePage(ttk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self._next_refresh = 0
        header = ttk.Frame(self, padding=(24, 18), style='Toolbar.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='Your robot workspace', style='Title.Panel.TLabel').pack(side='left')
        ttk.Button(header, text='Getting started', command=app.open_help).pack(side='right')
        self.scroll = ScrollableFrame(self, padding=24)
        self.scroll.pack(fill='both', expand=True)
        body = self.scroll.content
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        intro = ttk.Label(body, text='Inspect recorded behavior, review programs and prepare a documented handover.',
                          style='Muted.TLabel', wraplength=850)
        intro.grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 24))
        tasks = ttk.Frame(body)
        tasks.grid(row=1, column=0, sticky='nsew', padx=(0, 28))
        self.task_buttons = {}
        sections = (
            ('Inspect the robot', 'Follow live telemetry or investigate saved events and motion.', (
                ('Live simulator', app.edit_connection_page),
                ('Logs & incidents', app.open_logs),
                ('Flight analysis', app.open_flights))),
            ('Review and report', 'Use program sources for static checks; use recorded evidence for Robot Status.', (
                ('Program Checker', app.open_program_check),
                ('Robot Status report', app.open_diagnostics))),
            ('Commission and maintain', 'Record inspection evidence and preserve robot files for your next visit.', (
                ('Commissioning checklist', app.open_checklist),
                ('Robot backup', app.open_backups))),
        )
        self._wrap_labels = []
        for index, (title, description, actions) in enumerate(sections):
            group = ttk.Frame(tasks)
            group.pack(fill='x', pady=(0, 24))
            ttk.Label(group, text=title, style='Title.TLabel').pack(anchor='w')
            label = ttk.Label(group, text=description, style='Muted.TLabel', wraplength=480)
            label.pack(fill='x', pady=(8, 12))
            self._wrap_labels.append(label)
            # Vertical task actions remain legible on compact desktops and at large font sizes.
            for text, action in actions:
                button = ttk.Button(group, text=text, command=action)
                button.pack(anchor='w', pady=(0, 6))
                self.task_buttons[text] = button
            if index < len(sections) - 1:
                ttk.Separator(group).pack(fill='x', pady=(14, 0))
        tasks.bind('<Configure>', self._resize)
        session = ttk.Frame(body, padding=18, style='Panel.TFrame')
        session.grid(row=1, column=1, sticky='new')
        ttk.Label(session, text='This session', style='Title.Panel.TLabel').pack(anchor='w')
        self.connection = tk.StringVar()
        connection = ttk.Label(session, textvariable=self.connection, style='Muted.Panel.TLabel', wraplength=300)
        connection.pack(fill='x', pady=(10, 16))
        labels = [connection]
        self.summaries = {}
        for name in workspace_summary(app):
            ttk.Label(session, text=name, style='Panel.TLabel').pack(anchor='w', pady=(12, 4))
            variable = self.summaries[name] = tk.StringVar()
            label = ttk.Label(session, textvariable=variable, style='Muted.Panel.TLabel', wraplength=300)
            label.pack(fill='x')
            labels.append(label)
        ttk.Separator(session).pack(fill='x', pady=18)
        note = ttk.Label(session, text='Switch pages freely. Loaded evidence and active transfers stay available until you close the app. Save reports and checklists to keep them.',
                         style='Muted.Panel.TLabel', wraplength=300)
        note.pack(fill='x')
        labels.append(note)
        session.bind('<Configure>', lambda e: [label.configure(wraplength=max(160, e.width-36))
                                               for label in labels] if e.widget is session else None)
        self.refresh()

    def _resize(self, event):
        for label in self._wrap_labels:
            label.configure(wraplength=max(200, event.width))

    def refresh(self, force=False):
        now = time.monotonic()
        if not force and now < self._next_refresh:
            return
        self._next_refresh = now + .5
        self.app.shell.update_connection()
        value = self.app.shell.connection.get().replace('\n', ' · ')
        if self.connection.get() != value:
            self.connection.set(value)
        for name, value in workspace_summary(self.app).items():
            if self.summaries[name].get() != value:
                self.summaries[name].set(value)
