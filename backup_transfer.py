"""Create a robot archive and remove it only after an atomic local save."""
from dataclasses import dataclass, field
import os
from pathlib import Path
import shlex
import tempfile
import threading
import time
from uuid import uuid4

from log_transfer import DOWNLOAD_IDLE_TIMEOUT, LogJob


@dataclass
class BackupDestination:
    path: str | None = None
    answered: threading.Event = field(default_factory=threading.Event)


class BackupJob(LogJob):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.remote_dir = f'/tmp/ur-backup-{uuid4().hex}'
        self.remote_path = self.remote_dir + '/backup.tar'

    def check_cancel(self):
        if self.stopped.is_set():
            raise InterruptedError('Backup cancelled.')

    def choose_destination(self):
        question = BackupDestination()
        self.events.put(('backup_destination', question))
        while not question.answered.wait(.1):
            self.check_cancel()
        self.check_cancel()
        return Path(question.path) if question.path else None

    def create_archive(self, client):
        self.events.put(('status', 'Creating backup of /programs and /data/apps on robot…'))
        channel = client.get_transport().open_session(timeout=5)
        output = bytearray()
        try:
            channel.set_combine_stderr(True)
            channel.exec_command(f'tar -P -cf {shlex.quote(self.remote_path)} /programs /data/apps')
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
        total = 0
        started = last_report = time.monotonic()
        try:
            with tempfile.NamedTemporaryFile(prefix='.ur-backup-', suffix='.part',
                                             dir=destination.parent, delete=False) as local:
                temporary = Path(local.name)
                with sftp.open(self.remote_path, 'rb') as remote:
                    while True:
                        self.check_cancel()
                        chunk = remote.read(1024 * 1024)
                        self.check_cancel()
                        if not chunk:
                            break
                        local.write(chunk)
                        total += len(chunk)
                        if total > size:
                            raise OSError('Robot archive changed during download. Please retry.')
                        now = time.monotonic()
                        if now - last_report >= .2 or total == size:
                            rate = total / max(now - started, .001) / 1048576
                            self.events.put(('status', f'Downloading backup · {total / 1048576:.2f} / '
                                             f'{size / 1048576:.2f} MiB · {rate:.2f} MiB/s'))
                            last_report = now
                if total != size:
                    raise OSError('Incomplete backup download; robot archive retained.')
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
                sftp.mkdir(self.remote_dir, mode=0o700)
                created = True
                self.create_archive(client)
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
            if self.connection:
                message = message.replace(self.connection.password, '[redacted]')
            if self.client:
                self.client.close()
            self.client = None
            self.connection = None
            self.events.put(('backup_finished', message))
