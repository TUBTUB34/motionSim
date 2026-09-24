"""SCP download of one installation file; never modifies the controller."""
import base64
import hashlib
from pathlib import Path
import tempfile
import time

import paramiko
from scp import SCPClient
from installation import MAX_FILE_SIZE, decode_installation

class ConfirmHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, confirm):
        self.confirm = confirm

    def missing_host_key(self, client, hostname, key):
        fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if not self.confirm(hostname, fingerprint):
            raise paramiko.SSHException("SSH host key was not accepted.")
        # Trust only this connection. Do not persist keys or credentials.
        client.get_host_keys().add(hostname, key.get_name(), key)

class InstallationTransfer:
    def __init__(self):
        self.client = None

    def close(self):
        client = self.client
        if client:
            client.close()

    def fetch(self, settings, stop, confirm_host):
        client = paramiko.SSHClient()
        self.client = client
        try:
            client.load_system_host_keys()
            client.set_missing_host_key_policy(ConfirmHostKey(confirm_host))
            if stop.is_set():
                raise InterruptedError("Download cancelled.")
            client.connect(settings.ip, username=settings.username, password=settings.password,
                           timeout=5, banner_timeout=5, auth_timeout=5,
                           look_for_keys=False, allow_agent=False)
            deadline = time.monotonic() + 30

            def progress(_filename, size, _sent):
                if stop.is_set():
                    raise InterruptedError("Download cancelled.")
                if size > MAX_FILE_SIZE:
                    raise ValueError("Installation file exceeds 16 MiB.")
                if time.monotonic() > deadline:
                    raise TimeoutError("Installation download timed out.")

            with tempfile.TemporaryDirectory(prefix="ur-installation-") as directory:
                local_path = Path(directory) / "robot.installation"
                with SCPClient(client.get_transport(), socket_timeout=5, progress=progress) as scp:
                    scp.get(settings.remote_path, str(local_path))
                if stop.is_set():
                    raise InterruptedError("Download cancelled.")
                return decode_installation(local_path.read_bytes())
        finally:
            client.close()
            self.client = None
