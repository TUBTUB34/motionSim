"""Settings: the working folder and project everything is saved in, appearance and saved robots."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog

from motionstudio.core.paths import (DEFAULT_PROJECT, PROJECT_FOLDERS, is_project, legacy_data, list_projects,
                                     move_legacy_data)
from motionstudio import __version__
from motionstudio.app.preferences import TEXT_SCALES
from motionstudio.ui import dialogs as messagebox
from motionstudio.ui.address_book import refresh_address_fields
from motionstudio.ui.theme import ScrollableFrame, flow, style_tree
from motionstudio.core.about import APP_NAME

TEXT_SIZE_NAMES = dict(zip(TEXT_SCALES, ('Small', 'Standard', 'Large', 'Larger', 'Largest')))
FOLDER_PURPOSE = {
    'flights': 'Downloaded flight reports and live recordings',
    'programs': 'Programs pulled from each robot, by IP',
    'status': 'Robot Status evidence collected from each robot, by IP',
    'backups': 'Robot backup archives',
    'logs': 'Saved robot logs',
    'reports': 'Exported PDF reports and CSV tables',
    'checklists': 'Commissioning checklists',
}


def open_folder(path):
    """Show a folder in the system file manager; returns an error message or ''."""
    path = Path(path)
    if not path.is_dir():
        return f'{path} does not exist yet. It is created the first time something is saved there.'
    try:
        if sys.platform == 'win32':
            os.startfile(path)  # noqa: only exists on Windows
        else:
            subprocess.Popen(['open' if sys.platform == 'darwin' else 'xdg-open', str(path)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as error:
        return f'Could not open {path}: {error}'
    return ''


class SettingsPage(ttk.Frame):
    def __init__(self, app, parent):
        super().__init__(parent)
        self.app = app
        self.moving = False
        header = ttk.Frame(self, padding=(24, 18), style='Toolbar.TFrame')
        header.pack(fill='x')
        ttk.Label(header, text='Settings', style='Title.Panel.TLabel').pack(side='left')
        self.scroll = ScrollableFrame(self, padding=24)
        self.scroll.pack(fill='both', expand=True)
        body = self.scroll.content
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        left = ttk.Frame(body)
        left.grid(row=0, column=0, sticky='nsew', padx=(0, 28))
        left.columnconfigure(1, weight=1)
        self._wrap = []
        self.workspace = tk.StringVar()
        self.project = tk.StringVar()
        self.new_project = tk.StringVar()
        self.project_status = tk.StringVar()
        self.legacy_text = tk.StringVar()
        self.text_scale = tk.IntVar(value=app.preferences.text_scale)

        row = self._section(left, 0, 'Projects', 'Each project keeps its own flight reports, robot downloads, backups, '
                            'logs, reports and checklists. Save and open dialogs start in the active project.')
        ttk.Label(left, text='Working folder').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=4)
        ttk.Entry(left, textvariable=self.workspace, state='readonly').grid(row=row, column=1, sticky='ew', pady=4)
        buttons = ttk.Frame(left)
        buttons.grid(row=row, column=2, sticky='w', padx=(8, 0))
        self.workspace_button = ttk.Button(buttons, text='Change…', command=self.choose_workspace)
        self.workspace_button.pack(side='left')
        ttk.Button(buttons, text='Open', command=lambda: self._open(self.app.workspace)).pack(side='left', padx=(6, 0))
        row += 1
        ttk.Label(left, text='Project').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=4)
        self.project_box = ttk.Combobox(left, textvariable=self.project, state='readonly')
        self.project_box.grid(row=row, column=1, sticky='ew', pady=4)
        self.project_box.bind('<<ComboboxSelected>>', self.select_project)
        ttk.Button(left, text='Open', command=lambda: self._open(self.app.project_root)).grid(
            row=row, column=2, sticky='w', padx=(8, 0))
        row += 1
        ttk.Label(left, text='New project').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=4)
        entry = ttk.Entry(left, textvariable=self.new_project)
        entry.grid(row=row, column=1, sticky='ew', pady=4)
        entry.bind('<Return>', lambda _event: (self.create_project(), 'break')[1])
        self.create_button = ttk.Button(left, text='Create', command=self.create_project)
        self.create_button.grid(row=row, column=2, sticky='w', padx=(8, 0))
        row += 1
        self.status_label = ttk.Label(left, textvariable=self.project_status, style='Muted.TLabel')
        self.status_label.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self._wrap.append(self.status_label)
        row += 1
        self.legacy = ttk.Frame(left, padding=(0, 14, 0, 0))
        self.legacy.grid(row=row, column=0, columnspan=3, sticky='ew')
        self.legacy.columnconfigure(0, weight=1)
        legacy_label = ttk.Label(self.legacy, textvariable=self.legacy_text, style='Warning.TLabel')
        legacy_label.grid(row=0, column=0, columnspan=2, sticky='ew')
        self._wrap.append(legacy_label)
        legacy_buttons = ttk.Frame(self.legacy)
        legacy_buttons.grid(row=1, column=0, sticky='w', pady=(8, 0))
        self.move_button = ttk.Button(legacy_buttons, command=self.move_legacy)
        self.move_button.pack(side='left')
        ttk.Button(legacy_buttons, text='Open old folder', command=lambda: self._open(self.app.legacy_base)).pack(
            side='left', padx=(6, 0))
        row += 1

        row = self._section(left, row, 'Appearance', 'Changes apply at once to every page. Charts and PDF reports '
                            'keep their own sizes.', separator=True)
        ttk.Label(left, text='Theme').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=4)
        # Option rows wrap at large text sizes instead of running past the window edge.
        modes = ttk.Frame(left)
        modes.grid(row=row, column=1, columnspan=2, sticky='ew', pady=4)
        self.theme_buttons = {}
        for text, dark in (('Dark', True), ('Light', False)):
            button = ttk.Radiobutton(modes, text=text, value=dark, variable=app.dark_mode, command=app.toggle_theme)
            self.theme_buttons[text] = button
        row += 1
        ttk.Label(left, text='Text size').grid(row=row, column=0, sticky='w', padx=(0, 16), pady=4)
        sizes = ttk.Frame(left)
        sizes.grid(row=row, column=1, columnspan=2, sticky='ew', pady=4)
        self.size_buttons = {}
        for scale in TEXT_SCALES:
            button = ttk.Radiobutton(sizes, text=f'{TEXT_SIZE_NAMES[scale]} · {scale} %', value=scale,
                                     variable=self.text_scale, command=self.change_text_size)
            self.size_buttons[scale] = button
        self._arrange = (flow(modes, gap=18), flow(sizes, gap=18))
        row += 1

        row = self._section(left, row, 'Saved robots', 'Addresses kept with Save IP and offered in every robot IP list. '
                            'Usernames and passwords are never saved.', separator=True)
        self.robots = style_tree(ttk.Treeview(left, show='tree', selectmode='browse', height=5))
        self.robots.grid(row=row, column=0, columnspan=2, sticky='ew', pady=4)
        self.robots.bind('<<TreeviewSelect>>', lambda _event: self._update_robot_buttons())
        self.remove_button = ttk.Button(left, text='Remove', command=self.remove_robot)
        self.remove_button.grid(row=row, column=2, sticky='nw', padx=(8, 0), pady=4)
        row += 1
        self.robots_empty = ttk.Label(left, text='No saved robots yet. Type an IP on the connection page and '
                                      'choose Save IP.', style='Muted.TLabel')
        self.robots_empty.grid(row=row, column=0, columnspan=3, sticky='ew')
        self._wrap.append(self.robots_empty)
        row += 1
        settings_file = ttk.Label(left, text=f'Settings file: {app.preferences.path}', style='Muted.TLabel')
        settings_file.grid(row=row, column=0, columnspan=3, sticky='ew', pady=(28, 0))
        self._wrap.append(settings_file)
        left.bind('<Configure>', lambda e: [label.configure(wraplength=max(240, e.width))
                                            for label in self._wrap] if e.widget is left else None)

        side = ttk.Frame(body, padding=18, style='Panel.TFrame')
        side.grid(row=0, column=1, sticky='new')
        side_title = ttk.Label(side, text='Saved in this project', style='Title.Panel.TLabel')
        side_title.pack(fill='x', pady=(0, 4))
        side_labels = [side_title]
        for kind, name in PROJECT_FOLDERS.items():
            ttk.Label(side, text=name, style='Panel.TLabel').pack(anchor='w', pady=(12, 2))
            label = ttk.Label(side, text=FOLDER_PURPOSE[kind], style='Muted.Panel.TLabel')
            label.pack(fill='x')
            side_labels.append(label)
        ttk.Separator(side).pack(fill='x', pady=18)
        ttk.Button(side, text='Open project folder', command=lambda: self._open(self.app.project_root)).pack(anchor='w')
        self.version_label = ttk.Label(side, text=f'{APP_NAME} {__version__}', style='Muted.Panel.TLabel')
        self.version_label.pack(anchor='w', pady=(18, 0))
        side.bind('<Configure>', lambda e: [label.configure(wraplength=max(160, e.width-36))
                                            for label in side_labels] if e.widget is side else None)
        self.refresh()

    def _section(self, parent, row, title, description, separator=False):
        if separator:
            ttk.Separator(parent).grid(row=row, column=0, columnspan=3, sticky='ew', pady=(28, 24))
            row += 1
        ttk.Label(parent, text=title, style='Title.TLabel').grid(row=row, column=0, columnspan=3, sticky='w')
        label = ttk.Label(parent, text=description, style='Muted.TLabel')
        label.grid(row=row+1, column=0, columnspan=3, sticky='ew', pady=(8, 14))
        self._wrap.append(label)
        return row + 2

    def refresh(self):
        app = self.app
        self.workspace.set(str(app.workspace))
        # The active and the default project are always offered, even before anything is saved in them.
        projects = list_projects(app.workspace)
        for name in (app.project, DEFAULT_PROJECT):
            if not any(item.casefold() == name.casefold() for item in projects):
                projects = sorted([*projects, name], key=str.casefold)
        self.project_box.configure(values=projects)
        self.project.set(app.project)
        if not app.workspace.is_dir():
            available = Path(app.workspace.anchor or '.').exists()
            self.project_status.set('This working folder does not exist yet. It is created the first time you save '
                                    'something.' if available else 'This working folder is not available. Reconnect '
                                    'its drive or choose another working folder; nothing is saved until then.')
            self.status_label.configure(style='Muted.TLabel' if available else 'Warning.TLabel')
        elif not self.moving:
            self.project_status.set(f'Saving into {app.project_root}')
            self.status_label.configure(style='Muted.TLabel')
        found = legacy_data(app.legacy_base)
        if found or self.moving:
            names = ', '.join(folder.name for folder, _kind in found)
            self.legacy_text.set(f'Files saved by an earlier version are still in {app.legacy_base} ({names}). '
                                 f'Move them into {app.project} to see them here. Nothing is overwritten.')
            self.move_button.configure(text=f'Move into {app.project}', state='disabled' if self.moving else 'normal')
            self.legacy.grid()
        else:
            self.legacy.grid_remove()
        self.text_scale.set(app.preferences.text_scale)
        self.robots.delete(*self.robots.get_children())
        for index, address in enumerate(app.preferences.addresses):
            self.robots.insert('', 'end', iid=address, text=address, tags=('odd' if index % 2 else 'even',))
        (self.robots_empty.grid_remove if app.preferences.addresses else self.robots_empty.grid)()
        self._update_robot_buttons()

    def _update_robot_buttons(self):
        self.remove_button.configure(state='normal' if self.robots.selection() else 'disabled')

    def _open(self, path):
        message = open_folder(path)
        if message:
            self.project_status.set(message)

    def choose_workspace(self):
        path = filedialog.askdirectory(parent=self, title='Choose the working folder for your projects',
                                       initialdir=str(self.app.workspace) if self.app.workspace.is_dir() else None)
        if not path:
            return
        workspace = Path(path)
        if is_project(workspace) and not list_projects(workspace):
            # A project folder was picked: use the folder that holds it, with that project active.
            self.app.set_project(workspace.parent, workspace.name)
            self.refresh()
            return
        projects = list_projects(workspace)
        # Keep the same project when the new folder has it; otherwise open the folder's first project.
        project = next((name for name in projects if name.casefold() == self.app.project.casefold()),
                       projects[0] if projects else DEFAULT_PROJECT)
        self.app.set_project(workspace, project)
        self.refresh()

    def select_project(self, _event=None):
        name = self.project.get()
        if name != self.app.project:
            self.app.set_project(self.app.workspace, name)
        self.refresh()

    def create_project(self):
        name = self.new_project.get().strip()
        if not name:
            self.project_status.set('Type a name for the new project, for example the customer or cell name.')
            self.status_label.configure(style='Warning.TLabel')
            return
        if self.app.set_project(self.app.workspace, name, create=True):
            self.new_project.set('')
        self.refresh()

    def change_text_size(self):
        if not self.app.set_text_scale(self.text_scale.get()):
            self.text_scale.set(self.app.preferences.text_scale)
        for arrange in self._arrange:
            arrange()  # buttons changed size with the text

    def remove_robot(self):
        selection = self.robots.selection()
        if not selection:
            return
        try:
            self.app.preferences.remove_address(selection[0])
        except (ValueError, OSError) as error:
            messagebox.showerror('Could not remove saved robot', str(error), parent=self)
            return
        refresh_address_fields(self.app.root)
        self.app.update_address_controls()
        self.refresh()

    def move_legacy(self):
        busy = self.app.busy_pages()
        if busy:
            messagebox.showwarning('Files not moved', 'Finish or cancel the work in progress first: '
                                   + ', '.join(busy) + '.', parent=self)
            return
        target, base = self.app.project_root, self.app.legacy_base
        self.moving = True
        self.project_status.set(f'Moving earlier files into {self.app.project}…')
        self.refresh()
        result = {}

        def work():
            try:
                result['value'] = move_legacy_data(target, base)
            except Exception as error:  # reported on the page; never left at "Moving…"
                result['error'] = error

        worker = threading.Thread(target=work, name='move-earlier-data', daemon=True)
        worker.start()
        self.after(100, self._wait_for_move, worker, result, target)

    def _wait_for_move(self, worker, result, target):
        if worker.is_alive():
            self.after(100, self._wait_for_move, worker, result, target)
            return
        self.moving = False
        flights = self.app.flight_window
        if flights is not None and flights.winfo_exists():
            flights.refresh_library()
        self.refresh()
        if 'error' in result:
            self.project_status.set(f'Moving stopped: {result["error"]}. Files already moved are in the project.')
            self.status_label.configure(style='Warning.TLabel')
            return
        moved, skipped, failed = result['value']
        text = f'Moved {len(moved)} item{"s" * (len(moved) != 1)} into {target}.'
        if skipped:
            text += (f' {len(skipped)} left in place because the project already has an item with the same name: '
                     + ', '.join(skipped[:5]) + ('…' if len(skipped) > 5 else ''))
        if failed:
            text += f' {len(failed)} could not be moved and were left in place: ' + '; '.join(failed[:3])
        self.project_status.set(text)
        self.status_label.configure(style='Warning.TLabel' if skipped or failed else 'Muted.TLabel')
