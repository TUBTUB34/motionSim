import unittest
from unittest.mock import Mock

import numpy as np

from kinematics import pose_matrix
from tooling import TOOLS, tool_parts, tool_tcp, tool_placement
from view import RobotView


class ToolTests(unittest.TestCase):
    def test_rendered_suction_contact_face_tracks_live_tcp(self):
        viewer = RobotView.__new__(RobotView)
        viewer.tool = "Suction cup"
        viewer.gripper_opening = .5
        viewer.center = np.zeros(3)
        viewer.eye_direction = np.array([0., 0., 1.])
        viewer.distance, viewer.focal = 5., 800.
        viewer.shaded_faces = Mock()
        flange = pose_matrix((.2, -.3, .6, .4, .7, -.2))
        displays = (np.eye(4), pose_matrix((-.4, .2, .3, -.7, .1, .3)))
        poses = ((.8, -.1, .4, .1, .9, -.6),
                 (.3, .2, .7, 2.8, -.2, .4))
        for display in displays:
            for pose in poses:
                with self.subTest(display=display.tolist(), pose=pose):
                    live = display @ pose_matrix(pose)
                    mesh, _, _ = tool_placement(
                        viewer.tool, display @ flange, "Live TCP", live_tcp=live)
                    viewer.shaded_faces.reset_mock()
                    viewer.tool_mesh(mesh)
                    # Exercise the real cylinder geometry, not just the TCP matrix:
                    # the final cylinder is the cup's bottom contact surface.
                    caps, normals, _ = viewer.shaded_faces.call_args.args
                    contact = caps[-1]
                    np.testing.assert_allclose(contact.mean(axis=0), live[:3, 3], atol=1e-12)
                    cup_axis = (display @ flange)[:3, 2]
                    np.testing.assert_allclose(normals[-1], cup_axis, atol=1e-12)
                    np.testing.assert_allclose(
                        (contact-live[:3, 3]) @ cup_axis, 0., atol=1e-12)

    def test_upward_live_tcp_axes_do_not_invert_downward_suction_cup(self):
        flange = pose_matrix((.3, 0., .5, np.pi, 0., 0.))
        live = pose_matrix((.1, 0., .2, 0., 0., 0.))
        original_flange, original_live = flange.copy(), live.copy()
        mesh, working, _ = tool_placement("Suction cup", flange, "Live TCP", live_tcp=live)
        np.testing.assert_allclose(working[:3, 3], live[:3, 3], atol=1e-12)
        np.testing.assert_allclose(mesh[:3, :3], flange[:3, :3], atol=1e-12)
        # The mounting end and stem must stay above the contact face.
        self.assertGreater(mesh[2, 3], live[2, 3])
        for _, start, end, *_ in tool_parts("Suction cup"):
            for point in (start, end):
                shown = mesh @ np.array((*point, 1.))
                self.assertGreaterEqual(shown[2], live[2, 3]-1e-12)
        np.testing.assert_array_equal(flange, original_flange)
        np.testing.assert_array_equal(live, original_live)

    def test_working_point_alignment_includes_rotation_and_display_frame(self):
        flange = pose_matrix((.2, -.3, .6, .4, .7, -.2))
        offset = (.12, -.08, .25, .3, -.5, 1.2)
        display = pose_matrix((-.4, .2, .3, -.7, .1, .3))
        for tool in TOOLS[1:]:
            mesh, target, source = tool_placement(tool, flange, "Installation TCP", offset)
            np.testing.assert_allclose(mesh @ tool_tcp(tool), flange @ pose_matrix(offset), atol=1e-12)
            np.testing.assert_allclose(target, flange @ pose_matrix(offset), atol=1e-12)
            self.assertEqual(source, "Installation TCP")
            shown_mesh, shown_target, _ = tool_placement(tool, display @ flange, "Installation TCP", offset)
            np.testing.assert_allclose(shown_mesh, display @ mesh, atol=1e-12)
            np.testing.assert_allclose(shown_target, display @ target, atol=1e-12)
            live = pose_matrix((.8, -.1, .4, .1, .9, -.6))
            mesh, target, _ = tool_placement(tool, flange, "Live TCP", live_tcp=live)
            np.testing.assert_allclose(mesh @ tool_tcp(tool), target, atol=1e-12)
            np.testing.assert_allclose(target[:3, 3], live[:3, 3], atol=1e-12)
            np.testing.assert_allclose(mesh[:3, :3], flange[:3, :3], atol=1e-12)
            shown_mesh, shown_target, _ = tool_placement(
                tool, display @ flange, "Live TCP", live_tcp=display @ live)
            np.testing.assert_allclose(shown_mesh, display @ mesh, atol=1e-12)
            np.testing.assert_allclose(shown_target, display @ target, atol=1e-12)

    def test_missing_tcp_falls_back_to_preset(self):
        flange = pose_matrix((.1, .2, .3, .4, .5, .6))
        for source in ("Installation TCP", "Live TCP"):
            mesh, target, label = tool_placement("Default flange-out gripper", flange, source)
            np.testing.assert_allclose(mesh, flange)
            np.testing.assert_allclose(target, flange @ tool_tcp("Default flange-out gripper"))
            self.assertIn("unavailable", label)

    def test_default_mounting_directions_and_wide_frame_opening(self):
        down = tool_tcp("Default flange-down gripper")
        out = tool_tcp("Default flange-out gripper")
        np.testing.assert_allclose(down[:3, 2], [0, 0, 1])
        np.testing.assert_allclose(out[:3, 2], [1, 0, 0], atol=1e-12)
        closed, opened = (tool_parts("Wide frame gripper", gap) for gap in (0, 1))
        def jaw_positions(parts):
            return [p[1][1] for p in parts if p[0] == "box" and p[2] == (.68, .016, .05)]
        np.testing.assert_allclose(jaw_positions(closed), [-.055, .055])
        np.testing.assert_allclose(jaw_positions(opened), [-.16, .16])

    def test_flange_down_rails_open_symmetrically(self):
        closed = tool_parts("Flange-down gripper", 0)
        opened = tool_parts("Flange-down gripper", 1)
        self.assertEqual(closed[:14], opened[:14])
        def rails(parts):
            return [p for p in parts if p[0] == "box" and p[2] == (.55, .016, .025)]
        for parts, half_gap in ((closed, .085), (opened, .14)):
            pair = rails(parts)
            self.assertEqual(len(pair), 2)
            self.assertAlmostEqual(pair[0][1][1], -half_gap)
            self.assertAlmostEqual(pair[1][1][1], half_gap)
        self.assertEqual(tool_parts("Flange-down gripper", -1), closed)
        self.assertEqual(tool_parts("Flange-down gripper", 2), opened)

    def test_gripper_gap_and_fixed_body(self):
        closed = tool_parts("Parallel gripper", 0)
        opened = tool_parts("Parallel gripper", 1)
        self.assertEqual(closed[:2], opened[:2])
        for parts, gap in ((closed, 0), (opened, .08)):
            pads = [p for p in parts if p[-1] == "#24313b"]
            inner_left = pads[0][1][0]+pads[0][2][0]/2
            inner_right = pads[1][1][0]-pads[1][2][0]/2
            self.assertAlmostEqual(inner_right-inner_left, gap)

    def test_all_tools_follow_flange_transform(self):
        viewer = RobotView.__new__(RobotView)
        viewer.gripper_opening = .5
        transform = pose_matrix((.3, -.4, .8, .5, -.7, .2))
        rotation, offset = transform[:3, :3], transform[:3, 3]
        for tool in TOOLS:
            viewer.tool = tool
            viewer.cylinder, viewer.shaded_faces = Mock(), Mock()
            viewer.tool_mesh(np.eye(4))
            cylinders = viewer.cylinder.call_args_list
            faces = viewer.shaded_faces.call_args_list
            viewer.cylinder.reset_mock()
            viewer.shaded_faces.reset_mock()
            viewer.tool_mesh(transform)
            for local, moved in zip(cylinders, viewer.cylinder.call_args_list):
                for index in (0, 1):
                    np.testing.assert_allclose(moved.args[index], rotation @ local.args[index]+offset)
            for local, moved in zip(faces, viewer.shaded_faces.call_args_list):
                np.testing.assert_allclose(moved.args[0], local.args[0] @ rotation.T+offset)
                np.testing.assert_allclose(moved.args[1], local.args[1] @ rotation.T)
            if tool == "None":
                self.assertFalse(cylinders or faces)
            else:
                self.assertTrue(cylinders)
