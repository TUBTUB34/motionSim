import threading
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
import paramiko

from connection import make_settings
from transfer import InstallationTransfer, ConfirmHostKey
from tests.test_core import XML
from installation import MAX_FILE_SIZE

class TransferTests(unittest.TestCase):
    def setUp(self):
        self.settings = make_settings("127.0.0.1", "user", "secret", "custom.installation", "/programs/folder with spaces")

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
        data = InstallationTransfer().fetch(self.settings, threading.Event(), Mock())
        self.assertEqual(data.payload_mass, 2.5)
        self.assertFalse(Path(local_paths[0]).exists())
        ssh_class.return_value.connect.assert_called_once_with(
            "127.0.0.1", username="user", password="secret", timeout=5, banner_timeout=5,
            auth_timeout=5, look_for_keys=False, allow_agent=False)
        ssh_class.return_value.close.assert_called_once()

    @patch("transfer.paramiko.SSHClient")
    def test_bad_credentials_close_connection(self, ssh_class):
        ssh_class.return_value.connect.side_effect = paramiko.AuthenticationException("failed")
        with self.assertRaises(paramiko.AuthenticationException):
            InstallationTransfer().fetch(self.settings, threading.Event(), Mock())
        ssh_class.return_value.close.assert_called_once()

    @patch("transfer.SCPClient")
    @patch("transfer.paramiko.SSHClient")
    def test_oversized_file_is_cancelled(self, ssh_class, scp_class):
        def construct(*args, **kwargs):
            kwargs["progress"]("test", MAX_FILE_SIZE+1, 0)
        scp_class.side_effect = construct
        with self.assertRaises(ValueError):
            InstallationTransfer().fetch(self.settings, threading.Event(), Mock())
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

if __name__ == "__main__":
    unittest.main()
