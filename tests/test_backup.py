"""Offline backup lifecycle checks; never contact a robot."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from contextlib import nullcontext

from backup_transfer import BackupJob
from log_transfer import LogConnection


class BackupTests(unittest.TestCase):
    def make_job(self, destination, content=b'archive content'):
        client = MagicMock()
        channel = client.get_transport.return_value.open_session.return_value
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        sftp = client.open_sftp.return_value.__enter__.return_value
        sftp.stat.return_value.st_size = len(content)
        sftp.open.side_effect = lambda *args: io.BytesIO(content)
        job = BackupJob(connection=LogConnection('127.0.0.1', 'root', 'secret'),
                        client_factory=lambda: client)
        job.choose_destination = MagicMock(return_value=destination)
        return job, client, channel, sftp

    def message(self, job):
        return list(job.events.queue)[-1][1]

    def test_create_save_then_remove_remote(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'backup.tar'
            job, client, channel, sftp = self.make_job(path)
            sftp.remove.side_effect = lambda remote: self.assertEqual(path.read_bytes(), b'archive content')
            job.run()
            self.assertIn('tar -P -cf ', channel.exec_command.call_args.args[0])
            self.assertTrue(channel.exec_command.call_args.args[0].endswith(' /programs /data/apps'))
            sftp.mkdir.assert_called_once_with(job.remote_dir, mode=0o700)
            sftp.remove.assert_called_once_with(job.remote_path)
            sftp.rmdir.assert_called_once_with(job.remote_dir)
            self.assertIn('Saved backup', self.message(job))
            self.assertIsNone(job.connection)
            client.close.assert_called()
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_cancel_save_retains_robot_archive(self):
        job, _, _, sftp = self.make_job(None)
        job.run()
        sftp.remove.assert_not_called()
        self.assertIn('cancelled', self.message(job).lower())
        self.assertIn(job.remote_path, self.message(job))

    def test_tar_failure_never_prompts_or_deletes(self):
        job, _, channel, sftp = self.make_job(None)
        channel.recv_exit_status.return_value = 1
        job.run()
        job.choose_destination.assert_not_called()
        sftp.open.assert_not_called()
        sftp.remove.assert_not_called()
        self.assertIn('tar exited with status 1', self.message(job))

    def test_failed_download_preserves_existing_local_and_remote(self):
        for failure in ('short', 'timeout', 'cancel', 'replace', 'fsync'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / 'backup.tar'
                path.write_bytes(b'existing backup')
                job, _, _, sftp = self.make_job(path)
                if failure == 'short':
                    sftp.stat.return_value.st_size += 1
                elif failure == 'timeout':
                    sftp.open.side_effect = TimeoutError('secret stalled')
                elif failure == 'cancel':
                    def cancelled(*args):
                        job.cancel()
                        return io.BytesIO(b'archive content')
                    sftp.open.side_effect = cancelled
                failing_call = {'replace': 'os.replace', 'fsync': 'os.fsync'}.get(failure)
                context = patch('backup_transfer.' + failing_call, side_effect=OSError('disk error')) if failing_call else nullcontext()
                with context:
                    job.run()
                self.assertEqual(path.read_bytes(), b'existing backup')
                self.assertEqual(list(Path(folder).iterdir()), [path])
                sftp.remove.assert_not_called()
                self.assertIn(job.remote_path, self.message(job))
                self.assertNotIn('secret', self.message(job))

    def test_cleanup_failure_reports_saved_file_and_remote_path(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'backup.tar'
            job, _, _, sftp = self.make_job(path)
            sftp.remove.side_effect = OSError('permission denied')
            job.run()
            self.assertEqual(path.read_bytes(), b'archive content')
            self.assertIn('Saved backup', self.message(job))
            self.assertIn(job.remote_path, self.message(job))

    def test_cancel_before_connect_does_not_create_archive(self):
        job, client, _, sftp = self.make_job(None)
        job.cancel()
        job.run()
        client.connect.assert_not_called()
        sftp.mkdir.assert_not_called()

    def test_destination_prompt_waits_for_answer_or_cancellation(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / 'backup.tar'
                job, _, _, sftp = self.make_job(destination)
                del job.choose_destination
                job.start()
                try:
                    while True:
                        kind, question = job.events.get(timeout=2)
                        if kind == 'backup_destination':
                            break
                    if cancel:
                        job.cancel()
                    else:
                        question.path = str(destination)
                        question.answered.set()
                    job.thread.join(timeout=2)
                    self.assertFalse(job.thread.is_alive())
                    self.assertEqual(destination.exists(), not cancel)
                    self.assertEqual(sftp.remove.call_count, 0 if cancel else 1)
                finally:
                    job.cancel()
                    job.thread.join(timeout=2)

    def test_cancel_during_archive_creation_retains_remote_path(self):
        job, _, channel, sftp = self.make_job(None)
        channel.exit_status_ready.return_value = False
        with patch.object(job.stopped, 'wait', side_effect=lambda timeout: job.cancel()):
            job.run()
        channel.close.assert_called_once()
        job.choose_destination.assert_not_called()
        sftp.remove.assert_not_called()
        self.assertIn(job.remote_path, self.message(job))

    def test_archive_errors_are_drained_and_redacted(self):
        job, _, channel, sftp = self.make_job(None)
        channel.recv_ready.side_effect = [True, True, False]
        channel.recv.side_effect = [b'warning: ', b'secret permission denied']
        channel.recv_exit_status.return_value = 2
        job.run()
        self.assertIn('permission denied', self.message(job))
        self.assertNotIn('secret', self.message(job))
        sftp.remove.assert_not_called()

