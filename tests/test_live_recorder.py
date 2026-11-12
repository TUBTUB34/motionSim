"""Live capture and recording round trip; synthetic samples only."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from types import MappingProxyType
import zipfile

import numpy as np

from flight_data import TELEMETRY_MEMBER, load_flight
from flight_motion import sample_at
from live_recorder import HEADERS, LiveCapture, REPORT_TYPE
from rtde_client import RobotSample
import live_recorder


def sample(tick, *, missing=()):
    timestamp = 100. + tick * .008
    joints = tuple(.1 * joint + .01 * tick for joint in range(6))
    values = {
        'timestamp': timestamp, 'actual_q': joints, 'target_q': tuple(q + .001 for q in joints),
        'actual_qd': (.5,) * 6, 'target_qd': (.5,) * 6, 'target_qdd': (0.,) * 6,
        'actual_current': tuple(1. + joint for joint in range(6)), 'target_current': (1.,) * 6,
        'target_moment': (10.,) * 6, 'actual_current_as_torque': (9.5,) * 6,
        'actual_TCP_pose': (.3 + .001 * tick, -.2, .5, 0., 3.14, 0.), 'actual_TCP_speed': (.125, 0., 0., 0., 0., 0.),
        'joint_temperatures': (30.,) * 6, 'actual_joint_voltage': (48.,) * 6, 'joint_mode': (253,) * 6,
        'robot_mode': 7, 'safety_mode': 3 if tick >= 8 else 1, 'runtime_state': 2,
        'script_control_line': 42, 'speed_scaling': 1., 'payload': 2.5, 'payload_cog': (0., 0., .1),
        'actual_digital_output_bits': 1026,
    }
    for name in missing:
        del values[name]
    return RobotSample(timestamp, joints, values['actual_TCP_pose'], time.monotonic(), MappingProxyType(values))


class LiveRecorderTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.capture = LiveCapture(window_seconds=1., rate=125.)
        self.addCleanup(self.capture.discard_recording)

    def test_recording_opens_as_a_flight_report_with_model_notes_and_states(self):
        self.capture.add(sample(0))
        recording = self.capture.start_recording()
        for tick in range(1, 11):
            self.capture.add(sample(tick))
        self.capture.add(sample(10))  # repeated controller timestamp is not a new sample
        self.capture.stop_recording()
        self.capture.add(sample(11))
        self.assertEqual(recording.samples, 10)
        path = recording.save(Path(self.folder.name) / 'live_UR10e_test.zip', model='UR10e',
                              robot='192.0.2.9', notes='  Pick cycle after tuning ')
        report = load_flight(path)
        self.assertEqual(report.sample_count, 10)
        self.assertEqual(report.model, 'UR10e')
        self.assertEqual(report.metadata['report_type'], REPORT_TYPE)
        self.assertEqual(report.metadata['notes'], 'Pick cycle after tuning')
        self.assertEqual(report.metadata['robot_address'], '192.0.2.9')
        self.assertIn('recorded_at', report.metadata)
        self.assertEqual(report.metadata['sample_rate_hz'], '125')
        np.testing.assert_allclose(report.timestamps[0], 100.008)
        np.testing.assert_allclose(report.series('position', 2), [.2 + .01 * tick for tick in range(1, 11)], atol=1e-6)
        np.testing.assert_allclose(report.series('position', 2, True) - report.series('position', 2), .001, atol=1e-6)
        np.testing.assert_allclose(report.series('current', 5), 6.)
        np.testing.assert_allclose(report.series('torque', 0, True), 10.)
        np.testing.assert_allclose(report.series('torque', 0), 9.5)
        np.testing.assert_allclose(report.columns['Safety mode'], [1] * 7 + [3] * 3)
        self.assertEqual(report.columns['Digital outputs'][0], 1026)
        self.assertFalse(any(word in warning for warning in report.warnings
                             for word in ('missing or invalid', 'malformed', 'unknown', 'gaps')))
        motion = sample_at(report, 0.)
        self.assertEqual(motion.tcp_source, 'Recorded TCP')
        self.assertAlmostEqual(motion.tcp_pose[0], .301, places=6)

    def test_pulled_installation_mounting_travels_with_the_recording(self):
        from installation import InstallationData
        from kinematics import forward_kinematics, mounting_matrix
        self.capture.add(sample(0))
        recording = self.capture.start_recording()
        self.capture.add(sample(1))
        self.capture.stop_recording()
        pulled = InstallationData(tcp=(0., 0., .2, 0., 0., 0.), payload_mass=3.5, payload_cog=(0., 0., .05),
                                  mounting=(.5, 1.5708))
        report = load_flight(recording.save(Path(self.folder.name) / 'mounted.zip', model='UR10e', installation=pulled))
        self.assertEqual(report.installation.mounting, (.5, 1.5708))
        self.assertEqual(report.installation.tcp, pulled.tcp)
        self.assertEqual(report.installation.payload_mass, 3.5)
        # A wall-mounted (tilted) robot's shoulder is no longer straight above its base.
        upright = forward_kinematics('UR10e', report.series('position', 0)[:1].tolist() + [0] * 5)[1][:3, 3]
        mounted = forward_kinematics('UR10e', [0] * 6, mounting_matrix(*report.installation.mounting))[1][:3, 3]
        self.assertGreater(upright[2], .15)
        self.assertLess(abs(mounted[2]), 1e-3)
        plain = self.capture
        plain.add(sample(2))
        second = plain.start_recording()
        plain.add(sample(3))
        plain.stop_recording()
        self.assertIsNone(load_flight(second.save(Path(self.folder.name) / 'plain.zip')).installation)

    def test_unsupported_controller_fields_are_omitted_not_filled(self):
        missing = ('target_moment', 'actual_current_as_torque', 'payload_cog', 'script_control_line')
        self.capture.add(sample(0, missing=missing))
        recording = self.capture.start_recording()
        self.capture.add(sample(1, missing=missing))
        self.capture.add(sample(2, missing=missing))
        self.capture.stop_recording()
        path = recording.save(Path(self.folder.name) / 'partial.zip', model='not a model')
        with zipfile.ZipFile(path) as archive:
            header = archive.read(TELEMETRY_MEMBER).decode().splitlines()[0].split(',')
            self.assertIsNone(json.loads(archive.read('metadata.json'))['robotAddress'])
        self.assertNotIn('Target moment j0 [Nm]', header)
        self.assertNotIn('Script control line', header)
        self.assertNotIn('Main voltage [V]', header)
        self.assertIn('Actual current j0 [A]', header)
        report = load_flight(path)
        self.assertIsNone(report.model)
        self.assertTrue(np.isnan(report.series('torque', 0, True)).all())
        self.assertFalse(any('missing or invalid' in warning for warning in report.warnings))

    def test_window_is_rolling_and_chronological(self):
        for tick in range(300):
            self.capture.add(sample(tick))
        window = self.capture.snapshot(.5, ('Actual position j0 [rad]', 'Safety mode'))
        times = window['Timestamp [s]']
        self.assertTrue((np.diff(times) > 0).all())
        self.assertAlmostEqual(times[-1], 100. + 299 * .008)
        self.assertAlmostEqual(times[-1] - times[0], .5, delta=.009)
        np.testing.assert_allclose(window['Actual position j0 [rad]'][-1], 2.99)
        self.assertAlmostEqual(self.capture.sample_rate(), 125., delta=.01)
        self.capture.reset()
        self.assertEqual(len(self.capture.snapshot(10., ())['Timestamp [s]']), 0)
        self.assertIsNone(self.capture.sample_rate())

    def test_lifecycle_guards_and_size_limit(self):
        with self.assertRaises(ValueError):
            self.capture.start_recording()
        self.capture.add(sample(0))
        recording = self.capture.start_recording()
        self.assertIs(self.capture.start_recording(), recording)
        with self.assertRaises(ValueError):
            recording.save(Path(self.folder.name) / 'active.zip')
        self.capture.stop_recording()
        with self.assertRaises(ValueError):
            recording.save(Path(self.folder.name) / 'empty.zip')
        self.assertEqual(list(Path(self.folder.name).iterdir()), [])
        temporary = recording.path
        self.capture.discard_recording()
        self.assertFalse(temporary.exists())
        self.assertIsNone(self.capture.recording)
        original = live_recorder.MAX_SAMPLES
        live_recorder.MAX_SAMPLES = 3
        try:
            recording = self.capture.start_recording()
            for tick in range(1, 8):
                self.capture.add(sample(tick))
        finally:
            live_recorder.MAX_SAMPLES = original
        self.assertFalse(recording.active)
        self.assertEqual(recording.samples, 3)
        self.assertIn('size limit', recording.stop_reason)

    def test_every_header_is_unique_and_loadable(self):
        self.assertEqual(len(set(HEADERS)), len(HEADERS))
        self.assertEqual(HEADERS[0], 'Timestamp [s]')


if __name__ == '__main__':
    unittest.main()
