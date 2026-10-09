import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, Mock, patch

import paramiko

from log_transfer import LogConnection, LogJob
from ssh_trust import known_hosts_path, replace_host_key


class SSHTrustTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = paramiko.RSAKey.generate(1024)
        cls.new = paramiko.RSAKey.generate(1024)

    def test_replace_plain_hashed_and_aliases_preserves_other_hosts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / '.ssh' / 'known_hosts'
            path.parent.mkdir()
            key = f'{self.old.get_name()} {self.old.get_base64()}'
            hashed = paramiko.HostKeys.hash_host('127.0.0.1')
            other = f'other-host {key}\n'
            path.write_text(f'# keep comment\n127.0.0.1,robot-alias {key}\n{hashed} {key}\n' + other)
            replace_host_key(path, '127.0.0.1', self.new)
            saved = paramiko.HostKeys(str(path))
            self.assertEqual(saved['127.0.0.1']['ssh-rsa'], self.new)
            self.assertEqual(saved['robot-alias']['ssh-rsa'], self.old)
            self.assertEqual(saved['other-host']['ssh-rsa'], self.old)
            self.assertNotIn(hashed, path.read_text())
            self.assertIn('# keep comment\n', path.read_text())
            self.assertIn(other, path.read_text())

    def test_platform_home_path_and_atomic_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            # No OS-specific shell utilities or hard-coded /home paths.
            for home in (Path(folder) / 'Users' / 'Operator', Path(folder) / 'home' / 'operator'):
                with patch('ssh_trust.Path.home', return_value=home):
                    path = known_hosts_path()
                self.assertEqual(path, home / '.ssh' / 'known_hosts')
                replace_host_key(path, '127.0.0.1', self.old)
                original = path.read_bytes()
                with patch('ssh_trust.os.replace', side_effect=PermissionError(errno.EACCES, 'denied')):
                    with self.assertRaises(PermissionError):
                        replace_host_key(path, '127.0.0.1', self.new)
                self.assertEqual(path.read_bytes(), original)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_changed_host_accept_reject_and_second_change(self):
        for accept, retry_failure in ((False, False), (True, False), (True, True)):
            with self.subTest(accept=accept, retry_failure=retry_failure), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'known_hosts'
                replace_host_key(path, '127.0.0.1', self.old)
                original = path.read_bytes()
                first, retry = MagicMock(), MagicMock()
                first.connect.side_effect = paramiko.BadHostKeyException('127.0.0.1', self.new, self.old)
                if retry_failure:
                    retry.connect.side_effect = paramiko.BadHostKeyException('127.0.0.1', self.old, self.new)
                factory = Mock(side_effect=[first, retry])
                job = LogJob(connection=LogConnection('127.0.0.1', 'root', 'secret'),
                             client_factory=factory, trust_path=path)
                job.confirm_host = Mock(return_value=accept)
                # Stop after trust handling, without performing any SFTP transfer.
                retry.open_sftp.side_effect = RuntimeError('end of connection test')
                job.run()
                self.assertIn('remove the old saved key', job.confirm_host.call_args.args[1])
                self.assertTrue(job.confirm_host.call_args.kwargs['replacement'])
                first.load_system_host_keys.assert_called_once_with(str(path))
                if accept:
                    retry.get_host_keys.return_value.add.assert_called_once_with('127.0.0.1', 'ssh-rsa', self.new)
                    retry.load_system_host_keys.assert_not_called()
                    retry.close.assert_called_once()
                else:
                    self.assertEqual(factory.call_count, 1)
                if accept and not retry_failure:
                    self.assertEqual(paramiko.HostKeys(str(path))['127.0.0.1']['ssh-rsa'], self.new)
                else:
                    self.assertEqual(path.read_bytes(), original)

    def test_popup_question_marks_replacement(self):
        import threading
        job = LogJob()
        result = []
        worker = threading.Thread(target=lambda: result.append(job.confirm_host('127.0.0.1', 'old / new', replacement=True)))
        worker.start()
        kind, question = job.events.get(timeout=2)
        self.assertEqual(kind, 'host_key')
        self.assertTrue(question.replacement)
        question.accepted = True
        question.answered.set()
        worker.join(2)
        self.assertEqual(result, [True])
