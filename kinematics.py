"""Nominal UR standard-DH kinematics. Metres and radians.
Source: https://www.universal-robots.com/articles/ur/application-installation/dh-parameters-for-calculations-of-kinematics-and-dynamics
UR30: https://www.universal-robots.com/developer/hardware-and-motion/robot-motion-dh-parameters/
These dimensions do not include the individual robot's factory calibration.
"""
import math
import numpy as np

# a2, a3, d1, d4, d5, d6
MODELS = {
    "UR15": (-.6475, -.5164, .2186, .1824, .1361, .1434),
    "UR3": (-.24365, -.21325, .1519, .11235, .08535, .0819),
    "UR5": (-.425, -.39225, .089159, .10915, .09465, .0823),
    "UR10": (-.612, -.5723, .1273, .163941, .1157, .0922),
    "UR3e": (-.24355, -.2132, .15185, .13105, .08535, .0921),
    "UR5e": (-.425, -.3922, .1625, .1333, .0997, .0996),
    "UR10e": (-.6127, -.57155, .1807, .17415, .11985, .11655),
    "UR16e": (-.4784, -.36, .1807, .17415, .11985, .11655),
    "UR20": (-.862, -.7287, .2363, .2010, .1593, .1543),
    "UR30": (-.6370, -.5037, .2363, .2010, .1593, .1543),
}

def pose_matrix(pose):
    """UR xyz + axis-angle rotation vector, not Euler angles."""
    pose = np.asarray(pose, dtype=float)
    if pose.shape != (6,) or not np.isfinite(pose).all():
        raise ValueError("Expected six finite pose values.")
    t = np.eye(4)
    t[:3, 3] = pose[:3]
    angle = np.linalg.norm(pose[3:])
    if angle > 1e-12:
        x, y, z = pose[3:] / angle
        k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
        t[:3, :3] = np.eye(3) + math.sin(angle)*k + (1-math.cos(angle))*(k @ k)
    return t

def mounting_matrix(base_angle=0., tilt_angle=0.):
    """Mounting tilt about X and base rotation about Z, in radians."""
    return pose_matrix((0, 0, 0, tilt_angle, 0, 0)) @ pose_matrix((0, 0, 0, 0, 0, base_angle))

def forward_kinematics(model, joints, mounting=None):
    q = np.asarray(joints, dtype=float)
    if q.shape != (6,) or not np.isfinite(q).all():
        raise ValueError("Expected six finite joint angles.")
    a2, a3, d1, d4, d5, d6 = MODELS[model]
    frames = [np.eye(4) if mounting is None else np.array(mounting, copy=True)]
    for theta, a, d, alpha in zip(q, (0, a2, a3, 0, 0, 0),
                                  (d1, 0, 0, d4, d5, d6),
                                  (math.pi/2, 0, 0, math.pi/2, -math.pi/2, 0)):
        c, s, ca, sa = math.cos(theta), math.sin(theta), math.cos(alpha), math.sin(alpha)
        dh = np.array([[c, -s*ca, s*sa, a*c], [s, c*ca, -c*sa, a*s],
                       [0, sa, ca, d], [0, 0, 0, 1]])
        frames.append(frames[-1] @ dh)
    return frames


VIEW_FRAMES = ("World (mounting)", "Robot base", "Live TCP")


def view_base_transform(frame, mounting, tcp_pose):
    """Robot-base transform in the selected display frame; robot data is unchanged."""
    if frame == "World (mounting)":
        return np.array(mounting, copy=True)
    if frame == "Robot base":
        return np.eye(4)
    if frame == "Live TCP":
        # RTDE TCP is already expressed relative to the robot base. Its inverse
        # places the TCP at the display origin and aligns its XYZ axes to the grid.
        tcp = pose_matrix(tcp_pose)
        inverse = np.eye(4)
        inverse[:3, :3] = tcp[:3, :3].T
        inverse[:3, 3] = -tcp[:3, :3].T @ tcp[:3, 3]
        return inverse
    raise ValueError(f"Unknown view frame: {frame}")
