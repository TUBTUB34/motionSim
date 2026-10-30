"""Torque provenance, joint overlays and interactive guide regression checks."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.figure import Figure

from flight_charts import FlightCharts, JOINT_COLORS
from flight_data import load_flight
from tests.test_flight_ui import flight


def recording():
    report = flight()
    for joint in range(6):
        report.columns[f'Target moment j{joint} [Nm]'] = np.arange(5.) + joint * 10
        report.columns[f'Temperature j{joint} [C]'] = np.full(5, 35 + joint)
        report.columns[f'Voltage j{joint} [V]'] = np.full(5, 48.)
        report.columns[f'Control mode j{joint}'] = np.array([1, 1, 2, 2, 2.])
    return report


class JointComparisonTests(unittest.TestCase):
    def test_torque_preserves_target_provenance_and_never_converts_current(self):
        report = recording()
        for joint in range(6):
            np.testing.assert_array_equal(report.series('torque', joint, True), np.arange(5.) + joint * 10)
            self.assertTrue(np.isnan(report.series('torque', joint)).all())
        report.columns['Actual torque j1 [Nm]'] = np.full(5, -3.)
        np.testing.assert_array_equal(report.series('torque', 1), np.full(5, -3.))
        np.testing.assert_array_equal(report.series('torque', 1, True), np.arange(5.) + 10)

    def test_recorded_joint_specs_do_not_fabricate_targets(self):
        report = recording()
        for metric, expected in [('temperature', 37), ('voltage', 48), ('control_mode', 1)]:
            self.assertEqual(report.series(metric, 2)[0], expected)
            self.assertTrue(np.isnan(report.series(metric, 2, True)).all())

    def test_actual_and_target_overlays_share_joint_colors_and_have_distinct_styles(self):
        charts = FlightCharts(Figure())
        charts.draw(recording(), focus='Position', compare_joints=[0, 2, 5])
        lines = charts.axes[0].lines[:-1]
        self.assertEqual(len(lines), 6)
        self.assertEqual([text.get_text() for text in charts.axes[0].get_legend().get_texts()],
                         ['Base', 'Elbow', 'Wrist 3'])
        for index, joint in enumerate((0, 2, 5)):
            actual, target = lines[index*2:index*2+2]
            self.assertEqual(actual.get_color(), JOINT_COLORS[joint])
            self.assertEqual(target.get_color(), actual.get_color())
            self.assertEqual(actual.get_linestyle(), '-')
            self.assertEqual(target.get_linestyle(), '--')
        charts.draw(recording(), focus='Torque', compare_joints=[0, 2, 5], series='Target')
        for line, joint in zip(charts.axes[0].lines[:-1], (0, 2, 5)):
            np.testing.assert_array_equal(line.get_ydata(), np.arange(5.) + joint * 10)
            self.assertIn('Target', line.get_label())
            self.assertEqual(line.get_color(), JOINT_COLORS[joint])

    def test_separate_joint_graphs_preserve_series_scales_and_gaps(self):
        report = recording()
        report.break_before[2] = True
        charts = FlightCharts(Figure())
        charts.draw(report, focus='Torque', compare_joints=[0, 2, 5], series='Target', separate_joints=True, shared_scale=True)
        self.assertEqual(len(charts.axes), 3)
        for axis, joint in zip(charts.axes, (0, 2, 5)):
            self.assertIn(('Base', 'Elbow', 'Wrist 3')[(0, 2, 5).index(joint)], axis.get_title(loc='left'))
            line = axis.lines[0]
            self.assertEqual(line.get_color(), JOINT_COLORS[joint])
            self.assertEqual(line.get_linestyle(), '--')
            self.assertTrue(np.isnan(line.get_ydata()[2]))
            self.assertEqual([text.get_text() for text in axis.get_legend().get_texts()], ['Target'])
            self.assertEqual(axis.get_ylim(), charts.axes[0].get_ylim())
        charts.axes[0].set_xlim(10.01, 10.03)
        self.assertEqual(charts.axes[-1].get_xlim(), (10.01, 10.03))
        charts.draw(report, focus='Torque', compare_joints=[0, 5], series='Target', separate_joints=True)
        self.assertNotEqual(charts.axes[0].get_ylim(), charts.axes[1].get_ylim())
        charts.draw(report, focus='Torque', compare_joints=[0, 2], series='Actual', separate_joints=True)
        self.assertTrue(all('No recorded' in axis.texts[0].get_text() for axis in charts.axes))
        charts.draw(report, focus='Torque', compare_joints=[], separate_joints=True)
        self.assertEqual(charts.axes[0].texts[0].get_text(), 'No joints selected')

    def test_unavailable_actual_torque_and_empty_selection_are_explicit(self):
        charts = FlightCharts(Figure())
        charts.draw(recording(), focus='Torque', compare_joints=range(6), series='Actual')
        self.assertEqual(len(charts.axes[0].lines), 1)  # playhead only
        self.assertIn('No recorded', charts.axes[0].texts[0].get_text())
        charts.draw(recording(), focus='Torque', compare_joints=[])
        self.assertEqual(charts.axes[0].texts[0].get_text(), 'No joints selected')

    def test_torque_gaps_and_discrete_control_mode(self):
        report = recording()
        report.break_before[2] = True
        charts = FlightCharts(Figure())
        charts.draw(report, focus='Torque', compare_joints=[1], series='Target')
        self.assertTrue(np.isnan(charts.axes[0].lines[0].get_ydata()[2]))
        charts.draw(report, focus='Control Mode', compare_joints=[1], series='Actual')
        self.assertEqual(charts.axes[0].lines[0].get_drawstyle(), 'steps-post')

    def test_supplied_recording_exposes_recorded_target_moment_exactly(self):
        paths = sorted((Path(__file__).resolve().parents[1] / 'assets/flightreports').glob('recording*.zip'))
        if not paths:
            self.skipTest('Optional supplied recordings are not bundled')
        report = load_flight(paths[0])
        for joint in range(6):
            np.testing.assert_array_equal(report.series('torque', joint, True), report.columns[f'Target moment j{joint} [Nm]'])
            self.assertTrue(np.isnan(report.series('torque', joint)).all())


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class JointComparisonUITests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from flight_view import FlightWindow
        from ui_theme import apply_theme
        self.root = tk.Tk()
        self.root.withdraw()
        apply_theme(self.root)
        with patch('flight_view.discover_flights', return_value=[]), patch('flight_view.discover_library', return_value=[]):
            self.window = FlightWindow(SimpleNamespace(root=self.root))
            self.window.executor.submit(lambda: None).result(timeout=5)
        self.window.after_cancel(self.window.after_id)
        self.window.set_report(recording())
        self.root.update()

    def tearDown(self):
        self.window.shutdown()
        self.root.destroy()

    def test_mode_switch_keeps_playback_and_torque_readout_matches_all_selected_joints(self):
        w = self.window
        w.seek(.02)
        w.timeline.play()
        w.view_mode.set('Compare joints')
        w.change_mode()
        w.focus.set('Torque')
        w.compare_series.set('Target')
        w.redraw_charts()
        self.assertTrue(w.timeline.playing)
        self.assertEqual(w.timeline.elapsed, .02)
        self.assertEqual(len(w.charts.axes), 1)
        self.assertIn('· Target', w.readout.get())
        self.assertIn('Base — 2 Nm', w.readout.get())
        self.assertIn('Wrist 3 — 52 Nm', w.readout.get())
        w.compare_joints[5].set(False)
        w.redraw_charts()
        self.assertNotIn('Wrist 3', w.readout.get())
        w.focus.set('Acceleration')
        w.compare_series.set('Actual')
        w.redraw_charts()
        self.assertIn('Derived: 100 rad/s²', w.readout.get())
        w.view_mode.set('One joint')
        w.change_mode()
        self.assertEqual(w.focus.get(), 'Position')
        self.assertEqual(len(w.charts.axes), 1)
        self.assertTrue(w.timeline.playing)

    def test_separate_and_combined_layout_keep_selection_and_playhead(self):
        w = self.window
        w.view_mode.set('Compare joints')
        w.change_mode()
        w.seek(.02)
        w.timeline.play()
        w.compare_layout.set('Separate joints')
        w.redraw_charts()
        self.root.update()
        self.assertEqual(len(w.charts.axes), 6)
        self.assertTrue(w.timeline.playing)
        self.assertAlmostEqual(w.timeline.elapsed, .02)
        self.assertTrue(all(line.get_xdata()[0] == 10.02 for line in w.charts.playheads))
        w.compare_joints[2].set(False)
        w.redraw_charts()
        self.assertEqual(len(w.charts.axes), 5)
        w.compare_layout.set('Combined')
        w.redraw_charts()
        self.assertEqual(len(w.charts.axes), 1)
        self.assertFalse(w.compare_joints[2].get())
        w.view_mode.set('One joint')
        w.change_mode()
        self.assertTrue(w.layout_combo.instate(['disabled']))

    def test_all_readings_scroll_and_compact_controls_stay_accessible(self):
        w = self.window
        w.focus.set('All readings')
        w.redraw_charts()
        w.geometry('1000x720')
        self.root.update()
        self.assertGreater(w.canvas.get_tk_widget().winfo_height(), w.chart_viewport.winfo_height())
        self.assertLess(w.chart_viewport.yview()[1], 1)
        w.chart_viewport.yview_moveto(1)
        self.assertAlmostEqual(w.chart_viewport.yview()[1], 1)
        for mode in ('One joint', 'Compare joints'):
            w.view_mode.set(mode)
            w.change_mode()
            self.root.update()
            label = w.charts.axes[-1].xaxis.label.get_window_extent(w.canvas.get_renderer())
            self.assertGreaterEqual(label.y0, 0)
            for control in (w.mode_combo, w.focus_combo, w.series_combo, w.layout_combo, w.scale_combo, w.play_button, w.scrubber, w.export_button):
                with self.subTest(mode=mode, control=control):
                    self.assertLessEqual(control.winfo_rootx()+control.winfo_width(), w.winfo_rootx()+w.winfo_width())
                    self.assertLessEqual(control.winfo_rooty()+control.winfo_height(), w.winfo_rooty()+w.winfo_height())


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires a graphical Tk session')
class HelpUITests(unittest.TestCase):
    def test_search_topics_empty_state_and_navigation_lifecycle(self):
        import tkinter as tk
        from main import SimulatorApp
        from help_content import TOPICS
        root = tk.Tk()
        app = SimulatorApp(root)
        try:
            app.open_help('Compare joints')
            guide = app.help_window
            self.assertIn('Target', guide.text.get('1.0', 'end'))
            guide.query.set('torque')
            self.assertIn('Flight reports', guide.topics.get_children())
            guide.query.set('zz-no-match-zz')
            self.assertEqual(guide.topic.get(), 'No matching topics')
            guide.show_topic('Backups')
            self.assertIn('/programs', guide.text.get('1.0', 'end'))
            app.show_setup()
            self.assertTrue(guide.winfo_exists())
            guide.geometry('700x520')
            root.update()
            self.assertLessEqual(guide.text.winfo_rootx()+guide.text.winfo_width(), guide.winfo_rootx()+guide.winfo_width())
            for topic in TOPICS:
                guide.show_topic(topic)
                self.assertEqual(guide.topic.get(), topic)
                self.assertGreater(len(guide.text.get('1.0', 'end')), 100)
        finally:
            app.close()
