"""Read-only robot program snapshots for the source report workflow."""
from pathlib import Path, PurePosixPath
import errno
import os
import shutil
import stat
import tempfile
import threading
import time
from concurrent.futures import FIRST_EXCEPTION, ThreadPoolExecutor, wait

from log_transfer import LogJob
from program_check import MAX_BYTES
from transfer_stream import copy_remote, DOWNLOAD_IDLE_TIMEOUT

MAX_SOURCE_FILES = 4000
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 100_000
MAX_DIRECTORIES = 4096
MAX_DEPTH = 32
PARALLEL_FILES = 8
SOURCE_SUFFIXES = {'.urp', '.script', '.installation'}
WINDOWS_DEVICE_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    f'{prefix}{number}' for prefix in ('COM', 'LPT') for number in '123456789¹²³'
}


def _safe_name(name):
    """Keep source identities unchanged across Linux and Windows snapshots."""
    if (not isinstance(name, str) or name in ('', '.', '..')
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or name.endswith((' ', '.'))
            or name.partition('.')[0].rstrip(' ').upper() in WINDOWS_DEVICE_NAMES):
        raise ValueError(f'Robot filename cannot be saved safely: {name!r}')
    return name


def _signature(attributes):
    return attributes.st_size, getattr(attributes, 'st_mtime', None)


