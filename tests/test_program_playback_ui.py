import os
import gc
import queue
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import tempfile

from tests.test_flight_ui import flight


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class ProgramPlaybackUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from main import SimulatorApp
        self.root = tk.Tk()
        self.app = SimulatorApp(self.root)
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.discovery = patch('flight_view.FlightWindow._discover')
        self.discovery.start()
        self.addCleanup(self.discovery.stop)

    def tearDown(self):
        self.app.close()
        # Each test owns a separate Tcl interpreter. Collect closed widget cycles
        # on the UI thread before another test starts background PDF work.
        del self.app
        del self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def source(self):
        self.app.open_flights()
        window = self.app.flight_window
        window.set_report(flight())
        return window

    def finish_program_work(self, window):
        window.executor.submit(lambda: None).result(timeout=30)
        window.after_cancel(window.after_id)
        window.poll()

    def test_main_playback_seek_switch_pause_and_missing_model(self):
        source = self.source()
        source.show_simulator()
        self.root.update()
        panel = self.app.recorded_panel
        self.assertEqual(source.state(), 'withdrawn')
        source.seek(.02)
        self.assertAlmostEqual(panel.robot.sample.joints[0], .005)
        source.toggle_play()
        self.assertEqual(panel.play.cget('text'), 'Pause')
        source.toggle_play()
        self.assertEqual(panel.play.cget('text'), 'Play')
        source.set_report(flight('other.csv', .5))
        self.assertAlmostEqual(panel.robot.sample.joints[0], .5)
        missing = flight('unknown.csv')
        missing.model = None
        source.set_report(missing)
        self.assertIsNone(panel.robot)
        self.assertIn('model unavailable', panel.note.get())
        source.toggle_play()
        self.app.show_setup()
        self.assertFalse(source.timeline.playing)
        self.assertIsNone(self.app.recorded_source)

    def test_tcp_mounting_default_switch_and_reference_persistence(self):
        source = self.source()
        source.show_simulator()
        panel = self.app.recorded_panel
        self.root.geometry('980x620')
        self.root.update()
        self.assertEqual(panel.mounting.get(), 'TCP mounting')
        self.assertEqual(panel.robot.view_frame, 'Live TCP')
        reference = panel.robot.tcp_view_reference
        self.assertIsNotNone(reference)
        source.seek(.03)
        self.assertEqual(panel.robot.tcp_view_reference, reference)
        panel.mounting.set('Robot base')
        panel.change_mounting()
        self.assertEqual(panel.robot.view_frame, 'Robot base')
        source.set_report(flight('other.csv', .5))
        self.assertEqual(panel.robot.view_frame, 'Robot base')
        panel.mounting.set('TCP mounting')
        panel.change_mounting()
        self.assertEqual(panel.robot.tcp_view_reference, panel.robot.sample.tcp_pose)
        self.assertEqual(source.timeline.elapsed, 0.)
        self.assertLessEqual(panel.mounting_selector.winfo_rootx()+panel.mounting_selector.winfo_width(), self.root.winfo_rootx()+self.root.winfo_width())

    def test_live_samples_cannot_overwrite_recorded_robot(self):
        from connection import make_settings
        from rtde_client import RobotSample
        self.app.settings = make_settings('127.0.0.1', '', '', '')
        self.app.show_view()
        live = RobotSample(1, (.7, -1, 1, 0, 0, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic())
        self.app.session = SimpleNamespace(events=queue.Queue(), sample=lambda: live, stop=lambda: None)
        source = self.source()
        source.show_simulator()
        source.seek(.02)
        self.root.after_cancel(self.app.poll_id)
        self.app.poll()
        self.assertIs(self.app.viewer.sample, live)
        self.assertAlmostEqual(self.app.recorded_panel.robot.sample.joints[0], .005)
        self.assertEqual(self.app.tabs.select(), str(self.app.simulator_tab))
        self.assertEqual(self.app.simulator_source.get(), 'Flight report')
        source.report.break_before[3] = True
        source.seek(.025)
        self.assertIsNone(self.app.recorded_panel.robot.sample)
        self.assertIn('No valid recorded motion', self.app.recorded_panel.note.get())

    def test_simulator_source_switch_preserves_both_views_and_pauses_flight(self):
        from connection import make_settings
        from rtde_client import RobotSample
        self.app.settings = make_settings('127.0.0.1', '', '', '')
        self.app.show_view()
        source = self.source()
        source.show_simulator()
        panel = self.app.recorded_panel
        source.seek(.02)
        source.toggle_play()
        self.app.simulator_source.set('Live robot')
        self.app.select_simulator_source()
        self.root.update()
        self.assertFalse(source.timeline.playing)
        self.assertTrue(self.app.viewer.winfo_ismapped())
        self.assertFalse(panel.winfo_ismapped())
        self.assertAlmostEqual(source.timeline.elapsed, .02)
        self.app.simulator_source.set('Flight report')
        self.app.select_simulator_source()
        self.root.update()
        self.assertIs(self.app.recorded_panel, panel)
        self.assertFalse(self.app.viewer.winfo_ismapped())
        self.assertTrue(panel.winfo_ismapped())
        self.assertEqual(len(self.app.tabs.tabs()), 3)
        self.assertAlmostEqual(source.timeline.elapsed, .02)
        self.assertTrue(panel.script_position_label.winfo_ismapped())

    def test_offline_source_selector_explains_missing_live_connection(self):
        source = self.source()
        source.show_simulator()
        self.app.simulator_source.set('Live robot')
        self.app.select_simulator_source()
        self.root.update()
        self.assertIsNone(self.app.session)
        self.assertTrue(self.app.live_simulator.winfo_ismapped())
        self.assertFalse(self.app.recorded_panel.winfo_ismapped())
        self.app.simulator_source.set('Flight report')
        self.app.select_simulator_source()
        self.root.update()
        self.assertIs(self.app.recorded_source, source)
        self.assertTrue(self.app.recorded_panel.winfo_ismapped())

    def test_program_selection_review_export_and_invalidation(self):
        from program_check import discover_programs, CATEGORIES
        self.app.open_program_check()
        window = self.app.program_check_window
        window.executor.submit(lambda: None).result(timeout=10)
        self.root.update()
        self.assertFalse(window.advanced.winfo_ismapped())
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'test.urp').write_text('<URProgram name="test" lastSavedIn="5.24"/>')
            (base/'test.script').write_text('movel(p, a=3)')
            window.scan(base)
            window.executor.submit(lambda: None).result(timeout=10)
            window.after_cancel(window.after_id)
            window.poll()
            self.assertIsNone(window.review)
            self.assertEqual(window.combo.current(), -1)
            window.limit_vars['tcp_velocity'].set('nan')
            window.combo.current(0)
            window.select_result()
            self.assertIsNone(window.review)
            self.assertFalse(window.run_button.instate(['disabled']))
            self.assertTrue(window.limit_entries['tcp_velocity'].instate(['invalid']))
            window.limit_vars['tcp_velocity'].set('')
            window.run()
            self.finish_program_work(window)
            self.assertIsNotNone(window.review)
            self.assertEqual(len(window.tree.get_children()), len(CATEGORIES))
            window.tree.selection_set('finding4')
            window.show_finding()
            self.assertIn('universal-robots.com', window.details.get('1.0', 'end'))
            full_count = len(window.review.findings)
            window.finding_filter.set('Review recommended')
            window.filter_findings()
            self.assertTrue(window.tree.get_children())
            for category in window.tree.get_children():
                for item in window.tree.get_children(category):
                    self.assertEqual(window.tree.item(item, 'values')[0], 'Review recommended')
            self.assertEqual(len(window.review.findings), full_count)
            with patch('program_check_view.filedialog.asksaveasfilename', return_value=str(base/'report.pdf')):
                window.export()
                window.executor.submit(lambda: None).result(timeout=20)
                window.after_cancel(window.after_id)
                window.poll()
            self.assertTrue((base/'report.pdf').read_bytes().startswith(b'%PDF'))
            window.limit_vars['tcp_velocity'].set('nan')
            self.assertIsNone(window.review)
            window.run()
            self.assertTrue(window.limit_entries['tcp_velocity'].instate(['invalid']))
            self.assertIn('positive, finite', window.status.get())
            window.limit_vars['tcp_velocity'].set('0.1')
            window.limit_vars['tcp_acceleration'].set('2')
            window.run()
            window.executor.submit(lambda: None).result(timeout=10)
            window.after_cancel(window.after_id)
            window.poll()
            self.assertTrue(any('Exceeds user-supplied' in f.detail for f in window.review.findings))
            window.profile.set('Other / unconfirmed')
            window.invalidate()
            self.assertIsNone(window.review)
            self.assertTrue(window.export_button.instate(['disabled']))
        window.geometry('900x650')
        if not window.advanced_visible:
            window.toggle_advanced()
        self.root.update()
        for widget in (window.run_button, window.export_button, window.combo, window.details, *window.limit_entries.values()):
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), window.winfo_rootx()+window.winfo_width())
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), window.winfo_rooty()+window.winfo_height())

    def test_folder_lists_urps_and_reviews_only_selected_with_missing_installation_warning(self):
        self.app.open_program_check()
        window = self.app.program_check_window
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base/'good.urp').write_text('<URProgram lastSavedIn="5.24" installation="default"/>')
            (base/'good.script').write_text('movel(p,a=3)')
            (base/'other.script').write_text('movej(q)')
            (base/'broken.urp').write_text('broken XML')
            subfolder = base/'subfolder'
            subfolder.mkdir()
            (subfolder/'good.urp').write_text('<URProgram lastSavedIn="5.24" installation="missing"/>')
            (subfolder/'good.script').write_text('movej(q,a=1)')
            (base/'default.installation').write_text('<Installation/>')
            window.scan(base)
            window.executor.submit(lambda: None).result(timeout=10)
            window.after_cancel(window.after_id)
            window.poll()
            self.assertEqual(len(window.pairs), 3)
            self.assertTrue(all(r is None for r in window.reviews))
            self.assertIsNone(window.review)
            self.assertTrue(window.run_button.instate(['disabled']))
            self.assertNotEqual(window.combo['values'][1], window.combo['values'][2])
            broken = next(i for i,pair in enumerate(window.pairs) if pair.name == 'broken')
            window.combo.current(broken)
            window.select_result()
            self.finish_program_work(window)
            self.assertIn('failed', window.status.get().lower())
            good = next(i for i,pair in enumerate(window.pairs) if pair.urp.path.parent == base and pair.name == 'good')
            window.combo.current(good)
            window.select_result()
            self.finish_program_work(window)
            self.assertEqual(window.review.counts['movel'], 1)
            self.assertIn('default.installation', window.review.metadata['reviewInstallation'])
            self.assertEqual(window.warning.get(), '')
            missing = next(i for i,pair in enumerate(window.pairs) if pair.urp.path.parent == subfolder)
            window.combo.current(missing)
            window.select_result()
            self.finish_program_work(window)
            self.assertEqual(window.review.counts['movej'], 1)
            self.assertNotIn('reviewInstallation', window.review.metadata)
            self.assertIn('without installation', window.warning.get())
            self.assertFalse(any('Saved installation' in f.detail for f in window.review.findings))
            self.assertFalse(window.advanced_visible)
            self.assertTrue(window.cancel_button.instate(['disabled']))
            with patch('program_check_view.filedialog.asksaveasfilename', return_value=str(base/'selected.pdf')):
                window.export()
                window.executor.submit(lambda: None).result(timeout=30)
                window.after_cancel(window.after_id)
                window.poll()
            self.assertEqual(len(list(base.glob('*.pdf'))), 1)
            self.assertTrue(all(p.read_bytes().startswith(b'%PDF') for p in base.glob('*.pdf')))

    def test_backup_archive_picker_reviews_program_and_matches_installation(self):
        import io
        import tarfile
        self.app.open_program_check()
        window = self.app.program_check_window
        with tempfile.TemporaryDirectory() as temp:
            archive_path = Path(temp) / 'full-backup.tar'
            with tarfile.open(archive_path, 'w') as archive:
                for name, data in (
                    ('/programs/app/demo.urp', b'<URProgram lastSavedIn="5.24" installation="../default"/>'),
                    ('/programs/app/demo.script', b'movel(p,a=3)'),
                    ('/programs/default.installation', b'<Installation/>'),
                ):
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, io.BytesIO(data))
            with patch('program_check_view.filedialog.askopenfilename', return_value=str(archive_path)):
                window.choose_zip()
            window.executor.submit(lambda: None).result(timeout=10)
            window.after_cancel(window.after_id)
            window.poll()
            self.assertEqual(len(window.reviews), 1)
            self.assertIsNone(window.review)
            window.combo.current(0)
            window.select_result()
            self.finish_program_work(window)
            self.assertIsNotNone(window.review)
            self.assertIn('/programs/default.installation', window.review.metadata['reviewInstallation'])
            self.assertTrue(any(f.status == 'Review recommended' for f in window.review.findings))
            self.assertFalse(window.advanced_visible)
            self.assertFalse(window.export_button.instate(['disabled']))

    def test_robot_snapshot_flows_to_urp_selection_and_report_without_live_telemetry(self):
        from contextlib import nullcontext
        from unittest.mock import MagicMock
        from tests.test_program_transfer import FakeSFTP
        from program_transfer import ProgramSourceJob
        self.app.open_program_check()
        window = self.app.program_check_window
        window.source_mode.set('Robot')
        window.change_source()
        window.robot_ip.set('127.0.0.1')
        window.robot_username.set('root')
        window.robot_password.set('test-password')
        with tempfile.TemporaryDirectory() as folder:
            sftp = FakeSFTP({
                '/programs/app/demo.urp': b'<URProgram lastSavedIn="5.24" installation="../tool"/>',
                '/programs/app/demo.script': b'movel(pose,a=3)',
                '/programs/tool.installation': b'<Installation/>',
            })
            client = MagicMock()
            client.open_sftp.return_value = nullcontext(sftp)
            created = []
            def factory(**kwargs):
                job = ProgramSourceJob(**kwargs, client_factory=lambda: client,
                                       trust_path=Path(folder)/'known_hosts')
                created.append(job)
                return job
            with patch('program_transfer.ProgramSourceJob', side_effect=factory), patch(
                    'program_check_view.filedialog.askdirectory', return_value=folder):
                window.pull_programs()
            self.assertTrue(window.source_selector.instate(['disabled']))
            self.assertTrue(window.cancel_button.instate(['!disabled']))
            created[0].thread.join(timeout=10)
            self.assertFalse(created[0].thread.is_alive())
            window.after_cancel(window.after_id)
            window.poll()
            self.finish_program_work(window)
            self.assertEqual(len(window.pairs), 1)
            self.assertIsNone(window.review)
            self.assertEqual(window.combo.current(), -1)
            self.assertTrue(Path(window.working_folder.get()).is_dir())
            window.combo.current(0)
            window.select_result()
            self.finish_program_work(window)
            self.assertEqual(window.review.counts['movel'], 1)
            self.assertTrue(window.review.metadata['reviewInstallation'].endswith('tool.installation'))
            self.assertEqual(window.warning.get(), '')
            self.assertFalse(window.export_button.instate(['disabled']))
            client.exec_command.assert_not_called()
            self.assertIsNone(self.app.session)

    def test_robot_source_cancelled_destination_and_host_trust_do_not_start_unapproved_work(self):
        from session import HostKeyQuestion
        self.app.open_program_check()
        window = self.app.program_check_window
        window.robot_ip.set('127.0.0.1')
        window.robot_username.set('root')
        window.robot_password.set('test-password')
        with patch('program_check_view.filedialog.askdirectory', return_value=''), patch('program_transfer.ProgramSourceJob') as factory:
            window.pull_programs()
            factory.assert_not_called()
        question = HostKeyQuestion('127.0.0.1', 'test-fingerprint')
        events = queue.Queue()
        events.put(('host_key', question))
        events.put(('error', 'SSH host was not trusted'))
        window.download_job = SimpleNamespace(events=events)
        with patch('program_check_view.messagebox.askyesno', return_value=False):
            window.after_cancel(window.after_id)
            window.poll()
        self.assertTrue(question.answered.is_set())
        self.assertFalse(question.accepted)
        self.assertIsNone(window.download_job)
        self.assertFalse(window.busy)
        self.assertIn('not trusted', window.status.get())

if __name__ == '__main__':
    unittest.main()
