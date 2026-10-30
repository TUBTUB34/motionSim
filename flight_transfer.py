"""Read-only flight retrieval over the application's verified SSH/SFTP transport.

The supplied recordings' files/polyscope.log identifies /root/flightreports.
UR's service manual also lists root/flightreports and root/ur-serial:
https://www.universal-robots.com/manuals/EN/HTML/SW5_21/Content/prod-serv-man/UR20/serv-man-SD-data.htm
No remote directories/files are created, removed, or modified by this module.
"""
from dataclasses import dataclass, replace
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import queue
import re
import shutil
import stat
import sys
import tempfile
import threading
import time
import zipfile

import paramiko

from flight_data import load_flight, MAX_TELEMETRY_BYTES, TELEMETRY_MEMBER
from log_transfer import LogJob, LogConnection, DOWNLOAD_IDLE_TIMEOUT
from transfer import host_fingerprint
from transfer_stream import copy_remote

DEFAULT_FLIGHT_DIRECTORY = '/root/flightreports'


def default_library():
    if sys.platform == 'win32':
        base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
    return base / 'URMotionStudio' / 'flightreports'


def _manifest(directory):
    try:
        data = json.loads((directory / 'manifest.json').read_text())
        return data if isinstance(data, dict) and isinstance(data.get('files'), dict) else {'files': {}}
    except (OSError, ValueError):
        return {'files': {}}


def _local_path(directory, record):
    name = record.get('local', '') if isinstance(record, dict) else ''
    if not isinstance(name, str) or not name or Path(name).name != name or name.startswith('.'):
        return None
    path = directory / name
    return path if path.is_file() and not path.is_symlink() else None


def discover_library(root=None):
    """Return only atomically published reports registered by this downloader."""
    root = Path(root) if root is not None else default_library()
    paths = []
    for manifest in sorted(root.glob('robot-*/manifest.json')):
        if manifest.parent.is_symlink():
            continue
        for record in _manifest(manifest.parent)['files'].values():
            path = _local_path(manifest.parent, record)
            if path is not None:
                paths.append(path)
    return sorted(set(paths))


@dataclass(frozen=True)
class RemoteFlight:
    name: str
    path: str
    size: int
    mtime: int
    downloaded: bool = False
    local_path: Path | None = None


