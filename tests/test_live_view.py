"""Live monitor charts and TCP acceleration, without Tk (plain Agg figures)."""
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from motionstudio.live.recorder import COLUMN
from motionstudio.live.view import (LiveCharts, settle_limits, PROTECTIVE_STOP, SIGNALS, TCP_SIGNALS, TCP_VELOCITY, stop_spans,
                       tcp_acceleration, tcp_series)


def stream(seconds=10., rate=125., stops=()):
    """A synthetic snapshot dict with every live header the charts may ask for."""
    times = 1000. + np.arange(int(seconds * rate)) / rate
    data = {header: np.sin(times + index) for index, header in enumerate(COLUMN)}
    data['Timestamp [s]'] = times
    data['Safety mode'] = np.ones(len(times))
    for first, last in stops:
        data['Safety mode'][first:last] = PROTECTIVE_STOP
    return data


class TcpAccelerationTests(unittest.TestCase):
    def test_linear_velocity_ramp_gives_constant_acceleration(self):
        times = np.arange(200) / 125.
        velocity = np.column_stack((2 * times, -.5 * times, np.zeros_like(times)))
        acceleration = tcp_acceleration(times, velocity)
        np.testing.assert_allclose(acceleration, np.tile([2., -.5, 0.], (200, 1)), atol=1e-9)

    def test_gap_and_missing_sample_do_not_spike(self):
        times = np.arange(300) / 125.
        times[150:] += 1.  # one second without data
        velocity = np.column_stack((np.where(times > 1.5, 1., 0.), np.zeros(300), np.zeros(300)))
        velocity[60] = np.nan
        acceleration = tcp_acceleration(times, velocity)
        finite = acceleration[np.isfinite(acceleration)]
        self.assertTrue(len(finite))
        self.assertLess(np.abs(finite).max(), 1e-9)
        self.assertTrue(np.isnan(acceleration[60]).all())

    def test_duplicate_or_backwards_timestamps_give_no_infinity(self):
        times = np.arange(100) / 125.
        times[50] = times[49]
        times[70] = times[60]
        velocity = np.column_stack((times, times, times))
        acceleration = tcp_acceleration(times, velocity)
        self.assertFalse(np.isinf(acceleration).any())
        np.testing.assert_allclose(acceleration[np.isfinite(acceleration)], 1., atol=1e-9)

    def test_empty_snapshot_gives_empty_result(self):
        # Happens right after a reconnect resets the capture ring.
        self.assertEqual(tcp_acceleration(np.empty(0), np.empty((0, 3))).shape, (0, 3))

    def test_missing_tcp_speed_stays_missing(self):
        times = np.arange(100) / 125.
        self.assertTrue(np.isnan(tcp_acceleration(times, np.full((100, 3), np.nan))).all())
        self.assertTrue(np.isnan(tcp_acceleration(times[:2], np.zeros((2, 3)))).all())
        self.assertTrue(np.isnan(tcp_series('tcp_speed', times, np.full((100, 3), np.nan))).all())

    def test_averaging_reduces_noise_at_125_and_500_hz(self):
        generator = np.random.default_rng(1)
        for rate in (125., 500.):
            times = np.arange(int(4 * rate)) / rate
            velocity = np.column_stack((np.sin(times), np.zeros_like(times), np.zeros_like(times)))
            velocity += generator.normal(0, 1e-4, velocity.shape)
            raw = np.gradient(velocity[:, 0], times) - np.cos(times)
            smooth = tcp_acceleration(times, velocity)[:, 0] - np.cos(times)
            self.assertLess(np.std(smooth), np.std(raw) / 2, rate)

    def test_magnitude_column(self):
        times = np.arange(10) / 125.
        series = tcp_series('tcp_speed', times, np.tile([3., 4., 0.], (10, 1)))
        np.testing.assert_allclose(series[:, 3], 5.)


