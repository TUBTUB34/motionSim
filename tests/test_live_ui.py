"""Live monitor end to end against the loopback RTDE robot; opt in with MOTIONSIM_UI_TESTS=1."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class LiveMonitorTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from motionstudio.app.main_window import SimulatorApp
        from motionstudio.live.rtde_client import RTDEClient
        from motionstudio.live.session import RobotSession
        from tests.test_ui import MockRobot
        self.folder = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app = SimulatorApp(self.root, preferences_path=Path(self.folder.name) / 'preferences.json')
        self.app.flight_library_root = Path(self.folder.name)
        self.robot = MockRobot()

        class Transfer:
            def fetch(self, *_args):
                raise OSError('no installation in this test')

            def close(self):
                pass

        with patch('motionstudio.app.main_window.RobotSession', lambda settings: RobotSession(
                settings, lambda host: RTDEClient(host, port=self.robot.port), Transfer)):
            self.app.ip.set('127.0.0.1')
            self.app.model.set('UR10e')
            self.app.submit()
        self.pump(lambda: self.app.last_sample is not None)

    def tearDown(self):
        self.app.live_capture.discard_recording()
        self.app.close()
        self.robot.close()
        self.folder.cleanup()
        self.assertEqual(self.errors, [])

    def pump(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for the live stream')

    def test_record_stop_save_and_open_in_flight_reports(self):
        from motionstudio.flight.data import load_flight
        from motionstudio.live.recorder import REPORT_TYPE
        self.app.open_live()
        page = self.app.live_page
        self.pump(lambda: page.capture.sample_rate() is not None)
        self.assertIn('Live · UR10e · 127.0.0.1', page.state.get())
        self.assertTrue(page.record_button.instate(['!disabled']))
        # Save and Discard appear only once there is a finished recording.
        self.assertFalse(page.save_button.winfo_manager())
        page.toggle_recording()
        recording = page.capture.recording
        self.app.open_logs()  # recording continues while another page is shown
        self.pump(lambda: recording.samples >= 20)
        self.app.open_live()
        page.toggle_recording()
        self.assertFalse(recording.active)
        self.pump(lambda: page.save_button.instate(['!disabled']) and page.save_button.winfo_manager())
        target = Path(self.folder.name) / 'cell2-live.zip'
        page.notes.set('Cycle after gripper change')
        with patch('motionstudio.live.view.filedialog.asksaveasfilename', return_value=str(target)):
            page.save_recording()
            self.pump(lambda: page.saved_path is not None)
        report = load_flight(target)
        self.assertEqual(report.metadata['report_type'], REPORT_TYPE)
        self.assertEqual(report.metadata['notes'], 'Cycle after gripper change')
        self.assertEqual(report.model, 'UR10e')
        self.assertGreaterEqual(report.sample_count, 20)
        self.assertIsNone(page.capture.recording)
        self.assertIn('Saved', page.record_text.get())
        page.open_button.invoke()
        self.pump(lambda: self.app.flight_window.report is not None
                  and self.app.flight_window.report.path == target.resolve())
        self.assertEqual(self.app.active_page, 'flights')

    def test_separate_joint_graphs_scroll_and_tcp_tiles(self):
        self.app.open_live()
        page = self.app.live_page
        self.assertEqual(page.view.get(), 'All readings')
        self.assertTrue(page.scale_check.instate(['disabled']))
        page.select_view('TCP')
        self.assertEqual(len(page.charts.axes), 2)
        self.assertTrue(page.joint_group.flow_hidden and not page.tcp_group.flow_hidden)
        page.select_view('All readings')
        self.assertFalse(page.joint_group.flow_hidden)
        page.shared_scale.set(True)
        page.select_layout('Separate joints')
        self.assertTrue(page.scale_check.instate(['!disabled']))
        self.assertEqual(len(page.charts.axes), 5 * 6 + 2)
        self.pump(lambda: page.canvas.draw_seconds > 0)
        self.assertLess(page.chart_viewport.yview()[1], 1)
        page.scroll_chart('moveto', 1)
        self.pump(lambda: page.charts.axes[-1].get_visible())
        # The loopback robot streams actual_TCP_speed, so both tiles show values.
        self.pump(lambda: page.tiles['tcp_acceleration'].cget('text').endswith('m/s²'))
        self.assertTrue(page.tiles['tcp_speed'].cget('text').endswith('m/s'))
        # The rotational part of the same stream, in degrees per second.
        page.select_tcp_motion('rotation')
        self.assertIn('rotation', page.charts.axes[-2].get_title(loc='left'))
        self.pump(lambda: page.tiles['tcp_speed'].cget('text').endswith('°/s'))
        page.select_tcp_motion('linear')
        # Older controllers omit it; the tiles must say so rather than show zero.
        self.assertEqual(page._tcp_readouts({}), ('Unavailable', 'Unavailable'))

    def test_disconnect_stops_recording_and_unsaved_work_guards_close(self):
        self.app.open_live()
        page = self.app.live_page
        page.toggle_recording()
        self.pump(lambda: page.capture.recording.samples >= 5)
        self.app.disconnect()
        self.assertFalse(page.capture.recording.active)
        self.assertIn('connection ended', page.capture.recording.stop_reason)
        with patch('motionstudio.live.view.messagebox.askyesno', return_value=False) as ask:
            self.app.close()
            ask.assert_called_once()
        self.assertFalse(self.app.closed)
        self.assertIsNotNone(page.capture.recording)
