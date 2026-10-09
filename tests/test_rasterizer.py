import unittest

import numpy as np

from rasterizer import rasterize


class RasterizerTests(unittest.TestCase):
    def test_crossing_surfaces_use_pixel_depth_not_average_or_draw_order(self):
        triangle = [[0., 0.], [8., 0.], [0., 8.]]
        points = np.array([triangle, triangle])
        depths = np.array([[1., 8., 8.], [2., 2., 2.]])
        colors = np.array([[[255., 0., 0.]]*3, [[0., 0., 255.]]*3])
        pixels = rasterize(points, depths, colors, 9, 9)
        np.testing.assert_array_equal(pixels[1, 1], [255, 0, 0, 255])
        np.testing.assert_array_equal(pixels[3, 3], [0, 0, 255, 255])
        np.testing.assert_array_equal(pixels, rasterize(points[::-1].copy(), depths[::-1].copy(), colors[::-1].copy(), 9, 9))
        self.assertEqual(pixels[8, 8, 3], 0)

    def test_vertex_colors_interpolate_across_the_surface(self):
        points = np.array([[[0., 0.], [8., 0.], [0., 8.]]])
        pixels = rasterize(points, np.ones((1, 3)), np.array([[[255., 0., 0.], [0., 255., 0.], [0., 0., 255.]]]), 9, 9)
        self.assertTrue((pixels[2, 2, :3] > 0).all())
        self.assertGreater(pixels[0, 0, 0], pixels[2, 2, 0])

    def test_offscreen_degenerate_and_near_plane_faces_are_safe(self):
        points = np.array([[[-10., -10.], [-8., -10.], [-9., -8.]],
                           [[1., 1.], [1., 1.], [1., 1.]],
                           [[0., 0.], [4., 0.], [0., 4.]]])
        depths = np.array([[1., 1., 1.], [1., 1., 1.], [-1., 1., 1.]])
        self.assertFalse(rasterize(points, depths, np.ones((3, 3, 3))*255, 5, 5).any())
