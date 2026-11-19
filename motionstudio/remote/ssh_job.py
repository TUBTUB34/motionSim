"""The verified SSH connection and cancellable worker every robot download builds on; no credentials on disk."""
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
import queue
import threading
import time

import paramiko

from motionstudio.remote import fast_transfer
from motionstudio.remote.ssh_trust import (ConfirmHostKey, HostKeyQuestion, host_fingerprint, known_hosts_path,
                                           replace_host_key)
from motionstudio.remote.stream import SSH_TIMEOUT

# Every page starts from the robot's log path; downloads of other files set their own paths.
DEFAULT_LOG_PATH = "/root/log_history.txt"


@dataclass(frozen=True)
class SshConnection:
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
            raise ValueError("Enter an SSH username and password to connect to the robot.")
        if not self.path.startswith('/') or any(c in self.path for c in '\x00\r\n'):
            raise ValueError("Enter an absolute robot log path.")


class SshJob:
    """Shared SSH connect (host-key prompts, saved trust), AsyncSSH bulk reads and cancellation."""

    def __init__(self, *, path=None, connection=None, save_path=None, client_factory=paramiko.SSHClient, trust_path=None):
        self.path, self.connection = path, connection
        self.save_path = Path(save_path) if save_path else None
        self.client_factory = client_factory
        self.trust_path = Path(trust_path) if trust_path is not None else known_hosts_path()
        self.events = queue.Queue()
        self.stopped = threading.Event()
        self.client = None
        self.host_key = None
        self.host_port = 22
        # Bulk downloads use AsyncSSH when it can connect; paramiko otherwise.
        self.use_fast = fast_transfer.available()
        self.transfer_engine = 'paramiko'
        self._fast = {}
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def cancel(self):
        self.stopped.set()
        if self.client:
            self.client.close()
        self.close_fast()

    def close_fast(self):
        """Close AsyncSSH sessions; safe from any thread and when none were opened."""
        for session in list(self._fast.values()):
            if session is not None:
                session.close()
        self._fast = {}

    @property
    def engine_label(self):
        """Short progress-text note, so the user can see which transfer engine is in use."""
        return 'via AsyncSSH' if self.transfer_engine == 'AsyncSSH' else 'via paramiko'

    def open_bulk(self, sftp, path, *, compress=False, size=None):
        """Open ``path`` for a large ``copy_remote`` download.

AsyncSSH keeps far more reads in flight than paramiko, and ``compress`` adds
zlib for text such as logs and uncompressed tar backups. Without AsyncSSH, or
when it cannot connect, this is the paramiko file it always was.
"""
        session = self._fast_session(compress)
        return session.open(path, size) if session is not None else sftp.open(path, 'rb')

    def _fast_session(self, compress):
        if not self.use_fast or not isinstance(self.host_key, paramiko.PKey) or self.connection is None:
            return None
        if compress not in self._fast:
            settings = self.connection
            try:
                session = fast_transfer.FastSFTP.connect(settings.ip, settings.username, settings.password,
                                                         self.host_key, port=self.host_port, compress=compress,
                                                         stop=self.stopped)
            except InterruptedError:
                raise
            except Exception as error:
                # Speed only: keep the verified paramiko path and stop trying.
                self.use_fast = False
                detail = str(error) or type(error).__name__
                if settings.password:
                    detail = detail.replace(settings.password, '[redacted]')
                self.transfer_engine = f'paramiko (AsyncSSH unavailable: {detail})'
                self.events.put(('status', f'Fast transfer unavailable ({detail}); continuing with the standard SSH download.'))
                return None
            self._fast[compress] = session
            self.transfer_engine = 'AsyncSSH'
            if self.stopped.is_set():
                self.close_fast()
        self.check_cancel()
        return self._fast[compress]

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

    def connect(self):
        """Open a verified SSH connection shared by logs and robot backups."""
        settings = self.connection
        self.check_cancel()
        client = self.client = self.client_factory()
        self.check_cancel()
        if self.trust_path.exists():
            client.load_system_host_keys(str(self.trust_path))
        policy = ConfirmHostKey(self.confirm_host, self.trust_path)
        client.set_missing_host_key_policy(policy)
        connect_args = dict(username=settings.username, password=settings.password,
                       timeout=SSH_TIMEOUT, banner_timeout=SSH_TIMEOUT, auth_timeout=SSH_TIMEOUT,
                       channel_timeout=SSH_TIMEOUT, look_for_keys=False, allow_agent=False)
        try:
            client.connect(settings.ip, **connect_args)
            self.check_cancel()
            policy.save()
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
        transport = client.get_transport()
        if transport is not None:
            # A second, faster session must reach the same endpoint with the same identity.
            self.host_key = transport.get_remote_server_key()
            if isinstance(self.host_key, paramiko.PKey):
                self.host_port = transport.getpeername()[1]
        return client
