import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import paramiko

from connection import make_settings
from transfer import InstallationTransfer, ConfirmHostKey
from tests.test_core import XML
from installation import MAX_FILE_SIZE
from transfer_stream import DOWNLOAD_IDLE_TIMEOUT, SSH_TIMEOUT, STREAM_BUFFER_SIZE

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings("127.0.0.1", "user", "secret", "custom.installation", "/programs/folder with spaces")
        # Never touch the user's real ~/.ssh/known_hosts.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.trust = Path(folder.name) / "known_hosts"

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_scp_download_path_and_cleanup(self, ssh_class, scp_class):
        scp = scp_class.return_value.__enter__.return_value
        local_paths = []
        def download(remote, local):
            self.assertEqual(remote, self.settings.remote_path)
            local_paths.append(local)
            Path(local).write_bytes(XML)
        scp.get.side_effect = download
        data = InstallationTransfer(self.trust).fetch(self.settings, threading.Event(), Mock())
        self.assertEqual(data.payload_mass, 2.5)
        self.assertFalse(Path(local_paths[0]).exists())
        ssh_class.return_value.connect.assert_called_once_with(
            "127.0.0.1", username="user", password="secret", timeout=SSH_TIMEOUT, banner_timeout=SSH_TIMEOUT,
            auth_timeout=SSH_TIMEOUT, look_for_keys=False, allow_agent=False)
        self.assertGreaterEqual(SSH_TIMEOUT, 30)
        ssh_class.return_value.close.assert_called_once()
        self.assertEqual(scp_class.call_args.kwargs['socket_timeout'], DOWNLOAD_IDLE_TIMEOUT)
        self.assertEqual(scp_class.call_args.kwargs['buff_size'], STREAM_BUFFER_SIZE)

    @patch("transfer.paramiko.SSHClient")
    def test_bad_credentials_close_connection(self, ssh_class):
        ssh_class.return_value.connect.side_effect = paramiko.AuthenticationException("failed")
        with self.assertRaises(paramiko.AuthenticationException):
            InstallationTransfer(self.trust).fetch(self.settings, threading.Event(), Mock())
        ssh_class.return_value.close.assert_called_once()

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_oversized_file_is_cancelled(self, ssh_class, scp_class):
        def construct(*args, **kwargs):
            kwargs["progress"]("test", MAX_FILE_SIZE+1, 0)
        scp_class.side_effect = construct
        with self.assertRaises(ValueError):
            InstallationTransfer(self.trust).fetch(self.settings, threading.Event(), Mock())
        ssh_class.return_value.close.assert_called_once()

    def test_host_key_requires_explicit_acceptance(self):
        key = Mock()
        key.asbytes.return_value = b"host key"
        key.get_name.return_value = "ssh-ed25519"
        client = Mock()
        confirm = Mock(return_value=False)
        with self.assertRaises(paramiko.SSHException):
            ConfirmHostKey(confirm).missing_host_key(client, "robot", key)
        client.get_host_keys.assert_not_called()
        confirm.return_value = True
        ConfirmHostKey(confirm).missing_host_key(client, "robot", key)
        client.get_host_keys.return_value.add.assert_called_once_with("robot", "ssh-ed25519", key)
        self.assertTrue(confirm.call_args.args[1].startswith("SHA256:"))

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_changed_key_retry_pins_accepted_key(self, ssh_class, scp_class):
        old, new = paramiko.RSAKey.generate(1024), paramiko.RSAKey.generate(1024)
        first, retry = Mock(), Mock()
        ssh_class.side_effect = [first, retry]
        first.connect.side_effect = paramiko.BadHostKeyException(self.settings.ip, new, old)
        scp_class.return_value.__enter__.return_value.get.side_effect = (
            lambda remote, local: Path(local).write_bytes(XML))
        confirm = Mock(return_value=True)
        data = InstallationTransfer(self.trust).fetch(self.settings, threading.Event(), confirm)
        self.assertEqual(data.payload_mass, 2.5)
        self.assertIn("Changed host key", confirm.call_args.args[1])
        retry.load_system_host_keys.assert_not_called()
        retry.get_host_keys.return_value.add.assert_called_once_with(
            self.settings.ip, "ssh-rsa", new)
        retry.connect.assert_called_once()
        self.assertTrue(confirm.call_args.kwargs.get("replacement"))
        self.assertIn(self.settings.ip, self.trust.read_text())  # approved replacement is remembered
        first.close.assert_called_once()
        retry.close.assert_called_once()

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_changed_key_rejection_does_not_download(self, ssh_class, scp_class):
        old, new = Mock(), Mock()
        old.asbytes.return_value, new.asbytes.return_value = b"old", b"new"
        ssh_class.return_value.connect.side_effect = paramiko.BadHostKeyException(
            self.settings.ip, new, old)
        with self.assertRaisesRegex(paramiko.SSHException, "not accepted"):
            InstallationTransfer(self.trust).fetch(self.settings, threading.Event(), Mock(return_value=False))
        ssh_class.assert_called_once()
        scp_class.assert_not_called()

class PersistentHostKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(1024)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.trust = Path(folder.name) / ".ssh" / "known_hosts"

    def test_accepted_key_is_saved_only_by_save(self):
        policy = ConfirmHostKey(Mock(return_value=True), self.trust)
        policy.missing_host_key(Mock(), "192.0.2.5", self.key)
        self.assertFalse(self.trust.exists())  # nothing written before the connection succeeds
        policy.save()
        self.assertEqual(paramiko.HostKeys(str(self.trust))["192.0.2.5"]["ssh-rsa"], self.key)
        policy.save()  # idempotent
        self.assertEqual(self.trust.read_text().count("192.0.2.5"), 1)

    def test_rejected_key_is_not_saved(self):
        policy = ConfirmHostKey(Mock(return_value=False), self.trust)
        with self.assertRaises(paramiko.SSHException):
            policy.missing_host_key(Mock(), "192.0.2.5", self.key)
        policy.save()
        self.assertFalse(self.trust.exists())

    @patch("transfer.paramiko.SSHClient")
    def test_failed_login_does_not_save_key(self, ssh_class):
        settings = make_settings("192.0.2.5", "user", "secret", "default")
        def connect(*_args, **_kwargs):
            ssh_class.return_value.set_missing_host_key_policy.call_args.args[0].missing_host_key(
                ssh_class.return_value, "192.0.2.5", self.key)
            raise paramiko.AuthenticationException("failed")
        ssh_class.return_value.connect.side_effect = connect
        with self.assertRaises(paramiko.AuthenticationException):
            InstallationTransfer(self.trust).fetch(settings, threading.Event(), Mock(return_value=True))
        self.assertFalse(self.trust.exists())

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_saved_key_is_loaded_on_next_connection(self, ssh_class, scp_class):
        self.trust.parent.mkdir()
        self.trust.write_text(f"192.0.2.5 ssh-rsa {self.key.get_base64()}\n")
        scp_class.return_value.__enter__.return_value.get.side_effect = (
            lambda remote, local: Path(local).write_bytes(XML))
        settings = make_settings("192.0.2.5", "user", "secret", "default")
        InstallationTransfer(self.trust).fetch(settings, threading.Event(), Mock(return_value=False))
        ssh_class.return_value.load_system_host_keys.assert_called_once_with(str(self.trust))


if __name__ == "__main__":
    unittest.main()
