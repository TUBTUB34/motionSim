"""Native diagnostic workflow checks with local fixtures and mocked transport."""
import gc
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import MagicMock, patch


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class DiagnosticUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.app = SimulatorApp(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.app.open_diagnostics()
        self.window = self.app.diagnostic_window
        self.root.update()
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'log_history.txt').write_text(
            '3.5 :: 1s :: 2026-04-01 12:00:00 :: 110 :: C153A0:6 :: Smoke incident\n', encoding='utf-8')
        (self.folder / 'flight.csv').write_text(
            'Timestamp [s],Actual position j0 [rad]\n1,0.1\n1.002,0.2\n', encoding='utf-8')

    def tearDown(self):
        self.window.executor.submit(lambda: None).result(timeout=20)
        self.app.close()
        self.temp.cleanup()
        del self.window
        del self.app
        del self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def poll(self):
        self.window.after_cancel(self.window.after_id)
        self.window.poll()

    def finish(self):
        self.window.executor.submit(lambda: None).result(timeout=20)
        self.poll()
        self.root.update()

    def text(self):
        return self.window.details.get('1.0', 'end')

    def start_robot(self):
        self.window.source_mode.set('Robot')
        self.window.change_source()
        self.window.ip.set('192.0.2.1')
        self.window.username.set('root')
        self.window.password.set('test-credential')
        job = MagicMock()
        job.events = queue.Queue()
        with patch('diagnostic_view.DiagnosticSourceJob', return_value=job) as factory, patch(
                'diagnostic_view.filedialog.askdirectory', return_value=str(self.folder)):
            self.window.choose_source()
        factory.assert_called_once()
        job.start.assert_called_once_with()
        self.assertTrue(self.window.busy)
        self.assertTrue(self.window.selector.instate(['disabled']))
        self.assertIsNone(self.app.session)
        return job

    def test_local_analysis_navigation_export_and_workspace_preservation(self):
        with patch('diagnostic_view.filedialog.askdirectory', return_value=str(self.folder)):
            self.window.choose_source()
        self.finish()
        report = self.window.report
        self.assertIsNotNone(report, self.window.status.get())
        self.assertTrue(report.incidents)
        self.assertIn('Assessment', self.text())
        self.assertEqual(len(self.window.robot_status.joints), 6)
        self.window.cases.selection_set('joints')
        self.window.show_selection()
        for name in ('Base', 'Shoulder', 'Elbow', 'Wrist 1', 'Wrist 2', 'Wrist 3'):
            self.assertIn(name, self.text())
        self.assertFalse(self.window.include_evidence.get(), 'Client PDF must default to concise status assessment')
        self.window.cases.selection_set('0')
        self.window.show_selection()
        self.assertIn(report.incidents[0].title, self.text())
        self.assertIn('C153A0', self.text())
        self.window.cases.selection_set('sources')
        self.window.show_selection()
        self.assertIn('log_history.txt', self.text())
        self.assertTrue(self.window.export_button.instate(['!disabled']))
        target = self.folder / 'diagnostic.pdf'
        with patch('diagnostic_view.filedialog.asksaveasfilename', return_value=str(target)):
            self.window.export()
        self.finish()
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
        self.assertIn('Report saved', self.window.status.get())
        self.app.show_setup()
        self.assertTrue(self.window.winfo_exists())
        self.app.open_diagnostics()
        self.assertIs(self.app.diagnostic_window.report, report)

    def test_robot_collected_event_runs_local_analysis(self):
        job = self.start_robot()
        job.events.put(('collected', {'path': self.folder, 'warnings': [], 'complete': True,
                                      'downloaded_files': 2, 'message': 'Saved'}))
        self.poll()
        self.finish()
        self.assertIsNone(self.window.download_job)
        self.assertIsNotNone(self.window.report, self.window.status.get())
        self.assertEqual(Path(self.window.report.source), self.folder)
        self.assertFalse(self.window.busy)
        self.assertTrue(self.window.selector.instate(['readonly']))

    def test_partial_collection_retains_coverage_warning_in_review(self):
        warning = 'Backup log was unavailable; historical coverage is incomplete.'
        (self.folder / 'diagnostic_manifest.json').write_text(json.dumps(
            {'schema_version': 1, 'status': 'partial', 'warnings': [warning], 'files': []}), encoding='utf-8')
        job = self.start_robot()
        job.events.put(('collected', {'path': self.folder, 'warnings': [warning], 'complete': False,
                                      'downloaded_files': 2, 'message': 'Partial'}))
        self.poll()
        self.finish()
        self.assertIsNotNone(self.window.report, self.window.status.get())
        self.assertIn(warning, self.window.report.warnings)
        self.assertIn(warning, self.text())
        self.assertIn('Coverage warnings', self.window.status.get())

    def test_cancelled_collection_preserves_path_without_automatic_analysis(self):
        job = self.start_robot()
        self.window.cancel()
        job.cancel.assert_called_once()
        with patch('diagnostic_view.analyze_directory') as analyze:
            job.events.put(('cancelled', {'path': self.folder, 'warnings': ['Incomplete dataset'],
                    'complete': False, 'downloaded_files': 1, 'message': 'Cancelled; saved files retained.'}))
            self.poll()
            analyze.assert_not_called()
        self.assertFalse(self.window.busy)
        self.assertIsNone(self.window.report)
        self.assertIn(str(self.folder), self.text())
        self.assertIn('Local folder', self.text())
        self.assertTrue((self.folder / 'log_history.txt').exists())

    def test_cancel_racing_with_collected_event_does_not_start_analysis(self):
        job = self.start_robot()
        job.events.put(('collected', {'path': self.folder, 'warnings': [], 'complete': True,
                                      'downloaded_files': 2, 'message': 'Saved'}))
        self.window.cancel()
        with patch('diagnostic_view.analyze_directory') as analyze:
            self.poll()
            analyze.assert_not_called()
        self.assertIsNone(self.window.report)
        self.assertFalse(self.window.busy)

    def test_hidden_page_prompts_for_trust_on_root_without_rejecting_transfer(self):
        from session import HostKeyQuestion
        job = self.start_robot()
        question = HostKeyQuestion('192.0.2.1', 'test-fingerprint')
        job.events.put(('host_key', question))
        job.events.put(('cancelled', {'path': None, 'warnings': [], 'message': 'Cancelled'}))
        self.app.open_home()
        with patch('diagnostic_view.messagebox.askyesno', return_value=True) as prompt:
            self.poll()
            prompt.assert_called_once()
            self.assertIs(prompt.call_args.kwargs['parent'], self.root)
        self.assertTrue(question.answered.is_set())
        self.assertTrue(question.accepted)
        self.assertFalse(self.window.busy)

    def test_cancelled_collection_still_rejects_pending_trust(self):
        from session import HostKeyQuestion
        job = self.start_robot()
        question = HostKeyQuestion('192.0.2.1', 'test-fingerprint')
        job.events.put(('host_key', question))
        self.window.cancel()
        with patch('diagnostic_view.messagebox.askyesno') as prompt:
            self.poll()
            prompt.assert_not_called()
        self.assertTrue(question.answered.is_set())
        self.assertFalse(question.accepted)

    def test_failed_collection_displays_retained_dataset_and_recovers_controls(self):
        job = self.start_robot()
        job.events.put(('error', {'path': self.folder, 'warnings': ['Flight transfer failed'],
                                 'complete': False, 'message': 'Could not save coverage manifest.'}))
        with patch('diagnostic_view.analyze_directory') as analyze:
            self.poll()
            analyze.assert_not_called()
        self.assertIn('Could not save coverage manifest', self.text())
        self.assertIn(str(self.folder), self.text())
        self.assertTrue(self.window.source_button.instate(['!disabled']))
        self.assertTrue(self.window.export_button.instate(['disabled']))

    def test_client_metadata_and_optional_evidence_are_forwarded_to_export(self):
        from robot_diagnostics import DiagnosticReport
        self.window.set_report(DiagnosticReport(source=str(self.folder)))
        self.window.client_name.set(' Example customer ')
        self.window.robot_label.set(' Packaging cell 2 ')
        self.window.prepared_by.set(' Service team ')
        self.window.include_evidence.set(True)
        destination = self.folder/'client-status.pdf'
        with patch('diagnostic_view.filedialog.asksaveasfilename', return_value=str(destination)), patch(
                'diagnostic_pdf.export_diagnostic_pdf', return_value=destination) as export:
            self.window.export()
            self.finish()
        self.assertEqual(export.call_args.kwargs['metadata'],
                         {'client': 'Example customer', 'robot_label': 'Packaging cell 2', 'prepared_by': 'Service team'})
        self.assertTrue(export.call_args.kwargs['include_evidence'])

    def test_source_controls_advanced_and_cancelled_picker_preserve_review(self):
        from robot_diagnostics import DiagnosticReport
        report = DiagnosticReport(source=str(self.folder), summary=['Existing review'])
        self.window.set_report(report)
        self.window.source_mode.set('Robot')
        self.window.change_source()
        self.root.update()
        self.assertTrue(self.window.robot_controls.winfo_ismapped())
        self.assertFalse(self.window.advanced_visible)
        self.window.toggle_advanced()
        self.root.update()
        self.assertTrue(self.window.advanced.winfo_ismapped())
        self.window.ip.set('192.0.2.1')
        self.window.password.set('test-credential')
        with patch('diagnostic_view.filedialog.askdirectory', return_value=''), patch(
                'diagnostic_view.DiagnosticSourceJob') as factory:
            self.window.choose_source()
            factory.assert_not_called()
        self.assertIs(self.window.report, report)
        self.assertFalse(self.window.busy)
        self.window.source_mode.set('Local folder')
        self.window.change_source()
        self.root.update()
        self.assertFalse(self.window.robot_controls.winfo_ismapped())

    def test_optional_log_clock_offset_is_forwarded_to_local_analysis(self):
        from robot_diagnostics import DiagnosticReport
        report = DiagnosticReport(source=str(self.folder), summary=['Clock alignment review'])
        self.window.log_utc_offset.set(' +02:00 ')
        with patch('diagnostic_view.analyze_directory', return_value=report) as analyze:
            self.window.analyze(self.folder)
            self.finish()
        analyze.assert_called_once()
        self.assertEqual(analyze.call_args.args, (self.folder,))
        self.assertEqual(analyze.call_args.kwargs['log_utc_offset'], '+02:00')
        self.assertTrue(callable(analyze.call_args.kwargs['check_cancel']))
        self.assertTrue(callable(analyze.call_args.kwargs['progress']))
        self.assertIs(self.window.report, report)

    def test_cancel_discards_report_already_queued_by_analysis(self):
        from robot_diagnostics import DiagnosticReport
        self.window.set_busy(True)
        self.window.events.put(('progress', 'Still processing'))
        self.window.events.put(('report', DiagnosticReport(source=str(self.folder))))
        self.window.cancel()
        self.poll()
        self.assertIsNone(self.window.report)
        self.assertFalse(self.window.busy)
        self.assertIn('Cancelled', self.window.status.get())
        self.assertTrue(self.window.export_button.instate(['disabled']))


if __name__ == '__main__':
    unittest.main()
