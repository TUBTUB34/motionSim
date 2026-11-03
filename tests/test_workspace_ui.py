"""Persistent page navigation and explicit side-by-side simulator behavior."""
import gc
import os
import unittest
from unittest.mock import patch

from tests.test_flight_ui import flight


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class WorkspaceUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.discovery = patch('flight_view.FlightWindow._discover')
        self.discovery.start()
        self.app = SimulatorApp(self.root)

    def tearDown(self):
        self.app.close()
        self.discovery.stop()
        del self.app
        del self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def windows(self):
        import tkinter as tk
        return [child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel)]

    def test_primary_workflows_are_embedded_and_navigation_is_persistent(self):
        shell = self.app.shell
        for name in ('home', 'reports', 'logs', 'flights', 'backups', 'checklist', 'simulator'):
            with self.subTest(page=name):
                self.app.show_page(name)
                self.root.update()
                self.assertEqual(self.app.active_page, name)
                self.assertEqual(shell.active, name)
                self.assertTrue(shell.buttons[name].winfo_viewable())
                self.assertEqual(shell.buttons[name].cget('style'), 'Accent.TButton')
                self.assertEqual(self.windows(), [])
        for panel in (self.app.log_window, self.app.flight_window, self.app.program_check_window,
                      self.app.backup_page):
            self.assertTrue(panel.embedded)

    def test_reports_have_two_distinct_tabs_and_preserve_each_workflow(self):
        self.app.open_program_check()
        program = self.app.program_check_window
        program.working_folder.set('/selected/programs')
        self.app.open_diagnostics()
        status = self.app.diagnostic_window
        status.folder.set('/selected/status-evidence')
        self.root.update()
        self.assertEqual([self.app.report_tabs.tab(tab, 'text') for tab in self.app.report_tabs.tabs()],
                         ['Program Checker', 'Robot Status'])
        self.app.open_logs()
        self.app.open_program_check()
        self.assertIs(self.app.program_check_window, program)
        self.assertEqual(program.working_folder.get(), '/selected/programs')
        self.assertEqual(status.folder.get(), '/selected/status-evidence')
        self.assertEqual(self.windows(), [])

    def test_setup_rebuild_does_not_destroy_analysis_pages(self):
        self.app.open_logs()
        logs = self.app.log_window
        logs.start_date.set('2026-01-01')
        self.app.open_flights()
        source = self.app.flight_window
        report = flight()
        source.set_report(report)
        source.seek(.02)
        self.app.show_setup()
        self.app.open_flights()
        self.root.update()
        self.assertIs(source, self.app.flight_window)
        self.assertIs(source.report, report)
        self.assertAlmostEqual(source.timeline.elapsed, .02)
        self.assertEqual(logs.start_date.get(), '2026-01-01')
        self.assertTrue(logs.winfo_exists())

    def test_return_does_not_connect_from_other_pages(self):
        self.assertEqual(self.app.active_page, 'home')
        self.assertFalse(self.root.bind('<Return>'))
        self.app.show_workspace()
        self.assertTrue(self.root.bind('<Return>'))
        self.app.open_logs()
        self.assertFalse(self.root.bind('<Return>'))
        self.app.show_workspace()
        self.assertTrue(self.root.bind('<Return>'))

    def test_home_tasks_preserve_work_and_do_not_start_robot_operations(self):
        self.app.home_page.task_buttons['Program Checker'].invoke()
        self.app.program_check_window.working_folder.set('/local/program-review')
        self.app.open_home()
        self.app.home_page.task_buttons['Robot backup'].invoke()
        self.assertIsNone(self.app.backup_page.job)
        self.app.open_home()
        self.app.home_page.task_buttons['Program Checker'].invoke()
        self.assertEqual(self.app.program_check_window.working_folder.get(), '/local/program-review')
        self.assertIsNone(self.app.session)

    def test_connection_navigation_does_not_disconnect_and_stale_data_is_visible(self):
        import time
        from unittest.mock import Mock
        from connection import make_settings
        from rtde_client import RobotSample
        self.app.settings = make_settings('192.0.2.1', '', '', '')
        self.app.show_view()
        self.app.session = Mock()
        self.app.last_sample = RobotSample(1, (0,) * 6, (0,) * 6, time.monotonic() - 2)
        self.app.open_home()
        self.assertIn('Stale telemetry', self.app.home_page.connection.get())
        self.app.edit_connection_page()
        self.app.session.stop.assert_not_called()
        self.assertEqual(self.app.active_page, 'simulator')

    def test_unsaved_checklist_can_cancel_app_close_before_workers_stop(self):
        from unittest.mock import Mock
        self.app.open_checklist()
        self.app.checklist_page.metadata['project'].set('Unsaved review')
        session = self.app.session = Mock()
        with patch('commissioning_view.messagebox.askyesno', return_value=False):
            self.app.close()
        self.assertFalse(self.app.closed)
        session.stop.assert_not_called()
        self.app.checklist_page.saved_record = self.app.checklist_page.record.copy()

    def test_flight_retrieval_is_a_tab_in_same_page(self):
        self.app.open_flight_retrieval()
        self.root.update()
        self.assertEqual(self.app.active_page, 'flights')
        self.assertEqual(self.app.flight_tabs.select(), str(self.app.retrieval_host))
        self.assertTrue(self.app.flight_retrieval_window.embedded)
        self.assertEqual(self.windows(), [])
        self.app.open_logs()
        self.app.show_page('flights')
        self.assertEqual(self.app.flight_tabs.select(), str(self.app.retrieval_host))
        source = self.app.flight_window
        source.deiconify()
        self.root.update()
        self.assertEqual(self.app.flight_tabs.select(), str(self.app.flight_host))

    def test_backup_shortcut_only_opens_backup_page(self):
        self.app.make_full_backup()
        self.root.update()
        self.assertEqual(self.app.active_page, 'backups')
        self.assertTrue(self.app.backup_page.full.get())
        self.assertIsNone(self.app.backup_page.job)
        self.assertIsNone(self.app.log_window)

    def test_separate_simulator_is_explicit_and_shares_recording_controls(self):
        self.app.open_flights()
        source = self.app.flight_window
        source.set_report(flight())
        self.app.open_simulator_window()
        popout = self.app.simulator_popout
        popout.source_mode.set('Flight report')
        popout.change_source()
        self.root.update()
        self.assertEqual(self.app.active_page, 'flights')
        self.assertEqual(len(self.windows()), 1)
        self.assertTrue(self.app.is_recording_visible(source))
        source.seek(.02)
        self.assertAlmostEqual(popout.recorded.robot.sample.joints[0], .005)
        self.app.open_simulator_window()
        self.assertIs(self.app.simulator_popout, popout)
        self.assertEqual(len(self.windows()), 1)
        popout.close()
        self.assertIsNone(self.app.simulator_popout)
        self.assertFalse(self.app.is_recording_visible(source))
        self.assertIs(self.app.flight_window, source)

    def test_hidden_analysis_can_feed_visible_separate_simulator(self):
        self.app.open_flights()
        source = self.app.flight_window
        source.set_report(flight())
        self.app.simulator_source.set('Flight report')
        self.app.open_simulator_window()
        self.app.open_logs()
        self.root.update()
        self.assertFalse(source.winfo_viewable())
        self.assertTrue(self.app.is_recording_visible(source))
        source.seek(.03)
        self.assertAlmostEqual(self.app.simulator_popout.recorded.robot.sample.joints[0], .0075)


if __name__ == '__main__':
    unittest.main()