class LiveChartsTests(unittest.TestCase):
    def setUp(self):
        self.figure = Figure(figsize=(10, 8), dpi=100)
        FigureCanvasAgg(self.figure)
        self.charts = LiveCharts(self.figure)

    def titles(self):
        return [axis.get_title(loc='left') for axis in self.charts.axes]

    def test_combined_overlays_joints_in_one_panel_per_metric(self):
        self.charts.configure(['position', 'velocity', 'tcp_acceleration'], [0, 2], True, True)
        self.assertEqual(self.charts.panels, [('position', (0, 2)), ('velocity', (0, 2)), ('tcp_acceleration', None)])
        self.assertEqual(len(self.charts.axes[0].lines), 4)  # two joints, actual and target
        self.assertIn('derived', self.titles()[2])
        self.assertTrue(set(self.charts.headers) <= set(COLUMN))
        self.assertTrue(set(TCP_VELOCITY) <= set(self.charts.headers))

    def test_separate_joints_gives_one_panel_per_metric_and_joint(self):
        metrics = [*SIGNALS, *TCP_SIGNALS]
        self.charts.configure(metrics, list(range(6)), True, False, separate=True)
        self.assertEqual(len(self.charts.axes), 5 * 6 + 2)
        self.assertEqual([metric for metric, _joints in self.charts.panels],
                         [metric for metric in metrics for _ in range(1 if metric in TCP_SIGNALS else 6)])
        self.assertTrue(self.titles()[1].startswith('Position · Shoulder'))
        self.assertTrue(set(self.charts.headers) <= set(COLUMN))
        position = self.charts.axes[:6]
        self.assertFalse(position[0].get_shared_y_axes().joined(position[0], position[1]))

    def test_shared_scale_joins_y_within_each_metric_only(self):
        self.charts.configure(['position', 'current'], [0, 1, 2], False, True, separate=True, shared_scale=True)
        axes = self.charts.axes
        shared = axes[0].get_shared_y_axes()
        self.assertTrue(shared.joined(axes[0], axes[2]))
        self.assertFalse(shared.joined(axes[0], axes[3]))
        data = stream()
        data['Actual position j1 [rad]'] = data['Actual position j1 [rad]'] * 10
        self.charts.update(30., data)
        self.figure.canvas.draw()
        self.assertEqual(axes[0].get_ylim(), axes[1].get_ylim())

    def test_separate_without_joints_falls_back_to_combined(self):
        self.charts.configure(['position'], [], True, True, separate=True)
        self.assertEqual(self.charts.panels, [('position', ())])
        self.assertTrue(self.charts.notes[0].get_visible())

    def test_update_draws_and_shades_protective_stops(self):
        self.charts.configure(['velocity', 'tcp_speed', 'tcp_acceleration'], [1], True, True)
        self.charts.update(10., stream(stops=((200, 260),)))
        self.figure.canvas.draw()
        for axis, shading in zip(self.charts.axes, self.charts.stops):
            self.assertEqual(len(shading.get_paths()), 1)
            self.assertEqual(axis.get_xlim(), (-10., 0.))
            self.assertTrue(all(len(line.get_xdata()) for line in axis.lines))
        self.charts.update(10., stream())
        self.assertEqual(len(self.charts.stops[0].get_paths()), 0)

    def test_tcp_panels_say_when_the_controller_does_not_stream_tcp_speed(self):
        self.charts.configure(['position', 'tcp_acceleration'], [0], False, True)
        data = stream()
        for header in TCP_VELOCITY:
            data[header] = np.full(len(data['Timestamp [s]']), np.nan)
        self.charts.update(10., data)
        self.figure.canvas.draw()
        self.assertEqual([note.get_visible() for note in self.charts.notes], [False, True])

    def test_only_panels_near_the_viewport_are_rendered(self):
        self.charts.configure(list(SIGNALS), list(range(6)), True, True, separate=True)
        self.figure.set_size_inches(10, (30 * 160 + 60) / 100)
        self.charts.update(30., stream(30.))
        self.charts.band = (0., .15)
        self.figure.canvas.draw()
        self.charts._layout()
        visible = [axis.get_visible() for axis in self.charts.axes]
        self.assertTrue(visible[0])
        self.assertFalse(visible[-1])
        self.assertLess(sum(visible), 10)
        self.assertTrue(self.charts.view(.85, 1.))
        self.charts._layout()
        self.assertTrue(self.charts.axes[-1].get_visible())
        self.assertFalse(self.charts.view(.85, 1.))

    def test_tcp_components_are_optional_and_the_magnitude_leads(self):
        self.charts.configure(['tcp_speed'], [], False, True)
        self.assertEqual(len(self.charts.axes[0].lines), 1)
        self.assertIsNone(self.charts.axes[0].get_legend())
        self.charts.configure(['tcp_speed'], [], False, True, components=True)
        lines = self.charts.axes[0].lines
        self.assertEqual([line.get_label() for line in lines], ['x', 'y', 'z', '|v|'])
        self.assertGreater(lines[3].get_linewidth(), lines[0].get_linewidth())
        self.assertIsNotNone(self.charts.axes[0].get_legend())

    def test_latest_value_shows_on_tcp_and_per_joint_panels(self):
        self.charts.configure(['position', 'tcp_speed'], [0, 1], False, True, separate=True)
        self.charts.update(10., stream())
        right = [axis.get_title(loc='right') for axis in self.charts.axes]
        self.assertTrue(right[0].endswith(' °') and right[2].endswith(' m/s'), right)
        self.charts.configure(['position'], [0, 1], False, True)
        self.charts.update(10., stream())
        self.assertEqual(self.charts.axes[0].get_title(loc='right'), '')

    def test_y_range_grows_at_once_and_shrinks_only_when_mostly_unused(self):
        axis = self.figure.add_subplot()
        settle_limits(axis, 0., 1.)
        first = axis.get_ylim()
        settle_limits(axis, .1, .9)
        self.assertEqual(axis.get_ylim(), first)
        settle_limits(axis, 0., 2.)
        self.assertGreaterEqual(axis.get_ylim()[1], 2.)
        settle_limits(axis, 0., .2)
        self.assertLess(axis.get_ylim()[1], 1.)

    def test_stop_spans(self):
        x = np.arange(10.)
        stopped = np.zeros(10, bool)
        stopped[[2, 3, 9]] = True
        self.assertEqual(stop_spans(x, stopped), [(2., 4.), (9., 9.)])


if __name__ == '__main__':
    unittest.main()
