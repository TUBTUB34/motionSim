"""Mounting adjustment shared by every robot view; use MOTIONSIM_UI_TESTS=1."""
import gc
import math
import os
import time
import tkinter as tk
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from motionstudio.core.installation import InstallationData
from motionstudio.core.kinematics import forward_kinematics, mounting_matrix
from motionstudio.live.rtde_client import RobotSample
from tests.test_flight_ui import flight

SAMPLE = RobotSample(1, (.7, -1.2, 1, -.3, .5, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic())
SAVED = InstallationData(mounting=(math.radians(270), math.radians(45)))


def drawn_mounting(viewer):
    """Robot-base transform the view passes to its kinematics on a redraw."""
    with patch('motionstudio.viewer.robot_view.forward_kinematics', wraps=forward_kinematics) as kinematics:
        viewer.redraw()
    return kinematics.call_args.args[2]


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class MountingAdjustmentTests(unittest.TestCase):
    def setUp(self):
        from motionstudio.viewer.mounting import MountingControls
        from motionstudio.viewer.robot_view import RobotView
        self.root = tk.Tk()
        self.viewers = [RobotView(self.root, 'UR10e'), RobotView(tk.Toplevel(self.root), 'UR5e')]
        self.installation = SAVED
        self.controls = [MountingControls(self.root, lambda: self.installation) for _ in range(2)]
        for widget in self.viewers + self.controls:
            widget.pack()
        self.viewers[0].update_robot(SAMPLE, SAVED)
        self.viewers[1].update_robot(SAMPLE, None)
        self.root.update()

    def tearDown(self):
        self.root.destroy()

    def shown(self, control=0):
        return [variable.get() for variable in self.controls[control].angles]

    def test_saved_mounting_is_shown_until_adjusted(self):
        self.assertEqual(self.shown(), ['270', '45'])
        self.assertEqual(str(self.controls[0].reset.cget('state')), 'disabled')
        np.testing.assert_allclose(drawn_mounting(self.viewers[0]), mounting_matrix(*SAVED.mounting))
        np.testing.assert_allclose(drawn_mounting(self.viewers[1]), np.eye(4))
        self.assertEqual(self.viewers[0].find_withtag('mounting'), ())

    def test_step_buttons_turn_every_robot_view_and_wrap(self):
        self.controls[0].step(0, 45)
        self.controls[0].step(0, 45)
        self.controls[0].step(1, -45)
        self.controls[0].step(1, -45)
        expected = mounting_matrix(0, math.radians(315))
        for viewer in self.viewers:
            np.testing.assert_allclose(drawn_mounting(viewer), expected, atol=1e-9)
            self.assertTrue(viewer.find_withtag('mounting'))
        self.assertEqual(self.shown(0), ['0', '315'])
        self.assertEqual(self.shown(1), ['0', '315'])
        self.assertEqual(str(self.controls[1].reset.cget('state')), 'normal')

    def test_typed_angle_applies_and_bad_text_is_restored(self):
        self.controls[1].angles[1].set('22.5')
        self.controls[1].apply()
        np.testing.assert_allclose(drawn_mounting(self.viewers[1]),
                                   mounting_matrix(math.radians(270), math.radians(22.5)), atol=1e-9)
        self.assertEqual(self.shown(0), ['270', '22.5'])
        for text in ('sideways', 'nan', ''):
            self.controls[0].angles[0].set(text)
            self.controls[0].apply()
            self.assertEqual(self.shown(0), ['270', '22.5'])

    def test_leaving_a_field_unchanged_does_not_adjust(self):
        self.installation = replace(SAVED, mounting=(.4, 1.2))
        self.controls[0].mounting_changed()
        self.controls[0].apply()
        from motionstudio.viewer.robot_view import mounting_override
        self.assertIsNone(mounting_override(self.root))

    def test_playback_refresh_keeps_a_half_typed_angle(self):
        self.controls[0].angles[0].set('18')
        self.controls[0].mounting_changed()
        self.assertEqual(self.shown(), ['18', '45'])
        self.installation = None
        self.controls[0].mounting_changed()
        self.assertEqual(self.shown(), ['0', '0'])
        self.assertIn('upright', self.controls[0].state_text.get())

    def test_use_saved_returns_each_view_to_its_own_installation(self):
        self.controls[0].step(1, 45)
        self.controls[1].reset.invoke()
        np.testing.assert_allclose(drawn_mounting(self.viewers[0]), mounting_matrix(*SAVED.mounting))
        np.testing.assert_allclose(drawn_mounting(self.viewers[1]), np.eye(4))
        self.assertEqual(self.shown(), ['270', '45'])
        self.assertEqual(self.viewers[0].find_withtag('mounting'), ())

    def test_adjusting_refits_the_camera_in_the_world_frame_only(self):
        viewer = self.viewers[0]
        anchor = viewer.camera_anchor.copy()
        self.controls[0].step(1, 90)
        self.assertFalse(np.allclose(viewer.camera_anchor, anchor))
        viewer.set_view_frame('Robot base')
        anchor = viewer.camera_anchor.copy()
        self.controls[0].step(1, 90)
        np.testing.assert_array_equal(viewer.camera_anchor, anchor)
        self.assertEqual(viewer.find_withtag('mounting'), ())


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class SimulatorMountingTests(unittest.TestCase):
    def setUp(self):
        from motionstudio.app.main_window import SimulatorApp
        self.root = tk.Tk()
        self.errors = []
        self.root.report_callback_exception = lambda *args: self.errors.append(args)
        self.discovery = patch('motionstudio.flight.view.FlightWindow._discover')
        self.discovery.start()
        self.app = SimulatorApp(self.root)

    def tearDown(self):
        self.app.close()
        self.discovery.stop()
        del self.app
        del self.root
        gc.collect()
        self.assertEqual(self.errors, [])

    def test_simulator_buttons_reach_playback_flight_reports_and_the_separate_window(self):
        app = self.app
        app.open_flights()
        source = app.flight_window
        source.set_report(replace(flight(), installation=SAVED))
        source.show_simulator()
        app.open_simulator_window('Flight report')
        self.root.update()
        popout = app.simulator_popout
        self.assertEqual([v.get() for v in app.mounting_controls.angles], ['270', '45'])
        self.assertEqual([v.get() for v in popout.mounting_controls.angles], ['270', '45'])
        app.mounting_controls.step(0, -45)
        expected = mounting_matrix(math.radians(225), math.radians(45))
        for viewer in (app.recorded_panel.robot, source.robot, popout.recorded.robot):
            np.testing.assert_allclose(drawn_mounting(viewer), expected, atol=1e-9)
        self.assertEqual([v.get() for v in popout.mounting_controls.angles], ['225', '45'])
        source.set_report(flight())
        self.root.update()
        np.testing.assert_allclose(drawn_mounting(source.robot), expected, atol=1e-9)
        popout.mounting_controls.reset.invoke()
        np.testing.assert_allclose(drawn_mounting(app.recorded_panel.robot), np.eye(4))
        self.assertEqual([v.get() for v in app.mounting_controls.angles], ['0', '0'])


if __name__ == '__main__':
    unittest.main()
