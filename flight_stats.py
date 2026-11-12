"""Full-recording statistics and recorded state changes for one flight.

Shared by the Flight reports summary and the PDF export. Every figure comes from
finite recorded samples; nothing is imputed and no limit or verdict is inferred.
"""
from dataclasses import dataclass

import numpy as np

from flight_data import JOINTS
from telemetry import ROBOT_MODES, RUNTIME_STATES, SAFETY_MODES

STATE_FIELDS = {'Safety mode': SAFETY_MODES, 'Robot mode': ROBOT_MODES, 'Program state': RUNTIME_STATES}
PROTECTIVE_STOP = 3


@dataclass(frozen=True)
class Peak:
    """The sample with the largest magnitude, keeping its sign and elapsed time."""
    value: float
    elapsed: float


@dataclass(frozen=True)
class JointStatistics:
    joint: str
    position_min: float | None
    position_max: float | None
    peak_velocity: Peak | None
    peak_acceleration: Peak | None
    acceleration_derived: bool
    peak_current: Peak | None
    rms_current: float | None
    peak_torque: Peak | None
    torque_is_target: bool
    max_temperature: Peak | None
    min_voltage: Peak | None
    peak_tracking_error: Peak | None

    @property
    def travel(self):
        if self.position_min is None:
            return None
        return self.position_max - self.position_min


@dataclass(frozen=True)
class StateChange:
    elapsed: float
    field: str
    value: int
    label: str
    previous: int | None


def _peak(report, values, pick=None):
    finite = np.isfinite(values)
    if not finite.any():
        return None
    magnitude = np.where(finite, np.abs(values) if pick is None else pick(values), -np.inf)
    index = int(np.argmax(magnitude))
    return Peak(float(values[index]), float(report.time[index]))


def joint_statistics(report):
    """One row per joint, base to wrist 3, over the complete recording."""
    rows = []
    for joint, name in enumerate(JOINTS):
        position = report.series('position', joint)
        target_position = report.series('position', joint, True)
        current = report.series('current', joint)
        finite_position = position[np.isfinite(position)]
        finite_current = current[np.isfinite(current)]
        actual_torque = report.source_header('torque', joint) is not None
        rows.append(JointStatistics(
            joint=name,
            position_min=float(finite_position.min()) if finite_position.size else None,
            position_max=float(finite_position.max()) if finite_position.size else None,
            peak_velocity=_peak(report, report.series('velocity', joint)),
            peak_acceleration=_peak(report, report.series('acceleration', joint)),
            acceleration_derived=report.source_header('acceleration', joint) is None,
            peak_current=_peak(report, current),
            rms_current=float(np.sqrt(np.mean(finite_current * finite_current))) if finite_current.size else None,
            peak_torque=_peak(report, report.series('torque', joint, not actual_torque)),
            torque_is_target=not actual_torque,
            max_temperature=_peak(report, report.series('temperature', joint), pick=lambda v: v),
            min_voltage=_peak(report, report.series('voltage', joint), pick=lambda v: -v),
            peak_tracking_error=_peak(report, position - target_position)))
    return rows


def state_changes(report):
    """Initial value and every recorded change of the safety, robot and program state."""
    changes = []
    for field, labels in STATE_FIELDS.items():
        values = report.columns.get(field)
        if values is None:
            continue
        previous = None
        for index in np.flatnonzero(np.isfinite(values)):
            value = int(values[index])
            if value != previous:
                changes.append(StateChange(float(report.time[index]), field, value,
                                           labels.get(value, f'Unknown ({value})'), previous))
                previous = value
    return sorted(changes, key=lambda change: change.elapsed)


def protective_stops(report):
    """Elapsed (start, end) of each recorded protective-stop interval."""
    safety = report.columns.get('Safety mode')
    if safety is None:
        return []
    stopped = np.where(safety == PROTECTIVE_STOP, safety, np.nan)
    return [(float(report.time[segment.start]), float(report.time[segment.stop - 1]))
            for segment in report.valid_segments(stopped)]


def _column_peak(report, header, pick=None):
    values = report.columns.get(header)
    return None if values is None else _peak(report, values, pick)


def recording_overview(report):
    """Whole-robot figures; a missing channel yields None, never zero."""
    headers = [f'Actual TCP pose {axis} [m]' for axis in 'xyz']
    path = None
    if all(header in report.columns for header in headers):
        points = np.column_stack([report.columns[header] for header in headers])
        path = 0.
        for segment in report.valid_segments(points):
            path += float(np.linalg.norm(np.diff(points[segment], axis=0), axis=1).sum())
    speed_headers = [f'Actual TCP velocity {axis} [m/s]' for axis in 'xyz']
    tcp_speed = None
    if all(header in report.columns for header in speed_headers):
        speed = np.linalg.norm(np.column_stack([report.columns[header] for header in speed_headers]), axis=1)
        tcp_speed = _peak(report, speed)
    return {
        'tcp_path_m': path,
        'peak_tcp_speed': tcp_speed,
        'min_speed_scaling': _column_peak(report, 'Speed scaling', pick=lambda v: -v),
        'peak_arm_current': _column_peak(report, 'Arm current [A]'),
        'min_main_voltage': _column_peak(report, 'Main voltage [V]', pick=lambda v: -v),
        'protective_stops': protective_stops(report),
    }
