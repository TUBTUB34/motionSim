"""Theme changes repaint evidence in place without changing its view or data."""
import gc
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.colors import to_rgba
from matplotlib.figure import Figure

import ui_theme as theme
from flight_charts import FlightCharts, LIGHT_JOINT_COLORS
from tests.test_flight_ui import flight


def palette(mode):
    values = dict(theme.PALETTES[mode])
    values.update(MODE=mode, TEXT=values['INK'], ACTUAL=values['ACCENT'], SUCCESS=values['TARGET'])
    values['CHART_SERIES'] = tuple(values[name] for name in
                                  ('ACCENT', 'TARGET', 'WARNING', 'SERIES_PURPLE', 'DANGER', 'SERIES_TEAL'))
    return patch.multiple(theme, **values)


class ChartThemeTests(unittest.TestCase):
    def test_palette_cycle_preserves_joint_traces_limits_and_playhead(self):
        with palette('dark'):
            charts = FlightCharts(Figure())
            charts.draw(flight(), focus='Position', compare_joints=(0, 2), series='Actual + target')
            canvas = FigureCanvasAgg(charts.figure)
            canvas.draw()
            charts.move(10.02, canvas)
            axis = charts.axes[0]
            axis.set_xlim(10.01, 10.03)
            axis.set_ylim(-.1, .2)
            artists = tuple(axis.lines)
            values = [line.get_ydata().copy() if isinstance(line.get_ydata(), np.ndarray)
                      else list(line.get_ydata()) for line in artists]
        for mode in ('light', 'dark', 'light'):
            with palette(mode):
                charts.refresh_theme()
                self.assertIs(charts.axes[0], axis)
                self.assertEqual(tuple(axis.lines), artists)
                self.assertEqual(axis.get_xlim(), (10.01, 10.03))
                self.assertEqual(axis.get_ylim(), (-.1, .2))
                self.assertEqual(charts.selected_time, 10.02)
                self.assertIsNone(charts.backgrounds)
                self.assertEqual(charts.figure.get_facecolor(), to_rgba(theme.BACKGROUND))
                for artist, expected in zip(artists, values):
                    np.testing.assert_equal(artist.get_ydata(), expected)
                self.assertEqual(artists[0].get_linestyle(), '-')
                self.assertEqual(artists[1].get_linestyle(), '--')
                if mode == 'light':
                    self.assertEqual(to_rgba(artists[0].get_color()), to_rgba(LIGHT_JOINT_COLORS[0]))
                    self.assertEqual(to_rgba(artists[2].get_color()), to_rgba(LIGHT_JOINT_COLORS[2]))

    def test_new_light_chart_uses_legible_semantic_series_colors(self):
        with palette('light'):
            charts = FlightCharts(Figure())
            charts.draw(flight(), focus='Position')
            axis = charts.axes[0]
            self.assertEqual(axis.get_facecolor(), to_rgba(theme.BACKGROUND))
            self.assertEqual(axis.lines[0].get_color(), theme.ACTUAL)
            self.assertEqual(axis.lines[1].get_color(), theme.TARGET)
            self.assertEqual(to_rgba(axis.get_legend().get_texts()[0].get_color()), to_rgba(theme.INK))


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class SurfaceThemeUITests(unittest.TestCase):
    def test_live_palette_switch_preserves_recording_log_zoom_and_robot_camera(self):
        import tkinter as tk
        from connection import make_settings
        from log_data import load_log
        from main import SimulatorApp
        from rtde_client import RobotSample
        with tempfile.TemporaryDirectory() as temp, patch('flight_view.FlightWindow._discover'):
            root = tk.Tk()
            app = SimulatorApp(root, preferences_path=Path(temp) / 'preferences.json')
            errors = []
            root.report_callback_exception = lambda *args: errors.append(args)
            try:
                app.open_flights()
                source = app.flight_window
                report = flight()
                source.set_report(report)
                source.seek(.02)
                flight_axis = source.charts.axes[0]
                flight_axis.set_xlim(10.01, 10.03)
                flight_axis.set_ylim(-.1, .2)
                flight_line = flight_axis.lines[0]
                app.open_logs()
                log = Path(temp) / 'log.txt'
                log.write_text('3.5 :: 1s :: 2026-04-01 12:00:00 :: 110 :: C153A0:6 :: Example\n')
                logs = app.log_window
                logs.set_data(load_log(log))
                logs.axes.set_xlim(-.2, .2)
                log_patch = logs.axes.patches[0]
                app.settings = make_settings('192.0.2.1', '', '', '')
                app.show_view()
                viewer = app.viewer
                sample = RobotSample(1, (0, -1, 1, 0, 0, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic())
                viewer.update_robot(sample, None)
                viewer.zoom = 1.8
                viewer.pan_offset[:] = (.01, .02, .03)
                viewer.redraw()
                root.update()
                camera = viewer.camera_anchor.copy()
                for mode in ('light', 'dark', 'light'):
                    theme.apply_theme(root, mode)
                    root.update()
                    self.assertIs(source.report, report)
                    self.assertAlmostEqual(source.timeline.elapsed, .02)
                    self.assertIs(source.charts.axes[0], flight_axis)
                    self.assertIs(flight_axis.lines[0], flight_line)
                    self.assertEqual(flight_axis.get_xlim(), (10.01, 10.03))
                    self.assertIs(logs.axes.patches[0], log_patch)
                    self.assertEqual(logs.axes.get_xlim(), (-.2, .2))
                    self.assertEqual(logs.figure.get_facecolor(), to_rgba(theme.BACKGROUND))
                    self.assertEqual(viewer.cget('background'), theme.PANEL)
                    self.assertIs(viewer.sample, sample)
                    self.assertEqual(viewer.zoom, 1.8)
                    np.testing.assert_array_equal(viewer.camera_anchor, camera)
                    np.testing.assert_array_equal(viewer.pan_offset, (.01, .02, .03))
                    self.assertEqual(app.active_page, 'simulator')
                self.assertFalse(errors)
            finally:
                app.close()
                del app, root
                gc.collect()


if __name__ == '__main__':
    unittest.main()
