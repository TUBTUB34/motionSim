"""Dedicated backup page lifecycle, using mocked transfer workers."""
import gc
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class BackupPageTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from motionstudio.app.main_window import SimulatorApp
        self.root = tk.Tk()
        self.app = SimulatorApp(self.root)
        self.app.open_backups()
        self.page = self.app.backup_page
        self.page.ip.set('192.0.2.1')
        self.page.password.set('test-only')
        self.page.project_name.set('Fixture project')
        self.temp = tempfile.TemporaryDirectory()
        self.page.directory.set(self.temp.name)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.root.update()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()
        del self.page, self.app, self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def poll(self):
        self.page.after_cancel(self.page.after_id)
        self.page.poll()

    def test_navigation_preserves_backup_job_and_destination(self):
        with patch('motionstudio.backup.view.BackupJob.start') as start:
            self.page.make_backup()
            job = self.page.job
            self.app.open_logs()
            self.app.open_backups()
            self.assertIs(self.app.backup_page, self.page)
            self.assertIs(self.page.job, job)
            self.assertEqual(self.page.directory.get(), self.temp.name)
            start.assert_called_once()

    def test_backup_destination_never_overwrites_existing_archive(self):
        from motionstudio.backup.transfer import BackupDestination
        from motionstudio.backup.view import backup_filename
        existing = Path(self.temp.name)/backup_filename('Fixture project')
        existing.write_bytes(b'keep')
        with patch('motionstudio.backup.view.BackupJob.start'):
            self.page.make_backup()
            question = BackupDestination(filename=existing.name)
            self.page.job.events.put(('backup_destination', question))
            self.poll()
            self.assertTrue(question.answered.is_set())
            self.assertNotEqual(Path(question.path), existing)
            self.assertEqual(existing.read_bytes(), b'keep')

    def test_cancelled_backup_does_not_answer_destination_with_a_path(self):
        from motionstudio.backup.transfer import BackupDestination
        with patch('motionstudio.backup.view.BackupJob.start'):
            self.page.make_backup()
            self.page.cancel()
            question = BackupDestination()
            self.page.job.events.put(('backup_destination', question))
            self.poll()
            self.assertTrue(question.answered.is_set())
            self.assertIsNone(question.path)
            self.page.job.events.put(('backup_finished', 'Cancelled; no backup saved.'))
            self.poll()
            self.assertIsNone(self.page.job)
            self.assertFalse(self.page.start_button.instate(['disabled']))
            self.assertIn('Cancelled', self.page.activity.get('1.0', 'end'))

    def test_custom_contents_and_validation_happen_before_transfer(self):
        with patch('motionstudio.backup.view.BackupJob.start') as start:
            self.page.full.set(False)
            for name in ('programs', 'apps', 'logs', 'flights'):
                getattr(self.page, name).set(False)
            self.page.update_contents()
            self.root.update()
            self.assertTrue(self.page.items.winfo_ismapped())
            self.assertIn('at least one', self.page.contents.get())
            self.page.make_backup()
            start.assert_not_called()
            self.assertIsNone(self.page.job)
            self.page.programs.set(True)
            self.page.make_backup()
            self.assertEqual(self.page.job.sources, [('/programs', 'directory')])
            self.assertFalse(self.page.job.full)

    def test_each_item_can_be_backed_up_alone_or_together(self):
        expected = {'programs': ['/programs'], 'apps': ['/apps', '/data/apps'],
                    'logs': ['/root/log_history.txt', '/root/log_history.bak'], 'flights': ['/data/flights']}
        self.page.full.set(False)
        self.page.remote_flights.set('/data/flights')
        for chosen in (('logs',), ('flights',), ('apps',), ('programs', 'logs', 'flights')):
            with self.subTest(chosen=chosen), patch('motionstudio.backup.view.BackupJob.start'):
                for name in expected:
                    getattr(self.page, name).set(name in chosen)
                self.page.update_contents()
                self.page.make_backup()
                self.assertEqual([path for path, _kind in self.page.job.sources],
                                 [path for name in expected if name in chosen for path in expected[name]])
                self.page.job.events.put(('backup_finished', 'Saved'))
                self.poll()

    def test_complete_backup_hides_item_choices_and_download_progress_is_determinate(self):
        self.page.full.set(True)
        self.page.update_contents()
        self.root.update()
        self.assertFalse(self.page.items.winfo_ismapped())
        with patch('motionstudio.backup.view.BackupJob.start'):
            self.page.make_backup()
            self.assertTrue(self.page.job.full)
            self.assertEqual(len(self.page.job.sources), 6)
            self.page.job.events.put(('progress', .25))
            self.page.job.events.put(('status', 'Downloading backup · 1.00 / 4.00 MiB'))
            self.page.job.events.put(('status', 'Downloading backup · 2.00 / 4.00 MiB'))
            self.poll()
            self.assertEqual(str(self.page.progress.cget('mode')), 'determinate')
            self.assertAlmostEqual(float(self.page.progress.cget('value')), 25.)
            self.assertEqual(self.page.activity.get('1.0', 'end').count('Downloading backup'), 1)
            self.assertIn('2.00', self.page.status.get())

    def test_empty_password_and_missing_folder_do_not_start_robot_operation(self):
        with patch('motionstudio.backup.view.BackupJob.start') as start:
            self.page.password.set('')
            self.page.make_backup()
            self.assertIn('password', self.page.status.get())
            self.page.password.set('test-only')
            self.page.directory.set(str(Path(self.temp.name)/'missing'))
            self.page.make_backup()
            self.assertIn('existing local destination', self.page.status.get())
            start.assert_not_called()
