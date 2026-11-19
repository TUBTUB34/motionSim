"""Read-only robot log download and charting; no credentials on disk."""
from pathlib import Path, PurePosixPath
import errno
import os
import shutil
import tempfile
import time

import paramiko

from motionstudio.logs.data import MAX_LOG_BYTES, load_log, load_logs
from motionstudio.remote.ssh_job import SshJob
from motionstudio.remote.stream import copy_remote, DOWNLOAD_IDLE_TIMEOUT


class LogJob(SshJob):
    """Download the robot log pair (.bak and current), chart it, and optionally keep copies."""

    def download(self, destination):
        settings = self.connection
        client = self.connect()
        with client.open_sftp() as sftp:
            # Channel reads time out only while waiting for incoming data. Do not
            # impose a total duration limit on an actively progressing download.
            sftp.get_channel().settimeout(DOWNLOAD_IDLE_TIMEOUT)
            current = PurePosixPath(settings.path)
            if current.suffix.lower() == '.bak':
                current = current.with_suffix('.txt')
            backup = current.with_suffix('.bak')
            paths = []
            missing_backup = False
            for remote_path in dict.fromkeys((str(backup), str(current))):
                self.check_cancel()
                local_path = destination.parent / PurePosixPath(remote_path).name
                try:
                    size = sftp.stat(remote_path).st_size
                    if size > MAX_LOG_BYTES:
                        raise ValueError("Robot log exceeds 64 MiB per file. Retrieve a smaller log.")
                    total = 0
                    last_progress = time.monotonic()
                    last_bytes = 0
                    rate = 0.

                    def report_progress(now):
                        nonlocal last_progress, last_bytes, rate
                        if total > last_bytes:
                            rate = (total - last_bytes) / max(now - last_progress, .001)
                        speed = (f'{rate / 1048576:.2f} MiB/s' if rate >= 1048576 else
                                 f'{rate / 1024:.1f} KiB/s' if rate >= 1024 else f'{rate:.0f} B/s')
                        self.events.put(('status', f'Downloading {local_path.name} · '
                                         f'{total / 1048576:.2f} / {size / 1048576:.2f} MiB · {speed} · {self.engine_label}'))
                        last_progress, last_bytes = now, total

                    report_progress(last_progress)
                    def transferred(count):
                        nonlocal total
                        total = count
                        now = time.monotonic()
                        if now - last_progress >= .2:
                            report_progress(now)

                    with self.open_bulk(sftp, remote_path, compress=True, size=size) as remote, local_path.open('wb') as local:
                        try:
                            copy_remote(remote, local, size, check_cancel=self.check_cancel,
                                        progress=transferred, allow_growth=True, max_bytes=MAX_LOG_BYTES)
                        except TimeoutError:
                            raise TimeoutError(f'Download stalled: no data received for '
                                               f'{DOWNLOAD_IDLE_TIMEOUT} seconds. Try again.') from None
                    report_progress(time.monotonic())
                    paths.append(local_path)
                except OSError as error:
                    if remote_path != str(current) and error.errno == errno.ENOENT:
                        missing_backup = True
                    else:
                        raise
            return paths, missing_backup

    def save_downloads(self, paths):
        """Stage the original log bytes before replacing the chosen local files."""
        destinations = [self.save_path.with_suffix('.bak'), self.save_path] if len(paths) == 2 else [self.save_path]
        if len(set(destinations)) != len(destinations):
            raise ValueError('Choose a .txt filename to save both log files.')
        if len(paths) == 1 and self.save_path.with_suffix('.bak').exists():
            raise ValueError('Choose a different filename: an older .bak already exists here but the robot has no backup log.')
        staged = []
        originals = {}
        published = []
        try:
            for source, destination in zip(paths, destinations):
                self.check_cancel()
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.ur-log-', delete=False) as stream:
                    staged.append((Path(stream.name), destination))
                    with source.open('rb') as original:
                        shutil.copyfileobj(original, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
            # A pair must not silently combine this download with an older
            # current log if publishing the second file fails. Preserve the
            # previous bytes before changing either destination.
            if len(staged) > 1:
                for _, destination in staged:
                    self.check_cancel()
                    if destination.exists():
                        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.ur-log-restore-',
                                                         delete=False) as stream:
                            originals[destination] = Path(stream.name)
                            with destination.open('rb') as original:
                                shutil.copyfileobj(original, stream)
                            stream.flush()
                            os.fsync(stream.fileno())
                    else:
                        originals[destination] = None
            self.check_cancel()
            try:
                for temporary, destination in staged:
                    os.replace(temporary, destination)
                    published.append(destination)
            except OSError as error:
                recovery = []
                for destination in reversed(published):
                    original = originals.get(destination)
                    try:
                        if original is None:
                            destination.unlink()
                        else:
                            os.replace(original, destination)
                    except OSError:
                        if original is not None:
                            # Retain this copy for manual recovery when even
                            # rollback is blocked by the destination filesystem.
                            originals.pop(destination)
                            recovery.append(f'{destination}: previous copy at {original}')
                        else:
                            recovery.append(f'{destination}: new file could not be removed')
                if recovery:
                    raise OSError(f'Log save failed: {error}. Recovery needed: ' + '; '.join(recovery)) from error
                raise
            return destinations
        finally:
            for temporary, _ in staged:
                temporary.unlink(missing_ok=True)
            for original in originals.values():
                if original is not None:
                    original.unlink(missing_ok=True)

    def run(self):
        try:
            with tempfile.TemporaryDirectory(prefix='ur-log-') as folder:
                path = Path(self.path) if self.path else Path(folder) / 'log_history.txt'
                if self.connection:
                    self.events.put(('status', 'Connecting to robot over SSH…'))
                    paths, missing_backup = self.download(path)
                self.check_cancel()
                self.events.put(('status', 'Reading log and preparing charts…'))
                if self.connection:
                    source = f"{self.connection.ip} · " + ' + '.join(p.name for p in paths)
                    result = load_logs(paths, source, self.check_cancel)
                    if missing_backup:
                        result.metadata['warning'] = 'Backup not found · Current log only'
                    if self.save_path:
                        try:
                            saved = self.save_downloads(paths)
                            result.metadata['saved_logs'] = [str(path) for path in saved]
                        except (OSError, ValueError) as error:
                            self.check_cancel()
                            result.metadata['save_error'] = str(error).replace(self.connection.password, '[redacted]')
                else:
                    result = load_log(path, check_cancel=self.check_cancel)
                self.check_cancel()
                self.events.put(('loaded', result))
        except Exception as error:
            if self.stopped.is_set():
                self.events.put(('cancelled', None))
            else:
                if isinstance(error, paramiko.AuthenticationException):
                    message = 'SSH authentication failed. Check the username and password.'
                elif isinstance(error, paramiko.BadHostKeyException):
                    message = 'SSH host key changed. Verify the robot and update your trusted SSH host key before retrying.'
                else:
                    message = str(error) or type(error).__name__
                if self.connection:
                    message = message.replace(self.connection.password, '[redacted]')
                self.events.put(('error', message))
        finally:
            if self.client:
                self.client.close()
            self.close_fast()
            self.client = None
            self.connection = None