class FlightTransfer(LogJob):
    """One persistent worker/session; public requests are nonblocking and serialized.

    cancel() interrupts the current request; close() also stops the worker.
    No credentials or unverified files are persisted in the library manifest.
    """
    def __init__(self, connection: LogConnection, *, library=None,
                 directory=DEFAULT_FLIGHT_DIRECTORY, **kwargs):
        super().__init__(connection=connection, **kwargs)
        self.library = Path(library) if library is not None else default_library()
        self.directory = self._directory(directory)
        self.commands = queue.Queue()
        self.closing = threading.Event()
        self._lock = threading.Lock()
        self._busy = False
        self.sftp = None
        self.fingerprint = None
        self.robot_id = None
        self.serial = ''
        self.entries = []
        self.failed = []
        self._succeeded = 0
        self.manifest = {'files': {}}
        self.generation = 0

    @staticmethod
    def _directory(value):
        if not value.startswith('/') or any(c in value for c in '\x00\r\n') or '..' in PurePosixPath(value).parts:
            raise ValueError('Enter an absolute robot flight-report directory.')
        return str(PurePosixPath(value))

    @property
    def busy(self):
        with self._lock:
            return self._busy

    def _submit(self, operation, value):
        with self._lock:
            if self._busy or self.closing.is_set():
                return False
            self._busy = True
            self.stopped.clear()
            self.commands.put((operation, value))
        return True

    def refresh(self, directory=None):
        return self._submit('list', self._directory(directory or self.directory))

    def download(self, entries=None):
        return self._submit('download', None if entries is None else tuple(entries))

    def retry(self):
        return self.download(tuple(self.failed)) if self.failed else False

    def cancel(self):
        self.stopped.set()
        self._disconnect()

    def close(self):
        self.closing.set()
        self.cancel()
        self.commands.put(None)

    def check_cancel(self):
        if self.stopped.is_set() or self.closing.is_set():
            raise InterruptedError('Flight-report operation cancelled.')

    def _disconnect(self):
        client, self.client = self.client, None
        self.sftp = None
        if client:
            client.close()

    def confirm_host(self, hostname, fingerprint, *, replacement=False):
        # A retry must never approve a different identity midway through a batch.
        if self.fingerprint is not None:
            if replacement or fingerprint != self.fingerprint:
                raise ValueError('Robot identity changed. Reconnect from the application before retrying.')
            return True
        return super().confirm_host(hostname, fingerprint, replacement=replacement)

    def _ensure_connection(self):
        if self.sftp is not None:
            self.check_cancel()
            return
        self.events.put(('state', 'connecting'))
        client = super().connect()
        fingerprint = host_fingerprint(client.get_transport().get_remote_server_key())
        if self.fingerprint is not None and self.fingerprint != fingerprint:
            self._disconnect()
            raise ValueError('Robot identity changed. Reconnect from the application before retrying.')
        self.fingerprint = fingerprint
        self.sftp = client.open_sftp()
        self.sftp.get_channel().settimeout(DOWNLOAD_IDLE_TIMEOUT)
        self.generation += 1
        self.entries = []  # Invalidate the remote directory cache on every reconnect.
        serial = ''
        try:
            with self.sftp.open('/root/ur-serial', 'rb') as stream:
                serial = stream.read(256).decode('utf-8', errors='replace').strip()
        except OSError as error:
            if error.errno not in (errno.ENOENT, errno.EACCES):
                raise
        if self.serial and serial != self.serial:
            self._disconnect()
            raise ValueError('Robot serial changed or could not be verified. Reconnect from the application before retrying.')
        self.serial = serial
        identity = f'{fingerprint}|{serial}'
        self.robot_id = 'robot-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.manifest = _manifest(self.library / self.robot_id)
        self.events.put(('ready', dict(fingerprint=fingerprint, robot_id=self.robot_id,
                                       serial=self.serial, directory=self.directory)))

    def _hash(self, path):
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while True:
                self.check_cancel()
                block = stream.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
        return digest.hexdigest()

    def _existing(self, entry):
        record = self.manifest['files'].get(entry.path, {})
        path = _local_path(self.library / self.robot_id, record)
        if (path is not None and entry.mtime > 0 and record.get('fingerprint') == self.fingerprint and
                record.get('size') == entry.size and record.get('mtime') == entry.mtime and
                path.stat().st_size == entry.size and self._hash(path) == record.get('sha256')):
            return path
        return None

    def _list(self):
        self._ensure_connection()
        self.entries = []
        self.events.put(('state', 'listing'))
        entries = []
        for attr in self.sftp.listdir_attr(self.directory):
            self.check_cancel()
            name = attr.filename
            if (not isinstance(name, str) or PurePosixPath(name).name != name or
                    '\\' in name or any(c in name for c in '\x00\r\n') or
                    not name.lower().endswith('.zip') or
                    not stat.S_ISREG(attr.st_mode or 0)):
                continue
            # Configuration/support archives have no measured playback telemetry.
            if name.lower().startswith('robot_configuration'):
                continue
            entry = RemoteFlight(name, str(PurePosixPath(self.directory) / name),
                                 int(attr.st_size or 0), int(attr.st_mtime or 0))
            local = self._existing(entry)
            entries.append(replace(entry, downloaded=local is not None, local_path=local))
        self.entries = sorted(entries, key=lambda e: (e.mtime, e.name), reverse=True)
        self.events.put(('listed', self.entries.copy()))

    @staticmethod
    def _transient(error):
        if isinstance(error, (TimeoutError, EOFError, ConnectionError)):
            return True
        if isinstance(error, paramiko.SSHException):
            return not isinstance(error, (paramiko.AuthenticationException, paramiko.BadHostKeyException))
        return isinstance(error, OSError) and error.errno in (errno.ECONNRESET, errno.ETIMEDOUT,
                     errno.EPIPE, errno.ECONNABORTED, errno.ENETUNREACH, errno.EHOSTUNREACH)

    def _attempt(self, action):
        for attempt in range(3):
            self.check_cancel()
            try:
                return action()
            except Exception as error:
                self.check_cancel()
                if attempt == 2 or not self._transient(error):
                    raise
                self._disconnect()
                self.events.put(('state', f'retrying ({attempt + 1}/2)'))
                if self.stopped.wait(.25 * 2 ** attempt):
                    self.check_cancel()

    def _validate(self, path):
        # Stream every member for CRC verification, with cancellation and a
        # bounded expansion budget; do not extract archives onto the filesystem.
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if TELEMETRY_MEMBER not in archive.namelist():
                raise ValueError('Archive contains no recorded flight telemetry.')
            if len(infos) > 10000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
                raise ValueError('Flight archive exceeds the safe validation limit.')
            for info in infos:
                with archive.open(info) as stream:
                    while stream.read(1024 * 1024):
                        self.check_cancel()
        self.check_cancel()
        load_flight(path)
        self.check_cancel()

    def _save_manifest(self):
        directory = self.library / self.robot_id
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=directory, prefix='.manifest-',
                                             suffix='.part', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(self.manifest, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            self.check_cancel()
            os.replace(temporary, directory / 'manifest.json')
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _one(self, entry, progress):
        self._ensure_connection()
        self.events.put(('state', 'downloading'))
        before = self.sftp.stat(entry.path)
        if (before.st_size, int(before.st_mtime or 0)) != (entry.size, entry.mtime):
            raise ValueError('Remote file changed since listing. Refresh and select it again.')
        if not stat.S_ISREG(before.st_mode or 0):
            raise ValueError('Remote report is no longer a regular file.')
        local = self._existing(entry)
        if local is not None:
            progress(entry.size, skipped=True)
            return local, True
        if not 0 < entry.size <= MAX_TELEMETRY_BYTES:
            raise ValueError('Flight archive must be between 1 byte and 256 MiB.')
        directory = self.library / self.robot_id
        directory.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(directory).free < entry.size + 1024 * 1024:
            raise OSError(errno.ENOSPC, 'Not enough free disk space for this flight report.')
        temporary = None
        try:
            # Keep a .zip suffix for the existing parser. Hidden staging files
            # are never returned by discover_library, which reads the manifest.
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.flight-', suffix='.part.zip',
                                             delete=False) as local_stream:
                temporary = Path(local_stream.name)
                with self.sftp.open(entry.path, 'rb') as remote:
                    copy_remote(remote, local_stream, entry.size, check_cancel=self.check_cancel, progress=progress)
                local_stream.flush()
                os.fsync(local_stream.fileno())
            after = self.sftp.stat(entry.path)
            if (after.st_size, int(after.st_mtime or 0)) != (entry.size, entry.mtime):
                raise ValueError('Remote file changed during download. Refresh and retry.')
            self.events.put(('state', 'validating'))
            self._validate(temporary)
            digest = self._hash(temporary)
            # Metadata/content revisions retain separate filenames. Existing
            # valid files survive failed retries and files with duplicate names.
            suffix = hashlib.sha256(f'{entry.path}:{entry.mtime}:{digest}'.encode()).hexdigest()[:12]
            safe_name = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(entry.name).stem)[:120]
            destination = directory / f'flight-{safe_name}-{suffix}.zip'
            self.check_cancel()
            os.replace(temporary, destination)
            previous = self.manifest['files'].get(entry.path)
            self.manifest.update(fingerprint=self.fingerprint, serial=self.serial)
            self.manifest['files'][entry.path] = dict(local=destination.name, fingerprint=self.fingerprint,
                    size=entry.size, mtime=entry.mtime, sha256=digest)
            try:
                self._save_manifest()
            except Exception:
                if previous is None:
                    self.manifest['files'].pop(entry.path, None)
                else:
                    self.manifest['files'][entry.path] = previous
                # Do not remove a pre-existing valid revision on failed publish.
                if previous is None or previous.get('local') != destination.name:
                    destination.unlink(missing_ok=True)
                raise
            return destination, False
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _download_batch(self, requested):
        if self.sftp is None or not self.entries:
            self._attempt(self._list)
        available = {e.path: e for e in self.entries}
        if requested is None:
            selected = [e for e in self.entries if self._existing(e) is None]
        else:
            names = list(dict.fromkeys(e.path if isinstance(e, RemoteFlight) else str(e) for e in requested))
            if any(name not in available for name in names):
                raise ValueError('The report listing changed. Refresh and select reports again.')
            selected = [available[name] for name in names]
        self.failed = []
        self._succeeded = 0
        total, completed, succeeded = sum(e.size for e in selected), 0, 0
        for index, entry in enumerate(selected, 1):
            if self.stopped.is_set() or self.closing.is_set():
                self.failed.extend(e.path for e in selected[index - 1:])
            self.check_cancel()
            last_progress = 0.
            file_bytes = 0
            file_started = time.monotonic()
            def progress(value, *, skipped=False):
                nonlocal last_progress, file_bytes
                file_bytes = value
                now = time.monotonic()
                if now - last_progress < .15 and value != entry.size:
                    return
                elapsed = now - file_started
                rate = value / elapsed if elapsed >= .25 and value and not skipped else None
                self.events.put(('progress', dict(name=entry.name, file_bytes=value, file_total=entry.size,
                    overall_bytes=completed + value, overall_total=total, index=index, count=len(selected),
                    rate=rate, eta=(total - completed - value) / rate if rate else None)))
                last_progress = now
            try:
                path, skipped = self._attempt(lambda: self._one(entry, progress))
                updated = replace(entry, downloaded=True, local_path=path)
                available[entry.path] = updated
                self.events.put(('downloaded', dict(entry=updated, path=path, skipped=skipped)))
                succeeded += 1
                self._succeeded = succeeded
            except InterruptedError:
                self.failed.extend(e.path for e in selected[index - 1:])
                raise
            except Exception as error:
                self.check_cancel()
                self.failed.append(entry.path)
                self.events.put(('error', dict(message=self._error(error), path=entry.path)))
            completed += file_bytes
        self.entries = list(available.values())
        self.events.put(('listed', self.entries.copy()))
        return succeeded, len(self.failed)

    def _error(self, error):
        message = str(error)
        if self.connection and self.connection.password:
            message = message.replace(self.connection.password, '[redacted]')
        return message

    def run(self):
        try:
            while not self.closing.is_set():
                command = self.commands.get()
                if command is None:
                    break
                operation, value = command
                started = time.monotonic()
                cancelled, succeeded, failed = False, 0, 0
                self._succeeded = 0
                try:
                    self.check_cancel()
                    if operation == 'list':
                        self.directory = value
                        self._attempt(self._list)
                    else:
                        succeeded, failed = self._download_batch(value)
                except Exception as error:
                    cancelled = isinstance(error, InterruptedError) or self.stopped.is_set()
                    succeeded = self._succeeded
                    failed = len(self.failed) if operation == 'download' else 0
                    if not cancelled:
                        self.events.put(('error', dict(message=self._error(error), path=None)))
                        failed = max(1, len(self.failed))
                    self._disconnect()
                finally:
                    with self._lock:
                        self._busy = False
                    self.events.put(('finished', dict(operation=operation, cancelled=cancelled,
                        succeeded=succeeded, failed=failed, elapsed=time.monotonic() - started)))
        finally:
            self._disconnect()
            self.connection = None
