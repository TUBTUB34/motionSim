"""Run with MOTIONSIM_UI_TESTS=1 in a graphical desktop session."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_logs import line


class HeadlessLogUITests(unittest.TestCase):
    def test_missing_password_shows_validation_before_starting_worker(self):
        from unittest.mock import Mock
        from log_view import LogWindow
        for action, title in ((LogWindow.fetch, 'Cannot fetch robot logs'),
                              (LogWindow.make_backup, 'Cannot make robot backup')):
            with self.subTest(action=action.__name__):
                window = Mock(job=None)
                window.ip.get.return_value = '127.0.0.1'
                window.username.get.return_value = 'root'
                window.password.get.return_value = ''
                window.remote_path.get.return_value = '/root/log_history.txt'
                with patch('log_view.messagebox.showerror') as popup:
                    action(window)
                popup.assert_called_once_with(
                    title, 'Enter an SSH username and password to connect to the robot.', parent=window)
                window.begin.assert_not_called()

    def test_aggregate_checkbox_toggles_running_total_without_changing_dates(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from matplotlib.figure import Figure
        from log_view import LogWindow
        figure = Figure()
        window = SimpleNamespace(
            axes=figure.add_subplot(), data=True, period=Mock(get=lambda: 'Day'),
            start_date=Mock(get=lambda: ''), end_date=Mock(get=lambda: ''),
            split_code=Mock(get=lambda: False), chart_kind=Mock(get=lambda: 'Line'),
            aggregate=Mock(get=lambda: True),
            toolbar=Mock(), canvas=Mock())
        events = [dict(date=day, joint=joint, code='C153A0') for day, joint in
                  [('2026-04-01', 'Base'), ('2026-04-02', 'Base'), ('2026-04-02', 'Shoulder')]]
        LogWindow.draw_chart(window, events)
        self.assertEqual(list(window.axes.lines[0].get_ydata()), [1, 2])
        self.assertEqual(list(window.axes.lines[1].get_ydata()), [0, 1])
        self.assertEqual([label.get_text() for label in window.axes.get_xticklabels()],
                         ['2026-04-01', '2026-04-02'])
        self.assertIn('Cumulative', window.axes.get_title(loc='left'))
        window.aggregate.get = lambda: False
        LogWindow.draw_chart(window, events)
        self.assertEqual(list(window.axes.lines[0].get_ydata()), [1, 1])
        self.assertNotIn('Cumulative', window.axes.get_title(loc='left'))

    def test_backup_dialog_answers_worker_and_completion_preserves_data(self):
        import queue
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from backup_transfer import BackupDestination
        from log_view import LogWindow
        for destination in ('/tmp/backup.tar', ''):
            with self.subTest(destination=destination):
                question = BackupDestination()
                job = SimpleNamespace(events=queue.Queue(), stopped=threading.Event())
                job.events.put(('backup_destination', question))
                job.events.put(('backup_finished', 'Backup result'))
                window = Mock(job=job, closed=True, save_directory=None)
                original_data = window.data
                with patch('log_view.filedialog.asksaveasfilename', return_value=destination):
                    LogWindow.poll(window)
                self.assertTrue(question.answered.is_set())
                self.assertEqual(question.path, destination or None)
                window.finish.assert_called_once()
                window.status.set.assert_called_with('Backup result')
                window.set_data.assert_not_called()
                self.assertIs(window.data, original_data)

    def test_fetch_optional_save_remembers_folder(self):
        from unittest.mock import Mock
        from log_view import LogWindow
        for accept, filename in ((False, ''), (True, ''), (True, '/tmp/log_history.txt')):
            with self.subTest(accept=accept, filename=filename):
                window = Mock(job=None, save_directory=None)
                window.ip.get.return_value = '127.0.0.1'
                window.username.get.return_value = 'root'
                window.password.get.return_value = 'secret'
                window.remote_path.get.return_value = '/root/log_history.txt'
                with patch('log_view.messagebox.askyesno', return_value=accept), patch(
                        'log_view.filedialog.asksaveasfilename', return_value=filename) as dialog, patch(
                        'log_view.LogJob') as factory:
                    LogWindow.fetch(window)
                self.assertEqual(dialog.call_count, int(accept))
                self.assertEqual(factory.call_args.kwargs['save_path'], filename or None)
                self.assertEqual(window.save_directory, Path('/tmp') if filename else None)
                window.begin.assert_called_once()

    def test_backup_reuses_folder_without_overwriting_existing_archive(self):
        import queue
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        from backup_transfer import BackupDestination
        from log_view import LogWindow
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / 'backup.tar'
            original.write_bytes(b'previous backup')
            question = BackupDestination()
            job = SimpleNamespace(events=queue.Queue(), stopped=threading.Event())
            job.events.put(('backup_destination', question))
            window = Mock(job=job, closed=True, save_directory=Path(folder))
            with patch('log_view.filedialog.asksaveasfilename') as dialog:
                LogWindow.poll(window)
            dialog.assert_not_called()
            self.assertTrue(question.answered.is_set())
            self.assertEqual(Path(question.path).parent, Path(folder))
            self.assertNotEqual(Path(question.path), original)
            self.assertEqual(original.read_bytes(), b'previous backup')


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class LogUITests(unittest.TestCase):
    def test_changed_host_popup_explains_replacement(self):
        import queue
        import threading
        import tkinter as tk
        from unittest.mock import Mock
        from main import SimulatorApp
        from session import HostKeyQuestion
        root = tk.Tk()
        app = SimulatorApp(root)
        try:
            app.open_logs()
            window = app.log_window
            job = Mock(events=queue.Queue(), stopped=threading.Event())
            question = HostKeyQuestion('127.0.0.1', 'Old fingerprint: old\nNew fingerprint: new', replacement=True)
            job.events.put(('host_key', question))
            window.job = job
            with patch('log_view.messagebox.askyesno', return_value=True) as popup:
                deadline = time.monotonic() + 2
                while not question.answered.is_set() and time.monotonic() < deadline:
                    root.update()
                    time.sleep(.01)
                self.assertTrue(question.answered.is_set())
                self.assertTrue(question.accepted)
                self.assertEqual(popup.call_args.args[0], 'Replace saved robot SSH key?')
                self.assertIn('Remove the old key and update to the new one?', popup.call_args.args[1])
                self.assertEqual(popup.call_args.kwargs['default'], 'no')
        finally:
            app.close()

    def test_desktop_workflow_and_simulator_navigation(self):
        import tkinter as tk
        from main import SimulatorApp
        from log_transfer import LogJob
        root = tk.Tk()
        app = SimulatorApp(root)
        try:
            with patch('webbrowser.open') as browser:
                app.open_logs()
                window = app.log_window
                root.update()
                self.assertIsNone(app.session)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / 'log.txt'
                    path.write_text(line() + line('C157A1:6', day='2026-04-03', uptime='2s'))
                    window.begin(LogJob(path=path))
                    deadline = time.monotonic() + 5
                    while window.job and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertIsNone(window.job)
                    self.assertEqual(len(window.selected), 2)
                    for kind in ('Bar', 'Line', 'Scatter'):
                        window.chart_kind.set(kind)
                        window.refresh()
                        root.update()
                    window.aggregate.set(True)
                    window.chart_kind.set('Line')
                    window.refresh()
                    root.update()
                    self.assertIn('Cumulative', window.axes.get_title(loc='left'))
                    self.assertEqual(sum(line.get_ydata()[-1] for line in window.axes.lines), 2)
                    self.assertEqual([label.get_text() for label in window.axes.get_xticklabels()],
                                     ['2026-04-01', '2026-04-02', '2026-04-03'])
                    window.joint.set('Shoulder')
                    window.refresh()
                    self.assertEqual(len(window.selected), 1)
                    window.table.selection_set('0')
                    window.show_message()
                    self.assertIn('Collision', window.message.get())
                    csv_path = Path(folder) / 'events.csv'
                    with patch('log_view.filedialog.asksaveasfilename', return_value=str(csv_path)):
                        window.export_csv()
                    self.assertIn('C157A1', csv_path.read_text(encoding='utf-8-sig'))
                    self.assertNotIn('C153A0', csv_path.read_text(encoding='utf-8-sig'))
                    window.start_date.set('invalid')
                    window.refresh()
                    self.assertIn('YYYY-MM-DD', window.status.get())
                    self.assertEqual(len(window.selected), 1)
                    window.reset()
                    self.assertEqual(len(window.selected), 2)
                    window.begin(LogJob(path=Path(folder) / 'missing.txt'))
                    deadline = time.monotonic() + 5
                    while window.job and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertIn('Log not loaded', window.status.get())
                    self.assertEqual(len(window.selected), 2)
                window.hide()
                app.show_setup()
                app.open_logs()
                self.assertIs(app.log_window, window)
                self.assertEqual(len(window.selected), 2)
                browser.assert_not_called()
        finally:
            app.close()

    def test_backup_save_dialog_and_completion_keep_loaded_log(self):
        import tkinter as tk
        from main import SimulatorApp
        from log_data import load_log
        from tests.test_backup import BackupTests
        root = tk.Tk()
        app = SimulatorApp(root)
        try:
            app.open_logs()
            window = app.log_window
            with tempfile.TemporaryDirectory() as folder:
                log = Path(folder) / 'log.txt'
                log.write_text(line())
                window.set_data(load_log(log))
                data = window.data
                destination = Path(folder) / 'backup.tar'
                job, _, _, sftp = BackupTests().make_job(destination)
                # Exercise the actual queue/dialog exchange with the worker.
                del job.choose_destination
                window.password.set('secret')
                with patch('log_view.BackupJob', return_value=job), patch(
                        'log_view.filedialog.asksaveasfilename', return_value=str(destination)) as dialog:
                    window.make_backup()
                    self.assertEqual(str(window.backup_button['state']), 'disabled')
                    deadline = time.monotonic() + 5
                    while window.job and time.monotonic() < deadline:
                        root.update()
                        time.sleep(.01)
                    self.assertIsNone(window.job)
                    dialog.assert_called_once()
                    self.assertEqual(destination.read_bytes(), b'archive content')
                    sftp.remove.assert_called_once()
                    self.assertEqual(str(window.backup_button['state']), 'normal')
                    self.assertIn('Robot copy removed', window.status.get())
                    self.assertIs(window.data, data)
                    self.assertEqual(len(window.selected), 1)
        finally:
            app.close()

    def test_csv_formula_guard(self):
        from log_view import LogWindow
        self.assertEqual(LogWindow.csv_cell('=1+1'), "'=1+1")
        self.assertEqual(LogWindow.csv_cell('normal'), 'normal')
