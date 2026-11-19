"""Live RTDE capture: a rolling window for charts and an optional recording on disk.

Recordings use the column names of UR flight reports (files/realtimedata.csv), so a
saved recording opens in Flight reports exactly like one retrieved from a robot.
Only channels the controller actually streams are written; nothing is filled in.
"""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import threading
import zipfile

import numpy as np

from flight_data import MAX_SAMPLES, MAX_TELEMETRY_BYTES, TELEMETRY_MEMBER

REPORT_TYPE = 'Motion Studio live recording'


def _joints(template):
    return tuple(template.format(j=joint) for joint in range(6))


_AXES = ('x', 'y', 'z', 'rx', 'ry', 'rz')
# RTDE output -> flight-report CSV headers, in file order.
CHANNELS = (
    ('timestamp', ('Timestamp [s]',)),
    ('target_q', _joints('Target position j{j} [rad]')),
    ('target_qd', _joints('Target velocity j{j} [rad/s]')),
    ('target_qdd', _joints('Target acceleration j{j} [rad/s^2]')),
    ('target_current', _joints('Target current j{j} [A]')),
    ('target_moment', _joints('Target moment j{j} [Nm]')),
    ('actual_q', _joints('Actual position j{j} [rad]')),
    ('actual_qd', _joints('Actual velocity j{j} [rad/s]')),
    ('actual_current', _joints('Actual current j{j} [A]')),
    ('actual_current_as_torque', _joints('Actual current as torque j{j} [Nm]')),
    ('actual_TCP_pose', tuple(f'Actual TCP pose {axis} [{"m" if len(axis) == 1 else "rad"}]' for axis in _AXES)),
    ('actual_TCP_speed', tuple(f'Actual TCP velocity {axis} [{"m/s" if len(axis) == 1 else "rad/s"}]' for axis in _AXES)),
    ('actual_TCP_force', tuple(f'TCP force {axis} [{"N" if len(axis) == 1 else "Nm"}]' for axis in _AXES)),
    ('joint_temperatures', _joints('Temperature j{j} [C]')),
    ('actual_joint_voltage', _joints('Voltage j{j} [V]')),
    ('joint_mode', _joints('Control mode j{j}')),
    ('actual_execution_time', ('Realtime thread execution time [ms]',)),
    ('robot_mode', ('Robot mode',)),
    ('safety_mode', ('Safety mode',)),
    ('safety_status', ('Safety status',)),
    ('runtime_state', ('Program state',)),
    ('script_control_line', ('Script control line',)),
    ('speed_scaling', ('Speed scaling',)),
    ('actual_main_voltage', ('Main voltage [V]',)),
    ('actual_robot_voltage', ('Arm voltage [V]',)),
    ('actual_robot_current', ('Arm current [A]',)),
    ('actual_digital_input_bits', ('Digital inputs',)),
    ('actual_digital_output_bits', ('Digital outputs',)),
    ('payload', ('Payload mass [kg]',)),
    ('payload_cog', ('CoG x [m]', 'CoG y [m]', 'CoG z [m]')),
)
HEADERS = tuple(header for _field, headers in CHANNELS for header in headers)
COLUMN = {header: index for index, header in enumerate(HEADERS)}


