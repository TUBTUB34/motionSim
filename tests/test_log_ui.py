"""Run with MOTIONSIM_UI_TESTS=1 in a graphical desktop session."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_logs import line


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

    def test_csv_formula_guard(self):
        from log_view import LogWindow
        self.assertEqual(LogWindow.csv_cell('=1+1'), "'=1+1")
        self.assertEqual(LogWindow.csv_cell('normal'), 'normal')
