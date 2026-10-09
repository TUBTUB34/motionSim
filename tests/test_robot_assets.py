"""Visual meshes must follow the same joint frames as live TCP/tool geometry."""
import unittest
from pathlib import Path
import tempfile
import os

import numpy as np

from kinematics import MODELS, forward_kinematics, pose_matrix
from robot_assets import ASSET_DIRECTORY, load_robot_mesh, model_family


class RobotAssetTests(unittest.TestCase):
    def test_every_selected_model_has_distinct_finite_immutable_geometry(self):
        for model in MODELS:
            with self.subTest(model=model):
                links = load_robot_mesh(model)
                self.assertEqual(len(links), 7)
                self.assertIs(load_robot_mesh(model), links)
                for vertices, normals, colors, shading in links:
                    self.assertGreater(len(vertices), 50)
                    self.assertEqual(vertices.shape, (len(normals), 3, 3))
                    self.assertEqual(colors.shape, normals.shape)
                    self.assertTrue(np.isfinite(vertices).all())
                    np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1., atol=1e-5)
                    self.assertTrue(np.isfinite(shading).all())
                    self.assertFalse(vertices.flags.writeable)
                self.assertLess(sum(v.nbytes+n.nbytes+c.nbytes+s.nbytes for v,n,c,s in links), 5_000_000)
        self.assertEqual(model_family("UR15"), "UR Series")
        self.assertEqual(model_family("UR5e"), "e-Series")
        self.assertEqual(model_family("UR5"), "CB-Series")
        self.assertNotEqual(load_robot_mesh("UR5")[2][0].shape, load_robot_mesh("UR5e")[2][0].shape)

    def test_source_joint_chain_matches_live_dh_frames_in_arbitrary_poses(self):
        # Independent URDF joint origins versus the existing DH implementation:
        # checks that zero-pose mesh baking stays valid as *each* joint rotates.
        poses = [np.zeros(6), [.4, -1.2, 1.4, -.8, .6, 2.1], [-2, .8, -.7, 1.8, -.9, -.4]]
        mounting = pose_matrix((.2, -.4, .3, .7, -.2, .5))
        for model in MODELS:
            with np.load(ASSET_DIRECTORY / f"{model.lower().replace(' ', '')}.npz", allow_pickle=False) as data:
                for joints in poses:
                    frames = forward_kinematics(model, joints, mounting)
                    source = mounting.copy()
                    for i, q in enumerate(joints, 1):
                        source = source @ data['source_joint_origins'][i-1] @ pose_matrix((0, 0, 0, 0, 0, q))
                        with self.subTest(model=model, joint=i, pose=joints):
                            np.testing.assert_allclose(frames[i] @ data['dh_from_source_link'][i], source, atol=2e-7)

    def test_asset_paths_do_not_depend_on_launch_directory(self):
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as folder:
            try:
                os.chdir(folder)
                load_robot_mesh.cache_clear()
                self.assertEqual(len(load_robot_mesh("UR20")), 7)
            finally:
                os.chdir(cwd)
        with self.assertRaises(ValueError):
            load_robot_mesh("../unknown")
