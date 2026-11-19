"""SCP download of one installation file; never modifies the controller."""
import base64
import hashlib
from pathlib import Path
import tempfile

import paramiko
from scp import SCPClient
from motionstudio.core.installation import MAX_FILE_SIZE, decode_installation
from motionstudio.remote.ssh_trust import known_hosts_path, replace_host_key
from motionstudio.remote.stream import DOWNLOAD_IDLE_TIMEOUT, SSH_TIMEOUT, STREAM_BUFFER_SIZE

def host_fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")

class ConfirmHostKey(paramiko.MissingHostKeyPolicy):
    """Ask once per new robot; ``save`` persists the approved key after a successful connect."""
    def __init__(self, confirm, trust_path=None):
        self.confirm = confirm
        self.trust_path = trust_path
        self.accepted = None

    def missing_host_key(self, client, hostname, key):
        fingerprint = host_fingerprint(key)
        if not self.confirm(hostname, fingerprint):
            raise paramiko.SSHException("SSH host key was not accepted.")
        client.get_host_keys().add(hostname, key.get_name(), key)
        self.accepted = (hostname, key)

    def save(self):
        """Remember the approved robot identity so later connections do not ask again.

Credentials are never stored. A changed key is still reported on every
connection until the user approves the replacement.
"""
        if self.accepted is None or self.trust_path is None:
            return
        try:
            replace_host_key(self.trust_path, *self.accepted)
        except OSError as error:
            raise OSError(f"Could not save the robot SSH key to {self.trust_path}: {error}") from None
        self.accepted = None

class InstallationTransfer:
    def __init__(self, trust_path=None):
        self.client = None
        self.trust_path = Path(trust_path) if trust_path is not None else known_hosts_path()

    def close(self):
        client = self.client
        if client:
            client.close()

    def fetch(self, settings, stop, confirm_host):
        client = paramiko.SSHClient()
        self.client = client
        try:
            if self.trust_path.exists():
                client.load_system_host_keys(str(self.trust_path))
            policy = ConfirmHostKey(confirm_host, self.trust_path)
            client.set_missing_host_key_policy(policy)
            if stop.is_set():
                raise InterruptedError("Download cancelled.")
            connect_args = dict(username=settings.username, password=settings.password,
                                timeout=SSH_TIMEOUT, banner_timeout=SSH_TIMEOUT, auth_timeout=SSH_TIMEOUT,
                                look_for_keys=False, allow_agent=False)
            try:
                client.connect(settings.ip, **connect_args)
                policy.save()
            except paramiko.BadHostKeyException as error:
                client.close()
                details = (f"Changed host key — a different robot may be using this IP.\n\n"
                           f"Saved: {host_fingerprint(error.expected_key)}\n"
                           f"Received: {host_fingerprint(error.key)}")
                if not confirm_host(settings.ip, details, replacement=True):
                    raise paramiko.SSHException("Changed SSH host key was not accepted.") from None
                if stop.is_set():
                    raise InterruptedError("Download cancelled.")
                # Clear stale trust only for this retry; pin the explicitly accepted key.
                client = paramiko.SSHClient()
                self.client = client
                client.get_host_keys().add(settings.ip, error.key.get_name(), error.key)
                client.connect(settings.ip, **connect_args)
                try:
                    replace_host_key(self.trust_path, settings.ip, error.key)
                except OSError as save_error:
                    raise OSError(f"Could not update SSH trust file {self.trust_path}: {save_error}") from None
            def progress(_filename, size, _sent):
                if stop.is_set():
                    raise InterruptedError("Download cancelled.")
                if size > MAX_FILE_SIZE:
                    raise ValueError("Installation file exceeds 16 MiB.")

            with tempfile.TemporaryDirectory(prefix="ur-installation-") as directory:
                local_path = Path(directory) / "robot.installation"
                # SCP is already a stream (no read-request RTT per packet).
                # Batch local receives and time out only if incoming data stalls.
                with SCPClient(client.get_transport(), socket_timeout=DOWNLOAD_IDLE_TIMEOUT,
                               buff_size=STREAM_BUFFER_SIZE, progress=progress) as scp:
                    scp.get(settings.remote_path, str(local_path))
                if stop.is_set():
                    raise InterruptedError("Download cancelled.")
                return decode_installation(local_path.read_bytes())
        finally:
            client.close()
            self.client = None
