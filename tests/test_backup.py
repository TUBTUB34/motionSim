"""Offline backup lifecycle checks; never contact a robot."""
import io
import errno
from pathlib import Path
import stat
import shlex
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from contextlib import nullcontext

from backup_transfer import BackupJob
from log_transfer import LogConnection


class BackupTests(unittest.TestCase):
    def make_job(self, destination, content=b'archive content', *, full=False, flight_directory='/root/flightreports'):
        client = MagicMock()
        channel = client.get_transport.return_value.open_session.return_value
        channel.recv_ready.return_value = False
        channel.exit_status_ready.return_value = True
        channel.recv_exit_status.return_value = 0
        sftp = client.open_sftp.return_value.__enter__.return_value
        sftp.stat.return_value.st_size = len(content)
        sftp.stat.return_value.st_mode = stat.S_IFDIR | 0o755
        sftp.open.side_effect = lambda *args: io.BytesIO(content)
        job = BackupJob(connection=LogConnection('127.0.0.1', 'root', 'secret'),
                        client_factory=lambda: client, full=full, flight_directory=flight_directory)
        if full:
            directories = {path for path, kind in job.sources if kind == 'directory'}
            sftp.stat.side_effect = lambda path: SimpleNamespace(st_size=len(content),
                st_mode=(stat.S_IFDIR if path in directories else stat.S_IFREG) | 0o644)
        job.choose_destination = MagicMock(return_value=destination)
        return job, client, channel, sftp

    def test_full_backup_includes_all_sources_before_local_save_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'full-backup.tar'
            job, _, channel, sftp = self.make_job(destination, full=True)
            sftp.remove.side_effect = lambda _: self.assertEqual(destination.read_bytes(), b'archive content')
            job.run()
            self.assertEqual(shlex.split(channel.exec_command.call_args.args[0]),
                ['tar', '-P', '-cf', job.remote_path, '/programs', '/apps', '/data/apps',
                 '/root/log_history.txt', '/root/log_history.bak', '/root/flightreports'])
            sftp.remove.assert_called_once_with(job.remote_path)
            sftp.rmdir.assert_called_once_with(job.remote_dir)
            self.assertIn('Robot copy removed', self.message(job))

    def test_full_backup_skips_missing_file_and_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'full-backup.tar'
            job, _, channel, sftp = self.make_job(destination, full=True)
            available = sftp.stat.side_effect
            missing = {'/apps', '/root/log_history.bak'}
            def attributes(path):
                if path in missing:
                    raise FileNotFoundError(errno.ENOENT, 'Not found', path)
                return available(path)
            sftp.stat.side_effect = attributes
            job.run()
            self.assertTrue(destination.exists())
            arguments = shlex.split(channel.exec_command.call_args.args[0])
            self.assertTrue(missing.isdisjoint(arguments))
            self.assertIn('/root/flightreports', arguments)
            self.assertIn('Skipped missing sources:', self.message(job))
            for source in missing:
                self.assertIn(source, self.message(job))

    def test_full_backup_download_failure_keeps_remote_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'full-backup.tar'
            destination.write_bytes(b'previous backup')
            job, _, _, sftp = self.make_job(destination, full=True)
            sftp.open.side_effect = TimeoutError('Download stalled')
            job.run()
            self.assertEqual(destination.read_bytes(), b'previous backup')
            sftp.remove.assert_not_called()
            sftp.rmdir.assert_not_called()
            self.assertIn(job.remote_path, self.message(job))

    def test_full_backup_checks_file_types_and_quotes_flight_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'full-backup.tar'
            flight_folder = "/root/flight reports; echo unexpected"
            job, _, channel, sftp = self.make_job(destination, full=True, flight_directory=flight_folder)
            job.run()
            self.assertEqual(shlex.split(channel.exec_command.call_args.args[0])[-1], flight_folder)
            job, _, channel, sftp = self.make_job(destination, full=True)
            sftp.stat.side_effect = None
            sftp.stat.return_value.st_mode = stat.S_IFDIR
            job.run()
            channel.exec_command.assert_not_called()
            self.assertIn('not a file: /root/log_history.txt', self.message(job))

    def test_full_backup_custom_log_folder_keeps_both_files(self):
        connection = LogConnection('127.0.0.1', 'root', 'secret', '/data/logs/log_history.bak')
        job = BackupJob(connection=connection, full=True, flight_directory='/data/reports')
        self.assertIn(('/data/logs/log_history.txt', 'file'), job.sources)
        self.assertIn(('/data/logs/log_history.bak', 'file'), job.sources)
        self.assertIn(('/data/reports', 'directory'), job.sources)
        for folder in ('/', '//', '/root/..', 'relative/path', '/root/reports\n'):
            with self.subTest(folder=folder), self.assertRaises(ValueError):
                BackupJob(connection=connection, full=True, flight_directory=folder)

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

    def test_missing_source_is_skipped_and_remaining_directory_is_saved(self):
        for missing in ('/programs', '/data/apps'):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                destination = Path(folder) / 'backup.tar'
                job, _, channel, sftp = self.make_job(destination)
                attributes = sftp.stat.return_value

                def source_stat(path):
                    if path == missing:
                        raise FileNotFoundError(errno.ENOENT, 'No such file', path)
                    return attributes

                sftp.stat.side_effect = source_stat
                job.run()
                self.assertEqual(destination.read_bytes(), b'archive content')
                command = channel.exec_command.call_args.args[0]
                self.assertNotIn(missing, command)
                self.assertIn('/data/apps' if missing == '/programs' else '/programs', command)
                self.assertIn('Skipped missing folders: ' + missing, self.message(job))
                sftp.remove.assert_called_once_with(job.remote_path)

    def test_no_sources_does_not_create_archive_or_prompt(self):
        job, _, channel, sftp = self.make_job(None)
        sftp.stat.side_effect = FileNotFoundError(errno.ENOENT, 'No such file')
        job.run()
        sftp.mkdir.assert_not_called()
        channel.exec_command.assert_not_called()
        job.choose_destination.assert_not_called()
        self.assertIn('No backup folders found', self.message(job))
        self.assertNotIn('Check robot archive', self.message(job))

    def test_source_access_errors_are_not_treated_as_missing(self):
        for error in (PermissionError(errno.EACCES, 'Permission denied'),
                      OSError(errno.EIO, 'Input/output error')):
            with self.subTest(error=error):
                job, _, channel, sftp = self.make_job(None)
                sftp.stat.side_effect = error
                job.run()
                sftp.mkdir.assert_not_called()
                channel.exec_command.assert_not_called()
                job.choose_destination.assert_not_called()
                self.assertIn(error.strerror, self.message(job))
                self.assertNotIn('Skipped missing', self.message(job))

    def test_non_directory_source_is_reported(self):
        job, _, channel, sftp = self.make_job(None)
        sftp.stat.return_value.st_mode = stat.S_IFREG | 0o644
        job.run()
        channel.exec_command.assert_not_called()
        self.assertIn('not a directory: /programs', self.message(job))

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

    def test_archive_size_change_at_final_stat_preserves_previous_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'backup.tar'
            path.write_bytes(b'previous backup')
            job, _, _, sftp = self.make_job(path)
            sftp.stat.side_effect = [SimpleNamespace(st_mode=stat.S_IFDIR),
                                     SimpleNamespace(st_mode=stat.S_IFDIR),
                                     SimpleNamespace(st_size=15), SimpleNamespace(st_size=16)]
            job.run()
            self.assertEqual(path.read_bytes(), b'previous backup')
            self.assertEqual(list(Path(folder).iterdir()), [path])
            sftp.remove.assert_not_called()
            self.assertIn('changed during download', self.message(job))

    def test_growing_archive_is_rejected_before_replacement(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'backup.tar'
            job, _, _, sftp = self.make_job(path)
            sftp.stat.return_value.st_size -= 1
            job.run()
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(folder).iterdir()), [])
            sftp.remove.assert_not_called()
            self.assertIn('changed during download', self.message(job))

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

