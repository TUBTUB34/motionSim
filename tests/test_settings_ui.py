"""Settings page: projects, text size and saved robots, with isolated preferences and folders."""
import gc
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class SettingsUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'preferences.json'
        self.errors = []
        self.discovery = patch('motionstudio.flight.view.FlightWindow._discover')
        self.discovery.start()
        self.start_app()

    def start_app(self):
        import tkinter as tk
        from motionstudio.app.main_window import SimulatorApp
        self.root = tk.Tk()
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = SimulatorApp(self.root, preferences_path=self.path)
        self.root.after_cancel(self.app.poll_id)
        self.settle()

    def stop_app(self):
        from motionstudio.ui import theme
        theme.set_text_scale(self.root, 100)  # the scale is process-wide; later tests expect the default
        with patch('motionstudio.commissioning.view.messagebox.askyesno', return_value=True):
            self.app.close()
        del self.app, self.root
        gc.collect()

    def tearDown(self):
        self.stop_app()
        self.discovery.stop()
        self.temp.cleanup()
        self.assertEqual(self.errors, [])

    def settle(self):
        self.root.update_idletasks()
        self.root.update()
        self.root.update_idletasks()

    def open_settings(self):
        self.app.open_settings()
        self.settle()
        return self.app.settings_page

    def test_pages_start_in_the_project_and_opening_pages_creates_no_folders(self):
        workspace = self.folder / 'Projects'
        self.assertEqual(self.app.workspace, workspace)
        self.assertEqual(self.app.project_root, workspace / 'General')
        for action in (self.app.open_flights, self.app.open_flight_retrieval, self.app.open_logs, self.app.open_backups,
                       self.app.open_reports, self.app.open_checklist, self.app.open_live, self.open_settings):
            action()
            self.settle()
        self.assertFalse(workspace.exists())
        self.assertEqual(Path(self.app.flight_window.library.get()), (workspace / 'General' / 'Flight reports').resolve())
        self.assertEqual(self.app.backup_page.directory.get(), str(workspace / 'General' / 'Backups'))
        self.assertEqual(self.app.shell.project_label.cget('text'), 'Project · General')
        from motionstudio import __version__
        self.assertEqual(self.app.settings_page.version_label.cget('text'), f'UR Motion Studio {__version__}')

    def test_creating_and_switching_projects_moves_every_page_and_survives_restart(self):
        self.app.open_flights()
        self.app.open_flight_retrieval()
        self.app.open_backups()
        self.app.open_logs()
        self.app.log_window.save_directory = Path('/somewhere/else')
        page = self.open_settings()
        page.new_project.set('Cell 4')
        page.create_button.invoke()
        self.settle()
        project = self.folder / 'Projects' / 'Cell 4'
        self.assertTrue(project.is_dir())
        self.assertEqual(self.app.project_root, project)
        self.assertEqual(Path(self.app.flight_window.library.get()), (project / 'Flight reports').resolve())
        self.assertEqual(Path(self.app.flight_retrieval_window.library.get()), (project / 'Flight reports').resolve())
        self.assertEqual(self.app.backup_page.directory.get(), str(project / 'Backups'))
        self.assertIsNone(self.app.log_window.save_directory)
        self.assertEqual(self.app.shell.project_label.cget('text'), 'Project · Cell 4')
        self.assertEqual(page.project_box.cget('values'), ('Cell 4', 'General'))
        self.assertEqual(page.new_project.get(), '')
        with patch('motionstudio.app.main_window.messagebox.showerror') as error:
            page.new_project.set('cell 4')
            page.create_button.invoke()
            self.assertIn('already exists', error.call_args.args[1])
        # A backup destination chosen by hand is kept across a switch.
        self.app.backup_page.directory.set(str(self.folder))
        page.project.set('General')
        page.project_box.event_generate('<<ComboboxSelected>>')
        self.settle()
        self.assertEqual(self.app.project, 'General')
        self.assertEqual(self.app.backup_page.directory.get(), str(self.folder))
        saved = json.loads(self.path.read_text())
        self.assertEqual((saved['workspace'], saved['project']), (str(self.folder / 'Projects'), 'General'))
        self.stop_app()
        self.start_app()
        self.assertEqual(self.app.project_root, self.folder / 'Projects' / 'General')

    def test_switching_is_refused_while_work_is_still_saving_into_the_project(self):
        self.app.open_backups()
        self.app.backup_page.job = SimpleNamespace()
        with patch('motionstudio.app.main_window.messagebox.showwarning') as warning:
            self.assertFalse(self.app.set_project(self.folder / 'Projects', 'Other', create=True))
        self.assertIn('Backups', warning.call_args.args[1])
        self.assertEqual(self.app.project, 'General')
        self.assertFalse((self.folder / 'Projects' / 'Other').exists())
        self.app.backup_page.job = None
        page = self.open_settings()
        page.moving = True  # a running move of earlier files also holds the project
        with patch('motionstudio.app.main_window.messagebox.showwarning') as warning:
            self.assertFalse(self.app.set_project(self.folder / 'Projects', 'Other', create=True))
        self.assertIn('Moving earlier files', warning.call_args.args[1])
        page.moving = False

    def test_changing_the_working_folder_keeps_a_matching_project(self):
        other = self.folder / 'Jobs'
        (other / 'general' / 'Backups').mkdir(parents=True)
        page = self.open_settings()
        with patch('motionstudio.app.settings_view.filedialog.askdirectory', return_value=str(other)):
            page.workspace_button.invoke()
        self.settle()
        self.assertEqual((self.app.workspace, self.app.project), (other, 'general'))
        self.assertEqual(page.workspace.get(), str(other))
        # Picking a project folder itself selects it inside its parent, not <project>/General.
        with patch('motionstudio.app.settings_view.filedialog.askdirectory', return_value=str(other / 'general')):
            page.workspace_button.invoke()
        self.assertEqual((self.app.workspace, self.app.project), (other, 'general'))

    def test_chart_toolbar_save_starts_in_the_project(self):
        import matplotlib
        self.app.open_logs()
        (self.folder / 'Projects' / 'General' / 'Reports').mkdir(parents=True)
        self.app.set_project(self.folder / 'Projects', 'General')
        self.assertEqual(matplotlib.rcParams['savefig.directory'],
                         str(self.folder / 'Projects' / 'General' / 'Reports'))

    def test_text_size_resizes_open_pages_and_follows_theme_switches_and_restart(self):
        from tkinter import font
        from motionstudio.ui import theme
        self.app.open_logs()
        page = self.open_settings()
        body = font.nametofont('TkDefaultFont', root=self.root)
        title = font.Font(root=self.root, name=theme.font(17, 'bold'), exists=True)
        self.assertEqual((body.cget('size'), title.cget('size')), (10, 17))
        page.size_buttons[130].invoke()
        self.settle()
        self.assertEqual((body.cget('size'), title.cget('size')), (13, 22))
        self.assertGreater(int(self.root.tk.call('ttk::style', 'configure', 'Treeview', '-rowheight')), 30)
        self.assertTrue(self.app.set_theme('light'))  # the other palette is built for the first time here
        self.settle()
        self.assertEqual(body.cget('size'), 13)
        self.assertGreater(int(self.root.tk.call('ttk::style', 'configure', 'Treeview', '-rowheight')), 30)
        self.stop_app()
        self.start_app()
        self.assertEqual(font.nametofont('TkDefaultFont', root=self.root).cget('size'), 13)
        self.assertEqual(self.open_settings().text_scale.get(), 130)

    def test_earlier_data_is_offered_and_moved_into_the_project(self):
        old = self.folder / 'flightreports' / 'robot-a'
        old.mkdir(parents=True)
        (old / 'manifest.json').write_text('{"files": {}}')
        page = self.open_settings()
        self.assertTrue(page.legacy.winfo_ismapped())
        self.assertEqual(page.move_button.cget('text'), 'Move into General')
        page.move_button.invoke()
        deadline = time.monotonic() + 5
        while page.moving and time.monotonic() < deadline:
            self.settle()
        self.assertTrue((self.folder / 'Projects' / 'General' / 'Flight reports' / 'robot-a' / 'manifest.json').is_file())
        self.assertFalse((self.folder / 'flightreports').exists())
        self.assertFalse(page.legacy.winfo_ismapped())
        self.assertIn('Moved 1 item into', page.project_status.get())

    def test_saved_robots_can_be_removed_from_settings(self):
        self.app.preferences.add_address('192.0.2.10')
        self.app.preferences.add_address('192.0.2.11')
        page = self.open_settings()
        self.assertEqual(page.robots.get_children(), ('192.0.2.10', '192.0.2.11'))
        self.assertEqual(str(page.remove_button.cget('state')), 'disabled')
        page.robots.selection_set('192.0.2.10')
        self.settle()
        page.remove_button.invoke()
        self.assertEqual(self.app.preferences.addresses, ('192.0.2.11',))
        self.assertEqual(page.robots.get_children(), ('192.0.2.11',))


if __name__ == '__main__':
    unittest.main()
