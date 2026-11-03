"""Read-only, bounded diagnostic snapshots using the verified robot transport.

Successful source files survive failures and cancellation. The manifest describes
coverage explicitly; no robot files are changed and no credentials are persisted.
"""
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import time

from flight_data import MAX_TELEMETRY_BYTES, load_flight
from flight_transfer import DEFAULT_FLIGHT_DIRECTORY, FlightTransfer
from log_data import MAX_LOG_BYTES
from transfer_stream import copy_remote


class DiagnosticSourceJob(FlightTransfer):
    """One-shot worker; emits collected/cancelled/error with a result dictionary.

The result contains path, warnings, complete, downloaded_files, and message.
Host-key questions use the normal application host_key event. The destination
must exist; every run creates a separate dataset rather than replacing old data.
"""
    def __init__(self, *, connection, destination,
                 flight_directory=DEFAULT_FLIGHT_DIRECTORY, max_files=500,
                 max_total_bytes=2 * 1024 ** 3, max_listing_entries=10000, **kwargs):
        self.destination = Path(destination)
        if not self.destination.is_dir():
            raise ValueError('Choose an existing folder for the diagnostic dataset.')
        if min(max_files, max_total_bytes, max_listing_entries) <= 0:
            raise ValueError('Diagnostic collection limits must be positive.')
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_listing_entries = max_listing_entries
        self.snapshot_path = None
        self.records = []
        self.warnings = []
        self.downloaded_files = 0
        self.downloaded_bytes = 0
        super().__init__(connection, library=self.destination,
                         directory=flight_directory, **kwargs)

    def _warn(self, message):
        message = self._error(message)
        if message not in self.warnings:
            self.warnings.append(message)
        self.events.put(('status', message))

    def _save_diagnostic_manifest(self, status):
        if self.snapshot_path is None:
            return
        data = dict(schema_version=1, status=status,
                    collected_at=datetime.now(timezone.utc).isoformat(),
                    serial=self.serial, robot_id=self.robot_id, fingerprint=self.fingerprint,
                    warnings=self.warnings, files=self.records,
                    limits=dict(files=self.max_files, total_bytes=self.max_total_bytes,
                                listing_entries=self.max_listing_entries))
        # This must also work after cancellation, without discarding good files.
        target = self.snapshot_path / 'diagnostic_manifest.json'
        temporary = target.with_suffix('.json.part')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _progress(self, name):
        last = 0.
        def update(count):
            nonlocal last
            now = time.monotonic()
            if now - last >= .2:
                self.events.put(('status', f'Downloading {name} · {count / 1048576:.2f} MiB'))
                last = now
        return update

    def _copy_source(self, remote_path, kind, local_name):
        self._ensure_connection()
        self.check_cancel()
        before = self.sftp.stat(remote_path)
        if not stat.S_ISREG(before.st_mode or 0):
            raise ValueError('Source is not a regular file.')
        size = int(before.st_size)
        ceiling = MAX_LOG_BYTES if kind == 'log' else MAX_TELEMETRY_BYTES
        if not 0 <= size <= ceiling:
            raise ValueError(f'Source exceeds the {ceiling // 1048576} MiB file limit.')
        remaining = self.max_total_bytes - self.downloaded_bytes
        if size > remaining:
            raise ValueError('Total diagnostic download size limit reached; coverage is incomplete.')
        directory = self.snapshot_path / ('logs' if kind == 'log' else 'flights')
        directory.mkdir(exist_ok=True)
        if shutil.disk_usage(directory).free < size + 1024 * 1024:
            raise OSError(errno.ENOSPC, 'Not enough free space for this source.')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.source-',
                    suffix='.part' + Path(local_name).suffix, delete=False) as local:
                temporary = Path(local.name)
                with self.sftp.open(remote_path, 'rb') as remote:
                    copied = copy_remote(remote, local, size, check_cancel=self.check_cancel,
                            progress=self._progress(PurePosixPath(remote_path).name),
                            allow_growth=kind == 'log', max_bytes=min(ceiling, remaining))
                local.flush()
                os.fsync(local.fileno())
            self.check_cancel()
            if kind == 'flight':
                after = self.sftp.stat(remote_path)
                if (after.st_size, after.st_mtime) != (before.st_size, before.st_mtime):
                    raise ValueError('Flight report changed during download; retrieve it again.')
                if temporary.suffix.lower() == '.zip':
                    self._validate(temporary)
                else:
                    load_flight(temporary)
                self.check_cancel()
            if copied == 0:
                raise ValueError('Source is empty and contains no diagnostic evidence.')
            digest = self._hash(temporary)
            target = directory / local_name
            self.check_cancel()
            os.replace(temporary, target)
            return dict(path=target.relative_to(self.snapshot_path).as_posix(),
                        size=copied, mtime=before.st_mtime, sha256=digest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _collect_source(self, remote_path, kind, local_name):
        self.check_cancel()
        record = dict(kind=kind, remote_path=remote_path, path=None, status='pending')
        self.records.append(record)
        if self.downloaded_files >= self.max_files:
            record.update(status='skipped', error='Diagnostic file limit reached.')
            self._warn('Diagnostic file limit reached; remaining sources were not retrieved.')
            return
        try:
            result = self._attempt(lambda: self._copy_source(remote_path, kind, local_name))
            record.update(result, status='downloaded')
            self.downloaded_files += 1
            self.downloaded_bytes += result['size']
        except InterruptedError:
            record.update(status='cancelled', error='Collection cancelled before this source completed.')
            raise
        except Exception as error:
            self.check_cancel()
            missing = isinstance(error, OSError) and error.errno == errno.ENOENT
            message = self._error(error)
            record.update(status='missing' if missing else 'failed', error=message)
            self._warn(f'{kind.title()} {PurePosixPath(remote_path).name}: {message}')
        finally:
            self._save_diagnostic_manifest('collecting')

    def _collect_flights(self):
        self._ensure_connection()
        self.events.put(('status', 'Finding flight recordings…'))
        iterator = getattr(self.sftp, 'listdir_iter', None)
        entries = (iterator(self.directory, read_aheads=8) if callable(iterator)
                   else iter(self.sftp.listdir_attr(self.directory)))
        found = 0
        try:
            for index, attr in enumerate(entries):
                self.check_cancel()
                if index >= self.max_listing_entries:
                    self._warn('Flight directory scan limit reached; historical coverage is incomplete.')
                    break
                name = attr.filename
                if (not isinstance(name, str) or name in ('.', '..') or
                        PurePosixPath(name).name != name or '\\' in name or
                        any(c in name for c in '\x00\r\n') or
                        not name.lower().endswith(('.zip', '.csv')) or
                        name.lower().startswith('robot_configuration') or
                        not stat.S_ISREG(attr.st_mode or 0)):
                    continue
                found += 1
                remote_path = str(PurePosixPath(self.directory) / name)
                safe = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(name).stem)[:100]
                suffix = hashlib.sha256(remote_path.encode()).hexdigest()[:12]
                local_name = f'flight-{safe}-{suffix}{Path(name).suffix.lower()}'
                self._collect_source(remote_path, 'flight', local_name)
                if self.downloaded_files >= self.max_files:
                    # Explicit even if the last listed item happened to be final:
                    # the remainder has not been inspected, so coverage is unknown.
                    self._warn('Diagnostic file limit reached; remaining flight coverage was not verified.')
                    break
        finally:
            close = getattr(entries, 'close', None)
            if callable(close):
                close()
        if found == 0:
            self._warn('No supported flight recordings were found; flight evidence is unavailable.')

    def _result(self, status, message):
        return dict(path=self.snapshot_path, warnings=self.warnings.copy(),
                    complete=status == 'complete', downloaded_files=self.downloaded_files,
                    message=message)

    def run(self):
        status, event, message = 'failed', 'error', 'No usable diagnostic sources were retrieved.'
        try:
            self.check_cancel()
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            self.snapshot_path = Path(tempfile.mkdtemp(prefix=f'robot-diagnostics-{stamp}-',
                                                      dir=self.destination))
            self.library = self.snapshot_path / 'flights'
            self._save_diagnostic_manifest('collecting')
            self._attempt(self._ensure_connection)
            current = PurePosixPath(self.connection.path)
            if current.suffix.lower() == '.bak':
                current = current.with_suffix('.txt')
            for remote, name in dict.fromkeys(((str(current.with_suffix('.bak')), 'log_history.bak'),
                                               (str(current), 'log_history.txt'))):
                self._collect_source(remote, 'log', name)
            try:
                self._collect_flights()
            except InterruptedError:
                raise
            except Exception as error:
                self.check_cancel()
                message = self._error(error)
                self.records.append(dict(kind='flight', remote_path=self.directory, path=None,
                        status='missing' if isinstance(error, OSError) and error.errno == errno.ENOENT else 'failed',
                        error=message))
                self._warn(f'Flight recordings: {message}')
            self.check_cancel()
            if self.downloaded_files:
                status = 'partial' if self.warnings else 'complete'
                event, message = 'collected', 'Diagnostic sources saved.'
        except Exception as error:
            if self.stopped.is_set() or self.closing.is_set() or isinstance(error, InterruptedError):
                status, event = 'cancelled', 'cancelled'
                message = 'Collection cancelled. Completed source files were preserved.'
                for record in self.records:
                    if record['status'] == 'pending':
                        record.update(status='cancelled', error='Collection cancelled.')
            else:
                message = self._error(error)
            self._warn(message)
        finally:
            self._disconnect()
            try:
                self._save_diagnostic_manifest(status)
            except OSError as error:
                event, status = 'error', 'failed'
                message = f'Could not save diagnostic coverage manifest: {self._error(error)}'
                self._warn(message)
            self.events.put((event, self._result(status, message)))
            self.connection = None
