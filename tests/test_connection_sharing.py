"""One simulator identity for downloads; offline inputs remain available."""
import gc
import os
import queue
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from connection import active_robot_settings, make_settings, transfer_connection
from backup_view import backup_filename


def session():
    stopped = threading.Event()
    return SimpleNamespace(stop_event=stopped, sample=lambda: object(), stop=stopped.set,
                           events=queue.Queue())


class ConnectionSharingTests(unittest.TestCase):
    def test_active_identity_overrides_page_values_but_keeps_requested_path(self):
        settings = make_settings('192.0.2.8', 'operator', 'session-secret', '')
        app = SimpleNamespace(settings=settings, session=session())
        result = transfer_connection(app, '192.0.2.9', 'other', 'page-secret', '/root/custom.txt')
        self.assertEqual((result.ip, result.username, result.password, result.path),
                         ('192.0.2.8', 'operator', 'session-secret', '/root/custom.txt'))
        app.session.stop_event.set()
        self.assertIsNone(active_robot_settings(app))
        result = transfer_connection(app, '192.0.2.9', 'other', 'page-secret')
        self.assertEqual(result.ip, '192.0.2.9')

    def test_active_robot_without_ssh_password_does_not_use_old_page_password(self):
        app = SimpleNamespace(settings=make_settings('192.0.2.8', 'root', '', ''), session=session())
        with self.assertRaisesRegex(ValueError, 'Simulator → Connection'):
            transfer_connection(app, '192.0.2.9', 'other', 'old-secret')

    def test_project_date_filename_is_portable_and_required(self):
        self.assertEqual(backup_filename(' Project One ', '2026-11-02'), 'Project One-2026-11-02.tar')
        name = backup_filename('Cell/One: "tool"?', '2026-11-02')
        self.assertFalse(any(c in name for c in '<>:"/\\|?*'))
        for project in ('', '  ', '..', '/\\:*?'):
            with self.subTest(project=project), self.assertRaises(ValueError):
                backup_filename(project)
        self.assertLess(len(backup_filename('機'*300).encode('utf-8')), 180)


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class SharedCredentialUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.app = SimulatorApp(self.root)
        self.root.after_cancel(self.app.poll_id)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.discovery = patch('flight_view.FlightWindow._discover')
        self.discovery.start()
        self.app.open_backups()
        self.app.open_logs()
        self.app.open_flight_retrieval()
        self.app.open_program_check()
        self.app.program_check_window.source_mode.set('Robot')
        self.app.program_check_window.change_source()
        self.app.open_diagnostics()
        self.app.diagnostic_window.source_mode.set('Robot')
        self.app.diagnostic_window.change_source()
        self.folder = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.app.close()
        self.discovery.stop()
        self.folder.cleanup()
        del self.app, self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def connect(self, ip='192.0.2.8', password='session-secret'):
        self.app.settings = make_settings(ip, 'operator', password, '')
        self.app.session = session()
        self.sync()

    def sync(self):
        for page in (self.app.backup_page, self.app.log_window,
                     self.app.program_check_window, self.app.diagnostic_window):
            page.sync_connection()
        self.app.flight_retrieval_window._sync_connection()
        self.root.update_idletasks()

    def credential_frames(self):
        return [self.app.backup_page.credentials, self.app.log_window.credentials,
                self.app.flight_retrieval_window.credentials, self.app.program_check_window.robot_controls,
                self.app.diagnostic_window.robot_controls]

    def test_connect_hides_all_duplicate_inputs_disconnect_restores_them(self):
        self.assertTrue(all(frame.winfo_manager() for frame in self.credential_frames()))
        self.connect()
        self.assertTrue(all(not frame.winfo_manager() for frame in self.credential_frames()))
        self.assertIn('192.0.2.8', self.app.backup_page.shared_connection.cget('text'))
        self.assertNotIn('session-secret', self.app.backup_page.shared_connection.cget('text'))
        self.app.session.stop_event.set()
        self.sync()
        self.assertTrue(all(frame.winfo_manager() for frame in self.credential_frames()))

    def test_backup_and_logs_resolve_shared_credentials_at_action_time(self):
        self.connect()
        backup, logs = self.app.backup_page, self.app.log_window
        backup.directory.set(self.folder.name)
        backup.project_name.set('Client cell')
        for page in (backup, logs):
            page.ip.set('192.0.2.99')
            page.password.set('stale-page-secret')
        with patch('backup_view.BackupJob.start'), patch('log_view.LogJob.start'), patch('log_view.messagebox.askyesno', return_value=False):
            backup.make_backup()
            logs.fetch()
        for page in (backup, logs):
            self.assertEqual(page.job.connection.ip, '192.0.2.8')
            self.assertEqual(page.job.connection.password, 'session-secret')
        from backup_transfer import BackupDestination
        question = BackupDestination()
        original_filename = backup._filename
        backup.project_name.set('Edited after start')
        backup.job.events.put(('backup_destination', question))
        backup.after_cancel(backup.after_id)
        backup.poll()
        self.assertEqual(Path(question.path).name, original_filename)
        self.assertTrue(original_filename.startswith('Client cell-'))
        running_job = backup.job
        self.connect('192.0.2.10', 'replacement-secret')
        self.assertIs(backup.job, running_job)
        self.assertEqual(running_job.connection.ip, '192.0.2.8')
        self.assertEqual(backup.ip.get(), '192.0.2.10')

    def test_reconnection_invalidates_flight_transport_and_uses_new_identity(self):
        from tests.test_flight_retrieval_ui import QueuedTransfer
        self.connect()
        flight = self.app.flight_retrieval_window
        with patch('flight_retrieval_view.FlightTransfer', QueuedTransfer):
            self.assertTrue(flight._ensure_controller())
            first = flight.controller
            self.connect('192.0.2.10', 'replacement-secret')
            self.assertTrue(first.closed)
            self.assertIsNone(flight.controller)
            flight.password.set('stale')
            self.assertTrue(flight._ensure_controller())
            self.assertEqual(flight.controller.connection.ip, '192.0.2.10')
            self.assertEqual(flight.controller.connection.password, 'replacement-secret')
            self.assertEqual(self.app.backup_page.password.get(), 'replacement-secret')
            self.assertEqual(self.app.log_window.ip.get(), '192.0.2.10')

    def test_report_downloads_share_the_simulator_identity(self):
        self.connect()
        program, status = self.app.program_check_window, self.app.diagnostic_window
        program.robot_password.set('stale')
        status.password.set('stale')
        with patch('program_transfer.ProgramSourceJob') as programs, patch('diagnostic_view.DiagnosticSourceJob') as diagnostics, patch('tkinter.filedialog.askdirectory', return_value=self.folder.name):
            program.pull_programs()
            status.choose_source()
            for factory in (programs, diagnostics):
                connection = factory.call_args.kwargs['connection']
                self.assertEqual(connection.ip, '192.0.2.8')
                self.assertEqual(connection.password, 'session-secret')

    def test_missing_shared_password_and_project_are_actionable_without_transfer(self):
        backup = self.app.backup_page
        backup.directory.set(self.folder.name)
        self.connect(password='')
        with patch('backup_view.BackupJob.start') as start:
            backup.make_backup()
            self.assertIn('project name', backup.status.get())
            backup.project_name.set('Client cell')
            backup.password.set('old-private-password')
            backup.make_backup()
            self.assertIn('Simulator → Connection', backup.status.get())
            start.assert_not_called()
        self.assertFalse(backup.credentials.winfo_manager())
