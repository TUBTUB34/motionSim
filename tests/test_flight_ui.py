"""Deterministic graph tests; native UI checks opt in with MOTIONSIM_UI_TESTS=1."""
import os
import queue
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from flight_charts import FlightCharts, gap_series, script_line_at
from flight_data import FlightReport
from flight_motion import sample_at


def flight(name='example.csv', offset=0.):
    times = np.array([10., 10.01, 10.02, 10.03, 10.04])
    columns = {}
    for joint in range(6):
        columns[f'Actual position j{joint} [rad]'] = np.linspace(0., .01, 5) + offset
        columns[f'Target position j{joint} [rad]'] = np.linspace(0., .01, 5)
        columns[f'Actual velocity j{joint} [rad/s]'] = np.arange(5.)
        columns[f'Actual current j{joint} [A]'] = np.arange(5.) / 10
    return FlightReport(Path('/tmp')/name, name, {}, times, times-times[0], columns,
                        model='UR10e', break_before=np.zeros(5, dtype=bool))


class FlightChartTests(unittest.TestCase):
    def test_fine_wheel_deltas_accumulate_without_large_jumps(self):
        from flight_view import FlightWindow
        window = SimpleNamespace(tk=Mock(), scroll_chart=Mock())
        window.tk.call.return_value = 'win32'
        for _ in range(3):
            FlightWindow.wheel_chart(window, SimpleNamespace(delta=1))
        window.scroll_chart.assert_called_once_with('scroll', -1, 'units')
        window.tk.call.return_value = 'aqua'
        window._wheel_remainder = 0
        FlightWindow.wheel_chart(window, SimpleNamespace(delta=-1))
        window.scroll_chart.assert_called_with('scroll', 3, 'units')

    def test_line_axis_preserves_chronology_and_repeated_lines(self):
        report = flight()
        report.columns['Script control line'] = np.array([20., 21., 20., 80., np.nan])
        charts = FlightCharts(Figure())
        charts.draw(report, focus='Position')
        axis = charts.axes[0]
        np.testing.assert_array_equal(axis.lines[0].get_xdata(), report.timestamps)
        formatter = axis.xaxis.get_major_formatter()
        self.assertEqual([formatter(x) for x in report.timestamps], ['20', '21', '20', '80', '—'])
        self.assertIn('Script line number', axis.get_xlabel())
        report.break_before[2] = True
        self.assertIsNone(script_line_at(report, 10.015))
        self.assertEqual(script_line_at(report, 10.02), 20)
        self.assertIsNone(script_line_at(report, 9.))
        report.columns['Script control line'][0] = 0
        self.assertIsNone(script_line_at(report, 10.))

    def test_discovery_is_scoped_and_excludes_unfinished_files(self):
        from flight_view import FlightWindow
        from tests.test_flight_transfer import recording
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'report.zip').write_bytes(recording())
            (folder / 'measurements.csv').write_text('Timestamp [s]\n1\n2\n')
            (folder / '.flight-one.part.zip').write_bytes(recording())
            (folder / 'incomplete.part.zip').write_bytes(recording())
            (folder / 'robot_configuration.zip').write_bytes(b'not a flight')
            window = SimpleNamespace(events=queue.Queue())
            FlightWindow._discover(window, folder, 7)
            kind, generation, paths = window.events.get_nowait()
            self.assertEqual((kind, generation), ('discovered', 7))
            self.assertEqual({p.name for p in paths}, {'report.zip', 'measurements.csv'})

    def test_record_breaks_preserve_both_measured_endpoints(self):
        report = flight()
        report.break_before[2] = True
        x, values = gap_series(report, report.series('position', 1))
        self.assertTrue(np.isnan(x[2]))
        self.assertTrue(np.isnan(values[2]))
        np.testing.assert_array_equal(x[np.isfinite(x)], report.timestamps)
        np.testing.assert_array_equal(values[np.isfinite(values)], report.series('position', 1))

    def test_shared_axes_playhead_and_acceleration_provenance(self):
        report = flight()
        figure = Figure()
        canvas = FigureCanvasAgg(figure)
        charts = FlightCharts(figure)
        charts.draw(report)
        canvas.draw()
        charts.capture_background(canvas)
        charts.move(10.025, canvas)
        self.assertEqual(len(charts.axes), 8)
        for line in charts.playheads:
            self.assertEqual(list(line.get_xdata()), [10.025, 10.025])
        self.assertEqual(charts.axes[2].lines[0].get_label(), 'Actual (derived dV/dt)')
        charts.axes[0].set_xlim(10.01, 10.03)
        self.assertEqual(charts.axes[3].get_xlim(), (10.01, 10.03))
        report.columns['Actual acceleration j1 [rad/s^2]'] = np.ones(5)
        charts.draw(report, focus='Acceleration')
        self.assertEqual(charts.axes[0].lines[0].get_label(), 'Actual')

    def test_protective_stop_shading_does_not_bridge_missing_record(self):
        report = flight()
        report.columns['Safety mode'] = np.full(5, 3.)
        report.break_before[2] = True
        charts = FlightCharts(Figure())
        charts.draw(report, focus='Position')
        self.assertEqual(len(charts.axes[0].patches), 2)


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires local graphical display')
class FlightWindowTests(unittest.TestCase):
    def setUp(self):
        import tkinter as tk
        from flight_view import FlightWindow
        self.root = tk.Tk()
        self.root.withdraw()
        with patch('flight_view.discover_flights', return_value=[]), patch('flight_view.discover_library', return_value=[]):
            self.window = FlightWindow(SimpleNamespace(root=self.root))
            self.window.future = self.window.executor.submit(lambda: None)
            self.window.future.result(timeout=5)
        self.root.update()
        self.window.after_cancel(self.window.after_id)
        self.window.poll()

    def tearDown(self):
        self.window.shutdown()
        self.root.destroy()

    def test_scrolling_preserves_image_and_fixed_script_line_context(self):
        report = flight()
        report.columns['Script control line'] = np.array([20., 21., 20., 80., np.nan])
        window = self.window
        window.set_report(report)
        window.focus.set('All readings')
        window.redraw_charts()
        window.geometry('1000x720')
        self.root.update()
        axis_y = window.line_axis.winfo_rooty()
        photo = window.canvas._tkphoto
        with patch.object(window.canvas, 'draw', wraps=window.canvas.draw) as draw:
            for _ in range(4):
                window.wheel_chart(SimpleNamespace(delta=-120))
            self.root.update()
            self.assertEqual(draw.call_count, 0)
        self.assertIs(window.canvas._tkphoto, photo)
        self.assertGreater(window.chart_viewport.yview()[0], 0)
        self.assertEqual(window.line_axis.winfo_rooty(), axis_y)
        self.assertLess(axis_y + window.line_axis.winfo_height(), window.winfo_rooty()+window.winfo_height())
        self.assertEqual(window.canvas.get_tk_widget().cget('background'), '#090e14')
        window.seek(.03)
        self.assertEqual(window.script_position.get(), 'Script line 80')
        window.seek(.04)
        self.assertEqual(window.script_position.get(), 'Script line unavailable')

    def test_folder_switch_replaces_listing_and_ignores_stale_events(self):
        window = self.window
        window.set_report(flight())
        window.timeline.play()
        previous_generation = window.generation
        previous_discovery = window.discovery_generation
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            expected = folder / 'new.csv'
            expected.write_text('Timestamp [s],Actual position j0 [rad]\n1,0\n2,1\n')
            window.set_library(folder)
            window.reset_zoom()  # Empty workspace must not keep old chart axes.
            window.executor.submit(lambda: None).result(timeout=5)
            window.events.put(('discovered', previous_discovery, [Path('/tmp/old.csv')]))
            window.events.put(('loaded', previous_generation, (), flight('stale.csv')))
            with patch.object(window, 'load_path') as load:
                window.after_cancel(window.after_id)
                window.poll()
            self.assertIsNone(window.report)
            self.assertIsNone(window.timeline)
            self.assertEqual(window.paths, [expected])
            load.assert_called_once_with(expected)
            self.assertNotIn('stale.csv', window.flight.get())

    def test_completed_download_invalidates_scan_without_clearing_active_report(self):
        window = self.window
        with tempfile.TemporaryDirectory() as directory:
            window.set_library(directory)
            window.executor.submit(lambda: None).result(timeout=5)
            window.after_cancel(window.after_id)
            window.poll()
            window.set_report(flight())
            window.timeline.play()
            report, timeline = window.report, window.timeline
            old_generation = window.discovery_generation
            path = Path(directory) / 'downloaded.csv'
            path.write_text('Timestamp [s],Actual position j0 [rad]\n1,0\n2,1\n')
            window.add_download(path)
            self.assertIn('1 report in', window.library_summary.get())
            window.events.put(('discovered', old_generation, []))
            window.executor.submit(lambda: None).result(timeout=5)
            window.after_cancel(window.after_id)
            # The fixture lasts 40 ms; disk/plotting time must not make this
            # discovery-state test finish playback before its assertions.
            with patch('flight_view.time.monotonic', return_value=window._last_tick):
                window.poll()
            self.assertEqual(window.paths, [path])
            self.assertIn('1 report in', window.library_summary.get())
            self.assertIs(window.report, report)
            self.assertIs(window.timeline, timeline)
            self.assertTrue(timeline.playing)
            self.assertIs(window.charts.report, report)

    def test_empty_scan_does_not_blank_an_open_external_recording(self):
        window = self.window
        window.set_report(flight())
        report = window.report
        window.events.put(('discovered', window.discovery_generation, []))
        window.after_cancel(window.after_id)
        window.poll()
        self.assertIs(window.report, report)
        self.assertIs(window.charts.report, report)
        self.assertTrue(window.charts.axes)

    def test_report_switch_resets_every_playback_context(self):
        window = self.window
        window.set_report(flight())
        window.seek(.025)
        window.timeline.speed = 4.
        window.timeline.play()
        window.set_report(flight('second.csv', offset=.5))
        self.assertEqual(window.flight.get(), 'second.csv')
        self.assertEqual(window.timeline.elapsed, 0.)
        self.assertFalse(window.timeline.playing)
        self.assertEqual(window.timeline.speed, 1.)
        self.assertAlmostEqual(sample_at(window.report, window.timeline.elapsed).joints[0], .5)
        self.assertIs(window.charts.report, window.report)

    def test_programmatic_scrub_updates_do_not_pause_playback(self):
        window = self.window
        window.set_report(flight())
        window.timeline.play()
        window.timeline.tick(.015)
        window.update_time(window.timeline.elapsed)
        self.root.update_idletasks()
        self.assertTrue(window.timeline.playing)
        window.scrub('0.030')
        self.assertFalse(window.timeline.playing)
        self.assertAlmostEqual(window.timeline.elapsed, .03)

    def test_graph_click_and_hide_keep_timeline_consistent(self):
        window = self.window
        window.set_report(flight())
        window.graph_click(SimpleNamespace(inaxes=window.charts.axes[0], xdata=10.02, button=1))
        self.assertAlmostEqual(window.timeline.elapsed, .02)
        self.assertAlmostEqual(sample_at(window.report, window.timeline.elapsed).joints[0], .005)
        window.toggle_play()
        window.hide()
        self.assertFalse(window.timeline.playing)
        window.deiconify()
        self.root.update_idletasks()
        self.assertAlmostEqual(window.timeline.elapsed, .02)

    def test_stale_and_failed_load_keep_current_report(self):
        window = self.window
        current = flight()
        window.set_report(current)
        window.generation = 3
        window.events.put(('loaded', 2, ('old', 0, 0), flight('stale.csv')))
        window.events.put(('load_error', 3, 'Malformed timestamp'))
        window.after_cancel(window.after_id)
        window.poll()
        self.assertIs(window.report, current)
        self.assertEqual(window.flight.get(), current.name)
        self.assertIn('Previous flight retained', window.status.get())

    def test_export_uses_selected_report_and_complete_scope(self):
        window = self.window
        selected = flight()
        window.set_report(selected)
        window.charts.axes[0].set_xlim(10.01, 10.02)
        window.seek(.02)
        with patch('flight_view.filedialog.asksaveasfilename', return_value='/tmp/flight-ui-mocked.pdf'), \
                patch('flight_pdf.export_flight_pdf') as export:
            window.export_pdf()
            window.executor.submit(lambda: None).result(timeout=10)
            export.assert_called_once_with(selected, '/tmp/flight-ui-mocked.pdf')
            self.assertEqual(len(export.call_args.args[0].time), 5)

    def test_minimum_window_keeps_controls_and_graph_inside(self):
        window = self.window
        window.set_report(flight())
        window.geometry('1000x720')
        self.root.update_idletasks()
        for widget in (window.flight_combo, window.play_button, window.scrubber,
                       window.export_button, window.chart_viewport, window.status_label):
            with self.subTest(widget=widget):
                self.assertGreaterEqual(widget.winfo_rootx(), window.winfo_rootx())
                self.assertGreaterEqual(widget.winfo_rooty(), window.winfo_rooty())
                self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(),
                                     window.winfo_rootx() + window.winfo_width())
                self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(),
                                     window.winfo_rooty() + window.winfo_height())

    def test_resize_keeps_rendered_timestamp_label_inside_canvas(self):
        window = self.window
        window.set_report(flight())
        window.geometry('1955x1053')
        self.root.update()
        window.geometry('1000x720')
        self.root.update()
        renderer = window.canvas.get_renderer()
        bounds = window.charts.axes[-1].xaxis.label.get_window_extent(renderer)
        self.assertGreaterEqual(bounds.y0, 0.)
        self.assertLessEqual(bounds.y1, window.figure.bbox.height)

    def test_subplots_toolbar_adjustments_survive_draw_at_same_size(self):
        window = self.window
        window.set_report(flight())
        self.root.update()
        window.figure.subplots_adjust(bottom=.22, top=.84, left=.16)
        window.canvas.draw()
        self.assertAlmostEqual(window.figure.subplotpars.bottom, .22)
        self.assertAlmostEqual(window.figure.subplotpars.top, .84)
        self.assertAlmostEqual(window.figure.subplotpars.left, .16)


if __name__ == '__main__':
    unittest.main()
