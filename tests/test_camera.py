"""Shared live/playback camera behavior, using synthetic robot poses."""
import os
import time
import tkinter as tk
import unittest
from dataclasses import replace

import numpy as np

from kinematics import forward_kinematics, view_base_transform
from rtde_client import RobotSample
from view import RobotView


@unittest.skipUnless(os.environ.get('MOTIONSIM_UI_TESTS') == '1', 'requires native display')
class CameraTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry('1000x520')
        self.viewer = RobotView(self.root, 'UR10e')
        self.viewer.pack(fill='both', expand=True)
        self.root.update()
        self.sample = RobotSample(1, (.7, -1.2, 1, -.3, .5, 0),
                                  (2.3, -1.4, .9, 1.1, -.7, .6), time.monotonic())

    def tearDown(self):
        self.root.destroy()

    def vertices(self):
        view = self.viewer
        reference = view.tcp_view_reference if view.view_frame == 'Live TCP' else view.sample.tcp_pose
        mount = view_base_transform(view.view_frame, np.eye(4), reference)
        frames = forward_kinematics(view.model, view.sample.joints, mount)
        return np.concatenate([link[0].reshape(-1, 3) @ frame[:3, :3].T + frame[:3, 3]
                               for frame, link in zip(frames, view.robot_links)])

    def test_initial_tcp_framing_fits_robot_and_stays_locked_during_playback(self):
        view = self.viewer
        view.set_view_frame('Live TCP')
        view.update_robot(self.sample, None)
        xy, depth = view.project(self.vertices())
        self.assertGreater(depth.min(), 0)
        self.assertTrue((xy.min(axis=0) > [50, 26]).all())
        self.assertTrue((xy.max(axis=0) < [950, 494]).all())
        np.testing.assert_allclose((xy.min(axis=0)+xy.max(axis=0))/2, [500, 260], atol=40)
        anchor = view.camera_anchor.copy()
        reference = view.tcp_view_reference
        view.update_robot(replace(self.sample, joints=(.8, -1, .9, -.1, .4, 0)), None)
        np.testing.assert_array_equal(view.camera_anchor, anchor)
        self.assertEqual(view.tcp_view_reference, reference)
        self.root.geometry('620x650')
        self.root.update()
        np.testing.assert_array_equal(view.camera_anchor, anchor)

    def test_mouse_buttons_rotate_or_pan_in_live_and_recorded_frames(self):
        view = self.viewer
        for frame in ('World (mounting)', 'Live TCP'):
            with self.subTest(frame=frame):
                view.set_view_frame(frame)
                view.update_robot(self.sample, None)
                view.reset_camera()
                view.event_generate('<ButtonPress-1>', x=200, y=200)
                view.event_generate('<B1-Motion>', x=225, y=210)
                view.event_generate('<ButtonRelease-1>', x=225, y=210)
                self.assertAlmostEqual(view.yaw, -1)
                self.assertAlmostEqual(view.elevation, .53)
                np.testing.assert_array_equal(view.pan_offset, np.zeros(3))
                view.zoom_by(1.5)
                angles = view.yaw, view.elevation
                anchor = view.camera_anchor.copy()
                before = view.project([anchor])[0]
                view.event_generate('<ButtonPress-3>', x=300, y=200)
                view.event_generate('<B3-Motion>', x=390, y=245)
                view.event_generate('<ButtonRelease-3>', x=390, y=245)
                after = view.project([anchor])[0]
                np.testing.assert_allclose(after-before, [[90, 45]], atol=1e-8)
                self.assertEqual((view.yaw, view.elevation), angles)
                self.assertEqual(view.sample, self.sample)
                self.assertIsNone(view.pan_drag)
                offset = view.pan_offset.copy()
                view.update_robot(replace(self.sample, joints=(.8, -1, .9, -.1, .4, 0)), None)
                np.testing.assert_array_equal(view.pan_offset, offset)

    def test_reset_clears_pan_and_refits_current_pose_without_changing_tcp_reference(self):
        view = self.viewer
        view.set_view_frame('Live TCP')
        view.update_robot(self.sample, None)
        reference = view.tcp_view_reference
        view.event_generate('<ButtonPress-3>', x=100, y=100)
        view.event_generate('<B3-Motion>', x=400, y=250)
        view.zoom_by(2)
        view.update_robot(replace(self.sample, joints=(1.3, -.5, .3, -.2, .4, .5)), None)
        view.reset_camera()
        self.assertEqual((view.yaw, view.elevation, view.zoom), (-.8, .45, 1.35))
        np.testing.assert_array_equal(view.pan_offset, np.zeros(3))
        self.assertEqual(view.tcp_view_reference, reference)
        xy, _ = view.project(self.vertices())
        self.assertTrue((xy.min(axis=0) > 0).all())
        self.assertTrue((xy.max(axis=0) < [view.w, view.h]).all())
        view.set_view_frame('Robot base')
        np.testing.assert_array_equal(view.pan_offset, np.zeros(3))

