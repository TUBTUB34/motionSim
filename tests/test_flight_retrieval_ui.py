"""Native retrieval workflows with a queued fake transport; no robot connections."""
import os
from pathlib import Path
import queue
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flight_transfer import RemoteFlight


class QueuedTransfer:
    def __init__(self, connection, **kwargs):
        self.connection = connection
        self.options = kwargs
        self.events = queue.Queue()
        self.busy = False
        self.failed = []
        self.closed = False
        self.cancelled = False
        self.requests = []

    def start(self):
        pass

    def refresh(self, **kwargs):
        self.requests.append(('refresh', kwargs))
        self.busy = True
        return True

    def download(self, entries=None):
        self.requests.append(('download', entries))
        self.busy = True
        return True

    def retry(self):
        self.requests.append(('retry', self.failed.copy()))
        self.busy = bool(self.failed)
        return self.busy

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class FlightRetrievalUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from connection import make_settings
        from flight_retrieval_view import RobotFlightWindow
        from ui_theme import apply_theme
        self.root = tk.Tk()
        self.root.withdraw()
        apply_theme(self.root)
        self.temporary = tempfile.TemporaryDirectory()
        self.app = SimpleNamespace(root=self.root, session=None,
                settings=make_settings('192.0.2.1', 'root', 'fixture-password', 'default'),
                flight_window=None, flight_library_root=Path(self.temporary.name))
        self.patcher = patch('flight_retrieval_view.FlightTransfer', QueuedTransfer)
        self.patcher.start()
        self.window = RobotFlightWindow(self.app)
        self.window.after_cancel(self.window.after_id)
        self.root.update_idletasks()
        self.entries = [RemoteFlight('recording20261012_15_10_47.zip', '/root/flightreports/one.zip', 1048576, 1),
                        RemoteFlight('recording20261012_15_11_33.zip', '/root/flightreports/two.zip', 2097152, 2)]

    def tearDown(self):
        self.window.shutdown()
        self.root.destroy()
        self.patcher.stop()
        self.temporary.cleanup()

    def connect(self, sample=True):
        self.app.session = SimpleNamespace(stop_event=threading.Event(), sample=lambda: object() if sample else None)
        self.window._sync_connection()

    def listed(self):
        self.connect()
        self.window.refresh()
        self.window.controller.busy = False
        self.window._handle_event('listed', self.entries)
        self.window._handle_event('finished', dict(operation='list', cancelled=False, failed=0, succeeded=0))

    def test_folder_change_clears_old_status_and_rebuilds_scoped_controller(self):
        self.listed()
        previous = self.window.controller
        folder = Path(self.temporary.name) / 'second'
        folder.mkdir()
        self.window.set_library(folder)
        self.assertTrue(previous.closed)
        self.assertIsNone(self.window.controller)
        self.assertFalse(self.window.tree.get_children())
        self.assertFalse(self.window.entries)
        self.assertEqual(self.app.flight_library_root, folder)
        self.window.refresh()
        self.assertEqual(self.window.controller.options['library'], folder)

    def test_graph_and_retrieval_share_folder_and_guard_active_download(self):
        from flight_view import FlightWindow
        viewer = FlightWindow(self.app)
        self.app.flight_window = viewer
        self.app.flight_retrieval_window = self.window
        try:
            folder = Path(self.temporary.name) / 'second'
            folder.mkdir()
            self.window.set_library(folder)
            self.assertEqual(Path(viewer.library.get()), folder)
            viewer.set_library(self.temporary.name)
            self.assertEqual(Path(self.window.library.get()), Path(self.temporary.name))
            self.listed()
            self.window.download_new()
            with patch('flight_view.filedialog.askdirectory') as chooser:
                viewer.choose_folder()
            chooser.assert_not_called()
            self.assertIn('cancel', viewer.status.get())
            self.assertEqual(Path(viewer.library.get()), Path(self.temporary.name))
        finally:
            viewer.shutdown()
            self.app.flight_window = None

    def test_disconnected_and_connecting_gate_network_actions(self):
        self.assertTrue(self.window.refresh_button.instate(['disabled']))
        self.window.refresh()
        self.assertIsNone(self.window.controller)
        self.connect(sample=False)
        self.assertEqual(self.window._connection_state()[0], 'connecting')
        self.assertTrue(self.window.refresh_button.instate(['disabled']))
        self.app.session.sample = lambda: object()
        self.window._sync_connection()
        self.assertTrue(self.window.refresh_button.instate(['!disabled']))
        self.window.refresh()
        self.assertEqual(self.window.controller.connection.password, 'fixture-password')
        self.assertTrue(self.window.refresh_button.instate(['disabled']))

    def test_multi_select_all_new_and_busy_controls(self):
        self.listed()
        self.window.tree.selection_set([entry.path for entry in self.entries])
        self.window._update_controls()
        self.window.download_button.invoke()
        self.assertEqual(self.window.controller.requests[-1], ('download', self.entries))
        self.assertTrue(self.window.new_button.instate(['disabled']))
        self.assertTrue(self.window.cancel_button.instate(['!disabled']))
        self.window.controller.busy = False
        self.window._handle_event('finished', dict(operation='download', failed=0, succeeded=2))
        self.window.new_button.invoke()
        self.assertEqual(self.window.controller.requests[-1], ('download', None))

    def test_reconnect_invalidates_controller_and_old_robot_listing(self):
        self.listed()
        previous = self.window.controller
        from dataclasses import replace
        self.app.settings = replace(self.app.settings, ip='192.0.2.2')
        self.connect()
        self.assertIn('192.0.2.2', self.window.connection_status.get())
        self.assertTrue(previous.closed)
        self.assertIsNone(self.window.controller)
        self.assertFalse(self.window.tree.get_children())
        self.window.refresh()
        self.assertIsNot(previous, self.window.controller)
        self.app.session.stop_event.set()
        current = self.window.controller
        self.window._sync_connection()
        self.assertTrue(current.closed)
        self.assertTrue(self.window.refresh_button.instate(['disabled']))

    def test_edited_credentials_cannot_download_from_old_selection(self):
        self.listed()
        previous = self.window.controller
        self.window.tree.selection_set(self.entries[0].path)
        self.window.password.set('updated-fixture-password')
        self.window.download_selected()
        self.assertTrue(previous.closed)
        self.assertIsNot(previous, self.window.controller)
        self.assertEqual(self.window.controller.requests, [])
        self.assertFalse(self.window.tree.get_children())
        self.assertIn('Refresh', self.window.status.get())

    def test_selected_download_updates_live_selector_without_interrupting_playback(self):
        from flight_view import FlightWindow
        from tests.test_flight_ui import flight
        with patch('flight_view.discover_flights', return_value=[]), patch('flight_view.discover_library', return_value=[]):
            viewer = FlightWindow(self.app)
            viewer.executor.submit(lambda: None).result(timeout=5)
        self.app.flight_window = viewer
        viewer.after_cancel(viewer.after_id)
        try:
            report = flight()
            viewer.set_report(report)
            viewer.seek(.025)
            viewer.timeline.play()
            self.listed()
            self.window.tree.selection_set(self.entries[0].path)
            self.window.download_selected()
            path = Path(self.temporary.name) / 'downloaded.zip'
            self.window._handle_event('downloaded', dict(entry=self.entries[0], path=path, skipped=False))
            self.assertIn(path, viewer.paths)
            self.assertIn(path.name, viewer.flight_combo.cget('values'))
            self.assertIs(viewer.report, report)
            self.assertTrue(viewer.timeline.playing)
            self.assertAlmostEqual(viewer.timeline.elapsed, .025)
            self.assertEqual(viewer.flight.get(), report.name)
            self.assertTrue(viewer.export_button.instate(['!disabled']))
        finally:
            viewer.shutdown()
            self.app.flight_window = None

    def test_progress_and_completed_report_reach_existing_viewer(self):
        self.listed()
        self.window._handle_event('progress', dict(name=self.entries[0].name, file_bytes=524288,
                file_total=1048576, overall_bytes=524288, overall_total=3145728,
                index=1, count=2, rate=262144, eta=10))
        self.assertEqual(float(self.window.file_progress.cget('value')), 50)
        self.assertAlmostEqual(float(self.window.overall_progress.cget('value')), 100/6)
        self.assertIn('/s', self.window.overall_progress_text.get())
        self.assertIn('10 s remaining', self.window.overall_progress_text.get())
        self.app.flight_window = Mock()
        self.app.flight_window.winfo_exists.return_value = True
        path = Path(self.temporary.name) / 'validated.zip'
        self.window._handle_event('downloaded', dict(entry=self.entries[0], path=path, skipped=False))
        self.app.flight_window.add_download.assert_called_once_with(path)
        self.assertIn('verified', self.window.tree.set(self.entries[0].path, 'state'))
        self.assertIn('1 new or changed', self.window.summary.get())

    def test_cancelled_remainder_can_be_retried(self):
        self.listed()
        self.window.download_new()
        self.window.cancel()
        self.assertTrue(self.window.controller.cancelled)
        self.window.controller.failed = [self.entries[1].path]
        self.window.controller.busy = False
        self.window._handle_event('finished', dict(operation='download', cancelled=True, failed=0, succeeded=1))
        self.assertTrue(self.window.retry_button.instate(['!disabled']))
        self.window.retry_button.invoke()
        self.assertEqual(self.window.controller.requests[-1], ('retry', [self.entries[1].path]))

    def test_failure_survives_final_list_refresh(self):
        self.listed()
        self.window.download_new()
        self.window.controller.failed = [self.entries[1].path]
        self.window._handle_event('error', dict(path=self.entries[1].path, message='Connection timed out'))
        self.window._handle_event('listed', self.entries)
        self.window.controller.busy = False
        self.window._handle_event('finished', dict(operation='download', cancelled=False, failed=1, succeeded=1))
        self.assertIn('failed', self.window.status.get().lower())
        self.assertIn('Connection timed out', self.window.status.get())
        self.assertIn('failed', self.window.tree.set(self.entries[1].path, 'state').lower())
        self.assertTrue(self.window.retry_button.instate(['!disabled']))

    def test_host_key_prompt_always_releases_worker(self):
        from session import HostKeyQuestion
        question = HostKeyQuestion('192.0.2.1', 'SHA256:fixture', threading.Event())
        with patch('flight_retrieval_view.messagebox.askyesno', return_value=False):
            self.window._handle_event('host_key', question)
        self.assertTrue(question.answered.is_set())
        self.assertFalse(question.accepted)

    def test_compact_window_keeps_transfer_actions_accessible(self):
        from tkinter import ttk
        self.listed()
        self.window.geometry('920x740+0+0')
        # Windows delivers the native resize through the event queue; idle
        # geometry alone can still leave children at the previous width.
        self.root.update()
        self.root.update_idletasks()
        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)
        for control in descendants(self.window):
            if isinstance(control, ttk.Button):
                self.assertTrue(control.winfo_viewable(), control.cget('text'))
                self.assertGreaterEqual(control.winfo_rootx(), self.window.winfo_rootx())
                self.assertGreaterEqual(control.winfo_rooty(), self.window.winfo_rooty())
                self.assertLessEqual(control.winfo_rootx()+control.winfo_width(), self.window.winfo_rootx()+self.window.winfo_width()+1)
                self.assertLessEqual(control.winfo_rooty()+control.winfo_height(), self.window.winfo_rooty()+self.window.winfo_height()+1)


if __name__ == '__main__':
    unittest.main()