class ProgramSourceJob(LogJob):
    """Download source files to a new snapshot; never modify robot files."""

    def __init__(self, *, connection, destination, **kwargs):
        super().__init__(connection=connection, **kwargs)
        self.destination = Path(destination)
        self.snapshot_path = None
        self.remote_root = None

    def check_cancel(self):
        if self.stopped.is_set():
            raise InterruptedError('Program download cancelled.')

    def _root(self, sftp):
        for root in ('/programs', '/program'):
            self.check_cancel()
            try:
                attributes = sftp.stat(root)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    raise
                continue
            if not stat.S_ISDIR(attributes.st_mode):
                raise ValueError(f'Robot source path is not a directory: {root}')
            return root
        raise FileNotFoundError('Robot programs folder not found: /programs or /program.')

    def _sources(self, sftp, root):
        """Bound traversal and preserve exact names; child links are skipped."""
        pending = [(PurePosixPath(root), 0)]
        entries_seen = directories_seen = total_bytes = skipped_links = 0
        files = []
        while pending:
            self.check_cancel()
            directory, depth = pending.pop()
            if depth > MAX_DEPTH:
                raise ValueError('Robot programs folder nesting exceeds the supported limit.')
            directories_seen += 1
            if directories_seen > MAX_DIRECTORIES:
                raise ValueError('Too many robot program folders to download.')
            if str(directory) != root and not stat.S_ISDIR(sftp.lstat(str(directory)).st_mode):
                raise OSError(f'Robot program folder changed during listing: {directory}')
            self.events.put(('status', f'Finding saved programs in {directory}…'))
            names = set()
            iterator = sftp.listdir_iter(str(directory), read_aheads=8)
            try:
                for attributes in iterator:
                    self.check_cancel()
                    entries_seen += 1
                    if entries_seen > MAX_ENTRIES:
                        raise ValueError('Too many entries in the robot programs folder.')
                    name = attributes.filename
                    mode = attributes.st_mode
                    if stat.S_ISLNK(mode):
                        skipped_links += 1
                        continue
                    is_directory = stat.S_ISDIR(mode)
                    if not is_directory and not (stat.S_ISREG(mode) and PurePosixPath(name).suffix.lower() in SOURCE_SUFFIXES):
                        continue
                    _safe_name(name)
                    key = name.casefold()
                    if key in names:
                        raise ValueError(f'Ambiguous Windows filename in {directory}: {name}')
                    names.add(key)
                    path = directory / name
                    if is_directory:
                        pending.append((path, depth + 1))
                        continue
                    size = attributes.st_size
                    if not isinstance(size, int) or size < 0 or size > MAX_BYTES:
                        raise ValueError(f'Robot source exceeds the 16 MiB file limit: {path}')
                    files.append((path, attributes))
                    total_bytes += size
                    if len(files) > MAX_SOURCE_FILES or total_bytes > MAX_TOTAL_BYTES:
                        raise ValueError('Robot program sources exceed the 4,000 file / 128 MiB snapshot limit.')
            finally:
                close = getattr(iterator, 'close', None)
                if close is not None:
                    close()
        if skipped_links:
            self.events.put(('status', f'Skipped {skipped_links} symbolic links; only regular program sources are downloaded.'))
        return sorted(files, key=lambda entry: str(entry[0])), total_bytes

    def _download(self, sftp, files, root, staging, total_bytes):
        """Copy every listed source; with AsyncSSH several files transfer at once."""
        fast = self._fast_session(False)
        lstat = fast.lstat if fast is not None else sftp.lstat
        open_remote = fast.open if fast is not None else (lambda path, _size: sftp.open(path, 'rb'))
        lock = threading.Lock()
        done = dict(files=0, update=0.)
        received = {}  # bytes so far per file; parallel files each report their own count
        failed = threading.Event()

        def check():
            self.check_cancel()
            if failed.is_set():
                raise InterruptedError('Stopped because another program file failed.')

        def one(remote_path, listed):
            check()
            before = lstat(str(remote_path))
            if not stat.S_ISREG(before.st_mode) or _signature(before) != _signature(listed):
                raise OSError(f'Robot source changed before download; retry: {remote_path}')
            local_path = staging / PurePosixPath(root).name / remote_path.relative_to(root)
            local_path.parent.mkdir(parents=True, exist_ok=True)

            def progress(count):
                now = time.monotonic()
                with lock:
                    received[remote_path] = count
                    if now - done['update'] >= .2 or count == before.st_size:
                        self.events.put(('status', f'Downloading program sources · {done["files"]}/{len(files)} files · '
                                         f'{sum(received.values()) / 1048576:.2f}/{total_bytes / 1048576:.2f} MiB · {remote_path.name} · {self.engine_label}'))
                        done['update'] = now

            with open_remote(str(remote_path), before.st_size) as remote, local_path.open('xb') as local:
                copy_remote(remote, local, before.st_size, check_cancel=check, progress=progress)
                local.flush()
                os.fsync(local.fileno())
            after = lstat(str(remote_path))
            if not stat.S_ISREG(after.st_mode) or _signature(before) != _signature(after):
                raise OSError(f'Robot source changed during download; retry: {remote_path}')
            with lock:
                done['files'] += 1

        def each(function, items):
            if fast is None:
                for item in items:
                    function(*item)
                return
            # Small program files are latency-bound: overlap their round trips.
            with ThreadPoolExecutor(PARALLEL_FILES, thread_name_prefix='program-download') as pool:
                futures = [pool.submit(function, *item) for item in items]
                finished, _ = wait(futures, return_when=FIRST_EXCEPTION)
                failures = [future for future in finished if future.exception() is not None]
                if failures:
                    # Stop the others mid-file, then report the first real cause.
                    failed.set()
                    for future in futures:
                        future.cancel()
                    wait(futures)
                    errors = [future.exception() for future in futures
                              if future.done() and not future.cancelled() and future.exception() is not None]
                    raise next((error for error in errors if not isinstance(error, InterruptedError)), errors[0])

        each(one, files)

        # Recheck earlier copies too: saving a program can replace its URP,
        # generated script and installation while later files are transferring.
        def recheck(remote_path, listed):
            check()
            current = lstat(str(remote_path))
            if not stat.S_ISREG(current.st_mode) or _signature(current) != _signature(listed):
                raise OSError(f'Robot source changed while downloading the snapshot; retry: {remote_path}')
        each(recheck, files)

    def run(self):
        staging = None
        try:
            self.check_cancel()
            if not self.destination.is_dir():
                raise ValueError('Choose an existing folder for the downloaded program sources.')
            self.events.put(('status', 'Connecting to robot to retrieve program sources…'))
            client = self.connect()
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(DOWNLOAD_IDLE_TIMEOUT)
                self.remote_root = root = self._root(sftp)
                files, total_bytes = self._sources(sftp, root)
                if not any(path.suffix.lower() == '.urp' for path, _ in files):
                    raise ValueError(f'No URP files found in {root}. Choose a local folder or save the program on the robot.')
                host = self.connection.ip.replace(':', '-')
                staging = Path(tempfile.mkdtemp(prefix=f'.robot-programs-{host}-', dir=self.destination))
                self._download(sftp, files, root, staging, total_bytes)
                self.check_cancel()
                final_path = staging.with_name(staging.name[1:])
                # The random suffix also prevents replacing an existing download.
                if final_path.exists():
                    raise FileExistsError('Snapshot destination already exists; please retry.')
                staging.rename(final_path)
                staging = None
                self.snapshot_path = final_path
                self.events.put(('downloaded', final_path))
        except Exception as exc:
            if self.stopped.is_set() or isinstance(exc, InterruptedError):
                self.events.put(('cancelled', None))
            else:
                message = str(exc)
                if self.connection.password:
                    message = message.replace(self.connection.password, '[redacted]')
                self.events.put(('error', message))
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            if self.client:
                self.client.close()
            self.close_fast()
