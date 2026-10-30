"""Create a robot archive and remove it only after an atomic local save."""
from dataclasses import dataclass, field
import errno
import os
import posixpath
from pathlib import Path, PurePosixPath
import shlex
import stat
import tempfile
import threading
import time
from uuid import uuid4

from log_transfer import DOWNLOAD_IDLE_TIMEOUT, LogJob
from transfer_stream import copy_remote


@dataclass
class BackupDestination:
    path: str | None = None
    answered: threading.Event = field(default_factory=threading.Event)
    filename: str = 'backup.tar'


class BackupJob(LogJob):
    def __init__(self, sources=('/programs', '/data/apps'), *, full=False, flight_directory='/root/flightreports', **kwargs):
        super().__init__(**kwargs)
        self.full = full
        selected = tuple(sources)
        if not full and (not selected or any(path not in ('/programs', '/data/apps') for path in selected)):
            raise ValueError('Select programs, apps, or both for the backup.')
        self.sources = [(path, 'directory') for path in dict.fromkeys(selected)]
        if full:
            if not flight_directory.startswith('/') or any(c in flight_directory for c in '\x00\r\n'):
                raise ValueError('Enter an absolute robot flight-report folder.')
            flight_directory = posixpath.normpath(flight_directory)
            if not PurePosixPath(flight_directory).name:
                raise ValueError('Choose the flight-report folder, not the robot filesystem root.')
            logs = PurePosixPath(self.connection.path)
            if logs.suffix == '.bak':
                logs = logs.with_suffix('.txt')
            self.sources = [('/programs', 'directory'), ('/apps', 'directory'),
                            ('/data/apps', 'directory'), (str(logs), 'file'),
                            (str(logs.with_suffix('.bak')), 'file'),
                            (str(PurePosixPath(flight_directory)), 'directory')]
        self.remote_dir = f'/tmp/ur-backup-{uuid4().hex}'
        self.remote_path = self.remote_dir + '/backup.tar'
        self.skipped_sources = []

    def check_cancel(self):
        if self.stopped.is_set():
            raise InterruptedError('Backup cancelled.')

    def choose_destination(self):
        question = BackupDestination(filename='full-backup.tar' if self.full else 'backup.tar')
        self.events.put(('backup_destination', question))
        while not question.answered.wait(.1):
            self.check_cancel()
        self.check_cancel()
        return Path(question.path) if question.path else None

    def available_sources(self, sftp):
        """Skip absent sources, but never hide access, type or I/O errors."""
        sources = []
        for path, kind in self.sources:
            self.check_cancel()
            try:
                attributes = sftp.stat(path)
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
                self.skipped_sources.append(path)
                continue
            expected = stat.S_ISDIR if kind == 'directory' else stat.S_ISREG
            if not expected(attributes.st_mode):
                raise OSError(f'Backup source is not a {kind}: {path}')
            if path not in sources:
                sources.append(path)
        if not sources:
            raise OSError('No backup sources found; all requested paths are missing.' if self.full else
                          'No backup folders found: /programs and /data/apps are missing.')
        return sources

    def create_archive(self, client, sources):
        self.events.put(('status', f'Creating backup of {" and ".join(sources)} on robot…'))
        channel = client.get_transport().open_session(timeout=5)
        output = bytearray()
        try:
            channel.set_combine_stderr(True)
            arguments = ' '.join(shlex.quote(path) for path in sources)
            channel.exec_command(f'tar -P -cf {shlex.quote(self.remote_path)} {arguments}')
            while True:
                self.check_cancel()
                if channel.recv_ready():
                    output.extend(channel.recv(65536))
                    del output[:-4096]
                elif channel.exit_status_ready():
                    break
                else:
                    # tar can be silent for a long time on large robot archives.
                    self.stopped.wait(.1)
            status = channel.recv_exit_status()
            if status != 0:
                detail = output.decode('utf-8', errors='replace').strip()
                raise OSError(f'Robot tar exited with status {status}: {detail or "archive incomplete"}')
        finally:
            channel.close()

    def save_archive(self, sftp, destination, size):
        """Stage beside the destination so replacement stays on one filesystem."""
        temporary = None
        started = last_report = time.monotonic()

        def progress(total):
            nonlocal last_report
            now = time.monotonic()
            if now - last_report >= .2 or total == size:
                rate = total / max(now - started, .001) / 1048576
                eta = (size - total) / max(rate * 1048576, 1)
                self.events.put(('status', f'Downloading backup · {total / 1048576:.2f} / '
                                 f'{size / 1048576:.2f} MiB · {rate:.2f} MiB/s · {eta:.0f}s remaining'))
                last_report = now

        try:
            with tempfile.NamedTemporaryFile(prefix='.ur-backup-', suffix='.part',
                                             dir=destination.parent, delete=False) as local:
                temporary = Path(local.name)
                with sftp.open(self.remote_path, 'rb') as remote:
                    copy_remote(remote, local, size, check_cancel=self.check_cancel, progress=progress)
                if sftp.stat(self.remote_path).st_size != size:
                    raise OSError('Robot archive changed during download. Please retry.')
                local.flush()
                os.fsync(local.fileno())
            self.check_cancel()
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def run(self):
        created = False
        saved = None
        message = ''
        try:
            self.events.put(('status', 'Connecting to robot for backup…'))
            client = self.connect()
            with client.open_sftp() as sftp:
                sftp.get_channel().settimeout(DOWNLOAD_IDLE_TIMEOUT)
                self.check_cancel()
                sources = self.available_sources(sftp)
                sftp.mkdir(self.remote_dir, mode=0o700)
                created = True
                self.create_archive(client, sources)
                self.check_cancel()
                size = sftp.stat(self.remote_path).st_size
                if size <= 0:
                    raise OSError('Robot produced an empty archive.')
                self.events.put(('status', f'Backup ready · {size / 1048576:.2f} MiB · Choose where to save it'))
                destination = self.choose_destination()
                if destination is None:
                    raise InterruptedError('Backup save cancelled.')
                self.save_archive(sftp, destination, size)
                saved = destination
                self.events.put(('status', 'Backup saved · Removing temporary robot archive…'))
                sftp.remove(self.remote_path)
                sftp.rmdir(self.remote_dir)
                message = f'Saved backup to {saved} · Robot copy removed'
        except Exception as error:
            detail = ('Backup cancelled.' if self.stopped.is_set() else str(error) or type(error).__name__)
            message = f'Saved backup to {saved}, but robot cleanup failed: {detail}' if saved else f'Backup not saved · {detail}'
            if created:
                message += f' · Check robot archive at {self.remote_path} (may be incomplete if creation was interrupted).'
        finally:
            if self.skipped_sources:
                message += (' · Skipped missing sources: ' if self.full else ' · Skipped missing folders: ') + ', '.join(self.skipped_sources)
            if self.connection:
                message = message.replace(self.connection.password, '[redacted]')
            if self.client:
                self.client.close()
            self.client = None
            self.connection = None
            self.events.put(('backup_finished', message))
