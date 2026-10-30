import math
from pathlib import Path
import unittest

import numpy as np

from flight_data import FlightReport, discover_flights, load_flight
from flight_motion import PlaybackTimeline, interpolate_rotation, sample_at
from kinematics import pose_matrix


def fixture(times=(0., .1, .2), joints=None):
    times = np.array(times)
    joints = np.array(joints if joints is not None else [0., .1, .2], dtype=float)
    columns = {f"Actual position j{j} [rad]": joints.copy() for j in range(6)}
    for axis in ("x", "y", "z", "rx", "ry", "rz"):
        columns[f"Actual TCP pose {axis} [{'m' if len(axis) == 1 else 'rad'}]"] = np.zeros(len(times))
    return FlightReport(Path("fixture.csv"), "fixture", {}, times + 100, times, columns, model="UR10e")


class MotionTests(unittest.TestCase):
    def test_recorded_samples_match_every_source_joint_and_tcp(self):
        paths = discover_flights(Path(__file__).resolve().parents[1] / "assets/flightreports")
        if not paths:
            self.skipTest("Local recording assets are not bundled in the repository")
        for path in paths:
            report = load_flight(path)
            for i in (0, 100, report.sample_count // 2, report.sample_count - 1):
                with self.subTest(path=path.name, sample=i):
                    sample = sample_at(report, float(report.time[i]))
                    np.testing.assert_allclose(sample.joints, [report.series("position", j)[i] for j in range(6)])
                    expected = [report.columns[f"Actual TCP pose {a} [{'m' if len(a) == 1 else 'rad'}]"][i]
                                for a in ("x", "y", "z", "rx", "ry", "rz")]
                    np.testing.assert_allclose(sample.tcp_pose, expected)
                    self.assertEqual(sample.tcp_source, "Recorded TCP")

    def test_joint_wrap_and_continuous_winding(self):
        r = fixture(joints=np.radians([179, -179, -178]))
        self.assertAlmostEqual(sample_at(r, .05).joints[0], math.pi)
        r = fixture(joints=[7., 8., 9.])
        self.assertAlmostEqual(sample_at(r, .05).joints[0], 7.5)

    def test_axis_angle_slerp_avoids_wrap_and_euler_interpolation(self):
        result = interpolate_rotation([0, 0, math.radians(179)], [0, 0, math.radians(-179)], .5)
        np.testing.assert_allclose(pose_matrix([0, 0, 0, *result])[:3, :3],
                                   np.diag([-1., -1., 1.]), atol=1e-10)
        result = interpolate_rotation([math.pi, 0, 0], [0, math.pi, 0], .5)
        np.testing.assert_allclose(np.linalg.norm(result), math.pi)

    def test_no_interpolation_across_missing_samples_or_gaps(self):
        r = fixture(times=(0., .1, .2, 2., 2.1), joints=[0, 1, 2, 3, 4])
        self.assertIsNone(sample_at(r, 1.))
        r.columns["Actual position j1 [rad]"][1] = np.nan
        self.assertIsNone(sample_at(r, .05))
        self.assertIsNotNone(sample_at(r, 0.))

    def test_missing_tcp_is_explicitly_derived_and_no_unknown_model_guess(self):
        r = fixture()
        del r.columns["Actual TCP pose x [m]"]
        self.assertIn("Derived", sample_at(r, .05).tcp_source)
        r.model = None
        self.assertIsNone(sample_at(r, .05))

    def test_rejected_timestamp_break_is_not_interpolated(self):
        r = fixture()
        r.break_before = np.array([False, True, False])
        self.assertIsNone(sample_at(r, .05))
        self.assertIsNotNone(sample_at(r, .1))
        self.assertIsNotNone(sample_at(r, .15))

    def test_tcp_translation_interpolates_in_metres_and_recorded_rotation_at_exact_sample(self):
        r = fixture()
        r.columns["Actual TCP pose x [m]"] = np.array([.5, 1.5, 2.5])
        r.columns["Actual TCP pose rz [rad]"] = np.array([3., -3., -2.])
        sample = sample_at(r, .025)
        self.assertAlmostEqual(sample.tcp_pose[0], .75)
        self.assertAlmostEqual(sample.timestamp, 100.025)
        self.assertEqual(sample_at(r, .1).tcp_pose[-1], -3.)
        self.assertIsNone(sample_at(r, float("nan")))

    def test_derived_flange_rotation_matches_kinematics_near_pi(self):
        from kinematics import forward_kinematics
        r = fixture(joints=[0., math.pi, math.pi])
        r.columns = {key: value for key, value in r.columns.items() if "TCP" not in key}
        for elapsed in (0., .05, .1):
            sample = sample_at(r, elapsed)
            expected = forward_kinematics(r.model, sample.joints)[-1]
            np.testing.assert_allclose(pose_matrix(sample.tcp_pose), expected, atol=1e-12)

    def test_single_sample_and_invalid_playback_clock_inputs(self):
        r = fixture(times=(0.,), joints=[0.])
        clock = PlaybackTimeline(r)
        clock.play()
        self.assertFalse(clock.playing)
        self.assertIsNotNone(sample_at(r, 3.))
        for value in (-1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                clock.tick(value)
        with self.assertRaises(ValueError):
            clock.seek(float("nan"))
        clock.speed = 0
        with self.assertRaises(ValueError):
            clock.tick(.1)

    def test_timeline_speed_seek_pause_end_and_report_reset(self):
        r = fixture()
        clock = PlaybackTimeline(r)
        clock.speed = 2
        clock.play()
        self.assertAlmostEqual(clock.tick(.05), .1)
        clock.pause()
        self.assertAlmostEqual(clock.tick(.05), .1)
        clock.play()
        self.assertEqual(clock.tick(.5), .2)
        self.assertFalse(clock.playing)
        clock.restart()
        self.assertEqual(clock.elapsed, 0)
        clock.seek(-2)
        self.assertEqual(clock.elapsed, 0)
        self.assertEqual(PlaybackTimeline(r).speed, 1)


if __name__ == "__main__":
    unittest.main()
