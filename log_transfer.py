"""Read-only SSH log transfer and cancellable worker; no credentials on disk."""
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path, PurePosixPath
import errno
import queue
import tempfile
import threading
import time

import paramiko

from log_data import MAX_LOG_BYTES, load_log, load_logs
from session import HostKeyQuestion
from transfer import ConfirmHostKey, host_fingerprint
from ssh_trust import known_hosts_path, replace_host_key

DEFAULT_LOG_PATH = "/root/log_history.txt"
DOWNLOAD_IDLE_TIMEOUT = 60


@dataclass(frozen=True)
class LogConnection:
    ip: str
    username: str
    password: str = field(repr=False)
    path: str = DEFAULT_LOG_PATH

    def __post_init__(self):
        try:
            ip_address(self.ip)
        except ValueError:
            raise ValueError("Enter a valid robot IP address.") from None
        if not self.username.strip() or not self.password:
            raise ValueError("Enter an SSH username and password to fetch logs.")
        if not self.path.startswith('/') or any(c in self.path for c in '\x00\r\n'):
            raise ValueError("Enter an absolute robot log path.")


class LogJob:
    def __init__(self, *, path=None, connection=None, client_factory=paramiko.SSHClient, trust_path=None):
        self.path, self.connection = path, connection
        self.client_factory = client_factory
        self.trust_path = Path(trust_path) if trust_path is not None else known_hosts_path()
        self.events = queue.Queue()
        self.stopped = threading.Event()
        self.client = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.stopped.set()
        if self.client:
            self.client.close()

    def check_cancel(self):
        if self.stopped.is_set():
            raise InterruptedError("Log operation cancelled.")

    def confirm_host(self, hostname, fingerprint, *, replacement=False):
        question = HostKeyQuestion(hostname, fingerprint, replacement=replacement)
        self.events.put(('host_key', question))
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            self.check_cancel()
            if question.answered.wait(.1):
                return question.accepted
        return False

    def download(self, destination):
        settings = self.connection
        self.check_cancel()
        client = self.client = self.client_factory()
        self.check_cancel()
        if self.trust_path.exists():
            client.load_system_host_keys(str(self.trust_path))
        client.set_missing_host_key_policy(ConfirmHostKey(self.confirm_host))
        connect_args = dict(username=settings.username, password=settings.password,
                       timeout=5, banner_timeout=5, auth_timeout=5, channel_timeout=5,
                       look_for_keys=False, allow_agent=False)
        try:
            client.connect(settings.ip, **connect_args)
        except paramiko.BadHostKeyException as error:
            client.close()
            details = (f'A different robot may now be using {settings.ip}.\n\n'
                       f'Old fingerprint: {host_fingerprint(error.expected_key)}\n'
                       f'New fingerprint: {host_fingerprint(error.key)}\n\n'
                       'Continuing will remove the old saved key for this IP and replace it '
                       'with the new key after the connection succeeds. Other hosts are kept.\n\n'
                       f'Trust file: {self.trust_path}\n\n'
                       'Only continue if you expect this robot change.')
            if not self.confirm_host(settings.ip, details, replacement=True):
                raise paramiko.SSHException('Host key replacement cancelled. Saved key unchanged.') from None
            self.check_cancel()
            client = self.client = self.client_factory()
            # Pin the approved identity. A second key change must not be auto-accepted.
            client.get_host_keys().add(settings.ip, error.key.get_name(), error.key)
            client.connect(settings.ip, **connect_args)
            self.check_cancel()
            try:
                replace_host_key(self.trust_path, settings.ip, error.key)
            except OSError as save_error:
                raise OSError(f'Could not update SSH trust file {self.trust_path}: {save_error}') from None
        self.check_cancel()
        with client.open_sftp() as sftp:
            # Channel reads time out only while waiting for incoming data. Do not
            # impose a total duration limit on an actively progressing download.
            sftp.get_channel().settimeout(DOWNLOAD_IDLE_TIMEOUT)
            current = PurePosixPath(settings.path)
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
                                         f'{total / 1048576:.2f} / {size / 1048576:.2f} MiB · {speed}'))
                        last_progress, last_bytes = now, total

                    report_progress(last_progress)
                    with sftp.open(remote_path, 'rb') as remote, local_path.open('wb') as local:
                        while True:
                            self.check_cancel()
                            try:
                                chunk = remote.read(65536)
                            except TimeoutError:
                                raise TimeoutError(f'Download stalled: no data received for '
                                                   f'{DOWNLOAD_IDLE_TIMEOUT} seconds. Try again.') from None
                            self.check_cancel()
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > MAX_LOG_BYTES:
                                raise ValueError("Robot log exceeds 64 MiB per file.")
                            local.write(chunk)
                            now = time.monotonic()
                            if now - last_progress >= .2:
                                report_progress(now)
                    report_progress(time.monotonic())
                    paths.append(local_path)
                except OSError as error:
                    if remote_path != str(current) and error.errno == errno.ENOENT:
                        missing_backup = True
                    else:
                        raise
            return paths, missing_backup

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
            self.client = None
            self.connection = None
