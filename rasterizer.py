"""Perspective-correct, depth-buffered triangle rendering for the Tk viewport."""
import math

import numpy as np
from numba import njit


@njit(cache=True)
def rasterize(points, depths, colors, width, height):
    """Return RGBA pixels; nearer surfaces win regardless of submission order.

    Points: (triangles, 3, 2), positive camera depths: (triangles, 3),
    vertex RGB colors: (triangles, 3, 3). Alpha is zero outside the meshes.
    """
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    zbuffer = np.zeros((height, width), dtype=np.float32)
    for i in range(len(points)):
        if min(depths[i]) <= .05:
            continue
        x0, y0 = points[i, 0]
        x1, y1 = points[i, 1]
        x2, y2 = points[i, 2]
        denominator = (y1-y2)*(x0-x2) + (x2-x1)*(y0-y2)
        if abs(denominator) < 1e-8:
            continue
        left = max(0, int(math.floor(min(x0, x1, x2))))
        right = min(width-1, int(math.ceil(max(x0, x1, x2))))
        top = max(0, int(math.floor(min(y0, y1, y2))))
        bottom = min(height-1, int(math.ceil(max(y0, y1, y2))))
        iz0, iz1, iz2 = 1/depths[i, 0], 1/depths[i, 1], 1/depths[i, 2]
        for y in range(top, bottom+1):
            for x in range(left, right+1):
                a = ((y1-y2)*(x+.5-x2)+(x2-x1)*(y+.5-y2))/denominator
                b = ((y2-y0)*(x+.5-x2)+(x0-x2)*(y+.5-y2))/denominator
                c = 1-a-b
                if a < -1e-7 or b < -1e-7 or c < -1e-7:
                    continue
                inverse_depth = a*iz0 + b*iz1 + c*iz2
                if inverse_depth <= zbuffer[y, x]:
                    continue
                zbuffer[y, x] = inverse_depth
                for channel in range(3):
                    value = (a*iz0*colors[i, 0, channel] + b*iz1*colors[i, 1, channel]
                             + c*iz2*colors[i, 2, channel])/inverse_depth
                    pixels[y, x, channel] = min(255, max(0, int(value+.5)))
                pixels[y, x, 3] = 255
    return pixels