class Recording:
    """Samples streamed to a temporary CSV; memory use does not grow with length."""
    def __init__(self, available):
        self.columns = [index for index in range(len(HEADERS)) if available[index]]
        self.started_at = datetime.now().astimezone()
        self.samples = 0
        self.first = self.last = None
        self.active = True
        self.stop_reason = ''
        self._file = tempfile.NamedTemporaryFile('w', prefix='ur-live-', suffix='.csv', newline='',
                                                 encoding='utf-8', delete=False)
        self.path = Path(self._file.name)
        self._file.write(','.join(HEADERS[index] for index in self.columns) + '\r\n')
        self._bytes = 0

    @property
    def duration(self):
        return 0. if self.first is None else self.last - self.first

    def write(self, row):
        timestamp = float(row[0])
        if self.last is not None and timestamp <= self.last:
            return  # A repeated controller timestamp carries no new sample.
        # Full timestamp precision; seven significant digits for measurements.
        text = repr(timestamp) + ''.join(',%.7g' % row[index] for index in self.columns[1:]) + '\r\n'
        self._file.write(text)
        self._bytes += len(text)
        if self.first is None:
            self.first = timestamp
        self.last = timestamp
        self.samples += 1
        if self.samples >= MAX_SAMPLES or self._bytes >= MAX_TELEMETRY_BYTES - 1048576:
            self.stop('Recording stopped at the flight-report size limit '
                      f'({MAX_SAMPLES:,} samples / {MAX_TELEMETRY_BYTES // 1048576} MiB).')

    def stop(self, reason=''):
        if self.active:
            self.active = False
            self.stop_reason = reason
            self._file.close()

    def discard(self):
        self.stop()
        self.path.unlink(missing_ok=True)

    def save(self, destination, *, model=None, robot=None, notes='', installation=None):
        """Write a flight-report ZIP atomically and return its path."""
        if self.active:
            raise ValueError('Stop the recording before saving it.')
        if not self.samples:
            raise ValueError('The recording has no samples to save.')
        destination = Path(destination)
        rate = (self.samples - 1) / self.duration if self.duration > 0 else None
        metadata = {'reportId': destination.stem, 'reportType': REPORT_TYPE,
                    'application': 'UR Motion Studio', 'model': model, 'robotAddress': robot,
                    'recordedAt': self.started_at.isoformat(timespec='seconds'),
                    'samples': self.samples, 'durationSeconds': round(self.duration, 6),
                    'sampleRateHz': round(rate, 3) if rate else None, 'notes': notes.strip()}
        if installation is not None:
            # Saved settings pulled from the robot, so playback shows the real mounting.
            metadata['installation'] = {
                'mounting': list(installation.mounting) if installation.mounting else None,
                'tcp': list(installation.tcp) if installation.tcp else None,
                'payloadMass': installation.payload_mass,
                'payloadCog': list(installation.payload_cog) if installation.payload_cog else None}
        summary = ['== UR Motion Studio live recording ==', '',
                   f'Recording Time: {self.started_at.strftime("%a %b %d %H:%M:%S %Z %Y")}',
                   f'Robot address: {robot or "not recorded"}',
                   f'Robot model: {model or "not recorded"} (selected in Motion Studio; nominal geometry)',
                   f'Samples: {self.samples}', f'Duration [s]: {self.duration:.3f}']
        if notes.strip():
            summary += ['', 'Notes: ' + notes.strip()]
        handle, temporary = tempfile.mkstemp(prefix='.ur-live-', suffix='.part', dir=destination.parent)
        os.close(handle)
        temporary = Path(temporary)
        try:
            with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.write(self.path, TELEMETRY_MEMBER)
                archive.writestr('metadata.json', json.dumps(metadata, indent=2))
                archive.writestr('summary.log', '\n'.join(summary) + '\n')
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination


class LiveCapture:
    """Thread-safe sink for RobotSession samples.

    ``add`` runs on the receiver thread; every other method is for the UI thread.
    """
    def __init__(self, window_seconds=120., rate=125.):
        self._layout = []
        start = 0
        for field, headers in CHANNELS:
            self._layout.append((field, start, len(headers)))
            start += len(headers)
        self._ring = np.full((int(window_seconds * rate), len(HEADERS)), np.nan)
        self._count = 0
        self._lock = threading.Lock()
        self.recording = None

    def add(self, sample):
        row = np.full(len(HEADERS), np.nan)
        values = sample.telemetry
        for field, start, count in self._layout:
            value = values.get(field)
            if value is not None:
                if count == 1:
                    row[start] = value
                else:
                    row[start:start + count] = value
        with self._lock:
            self._ring[self._count % len(self._ring)] = row
            self._count += 1
            recording = self.recording
            if recording is not None and recording.active:
                recording.write(row)

    def reset(self):
        """Forget the displayed window, for a new connection's time base."""
        with self._lock:
            self._ring[:] = np.nan
            self._count = 0

    def snapshot(self, seconds, headers):
        """Chronological copies of the requested columns for the latest ``seconds``."""
        columns = [COLUMN[header] for header in ('Timestamp [s]', *headers)]
        # Only the requested columns are copied, so the receiver thread waits on
        # the lock for microseconds, not for a copy of the whole 120 s window.
        with self._lock:
            size = min(self._count, len(self._ring))
            end = self._count % len(self._ring)
            rows = (self._ring[:size, columns] if size < len(self._ring)
                    else np.concatenate((self._ring[end:, columns], self._ring[:end, columns])))
        if size:
            rows = rows[rows[:, 0] >= rows[-1, 0] - seconds]
        return {header: rows[:, index] for index, header in enumerate(('Timestamp [s]', *headers))}

    @property
    def received(self):
        """Samples received since the last reset; the UI derives frames per second from it."""
        with self._lock:
            return self._count

    def sample_rate(self):
        """Measured stream rate in Hz from the most recent samples, or None."""
        times = self.snapshot(2., ())['Timestamp [s]']
        steps = np.diff(times[-64:])
        steps = steps[steps > 0]
        return float(1 / np.median(steps)) if len(steps) else None

    def start_recording(self):
        with self._lock:
            if self.recording is not None and self.recording.active:
                return self.recording
            if not self._count:
                raise ValueError('No live data yet. Wait for the robot stream before recording.')
            latest = self._ring[(self._count - 1) % len(self._ring)]
            self.recording = Recording(np.isfinite(latest))
            return self.recording

    def stop_recording(self, reason=''):
        with self._lock:
            if self.recording is not None:
                self.recording.stop(reason)

    def discard_recording(self):
        with self._lock:
            recording, self.recording = self.recording, None
        if recording is not None:
            recording.discard()
