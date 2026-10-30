"""Recorded-motion sampling and a UI-independent elapsed-time playback clock.

Joint order is base, shoulder, elbow, wrist 1, wrist 2, wrist 3 (radians).
TCP poses use robot-base xyz in metres and UR axis-angle rotation vectors,
never Euler angles. RobotView supplies nominal DH geometry for these joints.
"""
from dataclasses import dataclass
import math

import numpy as np

from kinematics import forward_kinematics


@dataclass(frozen=True)
class MotionSample:
    timestamp: float
    joints: tuple
    tcp_pose: tuple
    tcp_source: str = "Recorded TCP"


def _quaternion(vector):
    angle = float(np.linalg.norm(vector))
    if angle < 1e-12:
        return np.array([1., 0., 0., 0.])
    return np.r_[math.cos(angle / 2), np.asarray(vector) * (math.sin(angle / 2) / angle)]


def _rotation_vector(quaternion):
    q = np.asarray(quaternion) / np.linalg.norm(quaternion)
    if q[0] < 0:
        q = -q
    length = float(np.linalg.norm(q[1:]))
    if length < 1e-12:
        return np.zeros(3)
    return q[1:] * (2 * math.atan2(length, float(q[0])) / length)


def interpolate_rotation(first, second, fraction):
    """Shortest-arc quaternion SLERP, returned as a UR rotation vector."""
    a, b = _quaternion(first), _quaternion(second)
    dot = float(a @ b)
    if dot < 0:
        b, dot = -b, -dot
    dot = float(np.clip(dot, -1, 1))
    if dot > .9995:
        q = a + fraction * (b - a)
    else:
        angle = math.acos(dot)
        q = (math.sin((1 - fraction) * angle) * a + math.sin(fraction * angle) * b) / math.sin(angle)
    return _rotation_vector(q)


def _matrix_rotation(rotation):
    # Stable matrix -> quaternion, including rotations near pi.
    trace = float(np.trace(rotation))
    if trace > 0:
        s = math.sqrt(trace + 1) * 2
        q = [s / 4, (rotation[2, 1] - rotation[1, 2]) / s,
             (rotation[0, 2] - rotation[2, 0]) / s, (rotation[1, 0] - rotation[0, 1]) / s]
    else:
        i = int(np.argmax(np.diag(rotation)))
        j, k = (i + 1) % 3, (i + 2) % 3
        s = math.sqrt(max(0., 1 + rotation[i, i] - rotation[j, j] - rotation[k, k])) * 2
        q = np.zeros(4)
        q[0] = (rotation[k, j] - rotation[j, k]) / s
        q[i + 1] = s / 4
        q[j + 1] = (rotation[j, i] + rotation[i, j]) / s
        q[k + 1] = (rotation[k, i] + rotation[i, k]) / s
    return _rotation_vector(q)


def sample_at(report, elapsed):
    """Sample measured motion without bridging invalid samples or time gaps.

Exact recorded samples retain the source values. Joint channels with a
[-pi, pi] boundary crossing interpolate across that boundary; continuous
multi-turn encoder positions retain their recorded winding. Missing TCP
channels may use a clearly labelled nominal flange pose, never a flight path.
    """
    if not report.sample_count or not math.isfinite(elapsed):
        return None
    elapsed = float(np.clip(elapsed, 0., report.duration))
    right = min(int(np.searchsorted(report.time, elapsed)), report.sample_count - 1)
    left = right if abs(float(report.time[right]) - elapsed) < 1e-10 else max(0, right - 1)
    dt = float(report.time[right] - report.time[left])
    breaks = getattr(report, "break_before", None)
    if left != right and (dt > report.gap_threshold or (breaks is not None and breaks[right])):
        return None
    fraction = (elapsed - float(report.time[left])) / dt if dt else 0.
    a = np.array([report.series("position", j)[left] for j in range(6)])
    b = np.array([report.series("position", j)[right] for j in range(6)])
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        return None
    delta = b - a
    wrapped = (np.abs(a) <= math.pi) & (np.abs(b) <= math.pi) & (np.abs(delta) > math.pi)
    delta[wrapped] = (delta[wrapped] + math.pi) % (2 * math.pi) - math.pi
    joints = a + fraction * delta
    headers = [f"Actual TCP pose {axis} [{'m' if len(axis) == 1 else 'rad'}]"
               for axis in ("x", "y", "z", "rx", "ry", "rz")]
    tcp_a = np.array([report.columns[h][left] if h in report.columns else np.nan for h in headers])
    tcp_b = np.array([report.columns[h][right] if h in report.columns else np.nan for h in headers])
    source = "Recorded TCP"
    if np.isfinite(tcp_a).all() and np.isfinite(tcp_b).all():
        tcp = tcp_a.copy() if left == right else np.r_[
            tcp_a[:3] + fraction * (tcp_b[:3] - tcp_a[:3]),
            interpolate_rotation(tcp_a[3:], tcp_b[3:], fraction)]
    elif report.model:
        flange = forward_kinematics(report.model, joints)[-1]
        tcp = np.r_[flange[:3, 3], _matrix_rotation(flange[:3, :3])]
        source = "Derived nominal flange (TCP unavailable)"
    else:
        return None
    return MotionSample(float(report.timestamps[0] + elapsed), tuple(joints), tuple(tcp), source)


class PlaybackTimeline:
    """Shared playback clock; callers provide monotonic frame deltas in seconds."""
    def __init__(self, report):
        self.report = report
        self.elapsed = 0.
        self.playing = False
        self.speed = 1.

    def seek(self, elapsed):
        if not math.isfinite(elapsed):
            raise ValueError("Playback time must be finite.")
        self.elapsed = float(np.clip(elapsed, 0., self.report.duration))
        return self.elapsed

    def play(self):
        if self.elapsed >= self.report.duration:
            self.elapsed = 0.
        self.playing = self.report.duration > 0

    def pause(self):
        self.playing = False

    def restart(self):
        self.elapsed = 0.
        self.playing = False

    def tick(self, dt):
        if not math.isfinite(dt) or dt < 0 or not math.isfinite(self.speed) or self.speed <= 0:
            raise ValueError("Playback delta and speed must be finite; speed must be positive.")
        if self.playing:
            self.seek(self.elapsed + dt * self.speed)
            if self.elapsed >= self.report.duration:
                self.pause()
        return self.elapsed
