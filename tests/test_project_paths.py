"""Working folder, projects and moving earlier-version data."""
from pathlib import Path
from types import SimpleNamespace
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from motionstudio.core.paths import (DEFAULT_PROJECT, PROJECT_MARKER, create_project, default_workspace, legacy_data,
                                     list_projects, move_legacy_data, project_folder, start_folder,
                                     validate_project_name)


class ProjectPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_project_names_must_be_portable_folder_names(self):
        self.assertEqual(validate_project_name('  Cell 4 – Line B  '), 'Cell 4 – Line B')
        for name in ('', '   ', 'a/b', 'a\\b', 'x:y', 'what?', 'CON', 'nul.txt', 'COM1', 'trailing.',
                     '..', 'x' * 81, None, 'tab\there'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_project_name(name)

    def test_only_project_folders_are_listed(self):
        (self.root / 'Holiday photos').mkdir()
        (self.root / 'Old job' / 'Backups').mkdir(parents=True)
        create_project(self.root, 'beta')
        create_project(self.root, 'Alpha')
        (self.root / 'notes.txt').write_text('x')
        self.assertEqual(list_projects(self.root), ['Alpha', 'beta', 'Old job'])
        self.assertTrue((self.root / 'Alpha' / PROJECT_MARKER).is_file())
        self.assertEqual(list_projects(self.root / 'missing'), [])

    def test_names_differing_only_in_case_are_the_same_project(self):
        create_project(self.root, 'Cell 4')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            create_project(self.root, 'cell 4')
        (self.root / 'Plain').mkdir()
        self.assertEqual(create_project(self.root, 'Plain'), self.root / 'Plain')  # adopts an existing plain folder

    def test_project_folders_follow_the_app_and_default_without_creating_anything(self):
        app = SimpleNamespace(project_root=self.root / 'Cell 4')
        self.assertEqual(project_folder(app), self.root / 'Cell 4')
        self.assertEqual(project_folder(app, 'flights'), self.root / 'Cell 4' / 'Flight reports')
        self.assertEqual(project_folder(app, 'status'), self.root / 'Cell 4' / 'Robot Status')
        with patch.dict('os.environ', {'MOTIONSIM_WORKSPACE': str(self.root / 'ws')}):
            self.assertEqual(default_workspace(), self.root / 'ws')
            self.assertEqual(project_folder(SimpleNamespace(), 'backups'), self.root / 'ws' / DEFAULT_PROJECT / 'Backups')
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(start_folder(app, 'logs', create=False), None)  # open dialogs create nothing
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(start_folder(app, 'reports'), str(self.root / 'Cell 4' / 'Reports'))
        self.assertTrue((self.root / 'Cell 4' / 'Reports').is_dir())
        self.assertEqual(start_folder(app, 'logs', create=False), str(self.root / 'Cell 4'))

    def test_a_missing_working_folder_is_never_recreated_by_a_dialog(self):
        app = SimpleNamespace(project_root=self.root / 'unplugged-drive' / 'Cell 4')
        with patch.dict('os.environ', {'MOTIONSIM_WORKSPACE': str(self.root / 'ws')}):
            self.assertIsNone(start_folder(app, 'reports'))
        self.assertFalse((self.root / 'unplugged-drive').exists())

    def test_start_folder_failure_lets_the_dialog_open_normally(self):
        app = SimpleNamespace(project_root=self.root / 'Cell 4')
        with patch('motionstudio.core.paths.Path.mkdir', side_effect=PermissionError('read-only')):
            self.assertIsNone(start_folder(app, 'logs'))

    def test_earlier_data_moves_into_the_project_without_overwriting(self):
        old = self.root / 'appdata'
        (old / 'flightreports' / 'robot-a').mkdir(parents=True)
        (old / 'flightreports' / 'robot-a' / 'manifest.json').write_text('{}')
        (old / 'flightreports' / 'robot-b').mkdir()
        (old / 'Robot Status' / '192.0.2.8').mkdir(parents=True)
        (old / 'Robot programs').mkdir()  # empty: nothing to offer
        project = self.root / 'ws' / 'General'
        (project / 'Flight reports' / 'robot-b').mkdir(parents=True)  # already present in the project
        self.assertEqual({folder.name for folder, _kind in legacy_data(old)}, {'flightreports', 'Robot Status'})
        moved, skipped, failed = move_legacy_data(project, old)
        self.assertEqual(moved, ['Flight reports/robot-a', 'Robot Status/192.0.2.8'])
        self.assertEqual((skipped, failed), (['Flight reports/robot-b'], []))
        self.assertTrue((project / 'Flight reports' / 'robot-a' / 'manifest.json').is_file())
        self.assertTrue((old / 'flightreports' / 'robot-b').is_dir())  # left in place, not overwritten
        self.assertFalse((old / 'Robot Status').exists())  # emptied folder removed
        self.assertEqual([folder.name for folder, _kind in legacy_data(old)], ['flightreports'])


    def test_a_move_across_drives_copies_under_a_temporary_name_and_reports_failures(self):
        old = self.root / 'appdata'
        (old / 'flightreports' / 'robot-a').mkdir(parents=True)
        (old / 'flightreports' / 'robot-a' / 'report.zip').write_bytes(b'zip')
        (old / 'flightreports' / 'robot-b').mkdir()
        project = self.root / 'ws' / 'General'
        real_rename, copies = os.rename, []

        def rename(source, destination):
            if '.partial-' not in str(source):
                raise OSError(18, 'Invalid cross-device link')  # what a different drive reports
            copies.append(Path(source).name)
            return real_rename(source, destination)

        def copytree(source, destination):
            if Path(source).name == 'robot-b':
                Path(destination).mkdir()
                raise OSError(28, 'No space left on device')
            return real_copytree(source, destination)
        real_copytree = shutil.copytree
        with patch('motionstudio.core.paths.os.rename', side_effect=rename), \
                patch('motionstudio.core.paths.shutil.copytree', side_effect=copytree):
            moved, skipped, failed = move_legacy_data(project, old)
        self.assertEqual((moved, skipped), (['Flight reports/robot-a'], []))
        self.assertEqual(len(failed), 1)
        self.assertIn('robot-b', failed[0])
        self.assertTrue(copies[0].startswith('.robot-a.partial-'))
        self.assertEqual((project / 'Flight reports' / 'robot-a' / 'report.zip').read_bytes(), b'zip')
        self.assertFalse((old / 'flightreports' / 'robot-a').exists())
        self.assertTrue((old / 'flightreports' / 'robot-b').is_dir())  # kept for another try
        self.assertEqual(sorted(p.name for p in (project / 'Flight reports').iterdir()), ['robot-a'])  # no partial left


if __name__ == '__main__':
    unittest.main()
