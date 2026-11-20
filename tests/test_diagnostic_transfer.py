"""Diagnostic collection uses synthetic, read-only SSH providers."""
import errno
import io
import json
from pathlib import Path
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from motionstudio.diagnostics.transfer import DiagnosticSourceJob
from motionstudio.remote.ssh_job import SshConnection
from tests.test_flight_transfer import Remote, SFTP, Key, recording


CSV = b'Timestamp [s],Actual position j0 [rad]\n1,0.1\n1.002,0.2\n'
REMOTE_PATHS = {'log_history.txt': '/root/log_history.txt', 'log_history.bak': '/root/log_history.bak',
                'recording.zip': '/root/flightreports/recording.zip'}


def robot_tar(files, mtime, names=REMOTE_PATHS):
    """A tar as `tar -P -cf` writes it on the robot: absolute member names."""
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(names[name])
            info.size, info.mtime = len(data), mtime
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


class DiagnosticTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.remote = Remote()
        self.remote.files.update({'log_history.txt': b'current log\n',
                                  'log_history.bak': b'previous log\n'})

    def make_job(self, **kwargs):
        return DiagnosticSourceJob(connection=SshConnection('192.0.2.1', 'root', 'private-password'),
                destination=self.destination, client_factory=self.remote.factory,
                trust_path=self.destination / 'known_hosts', **kwargs)

    def events(self, job, kind):
        return [payload for event, payload in list(job.events.queue) if event == kind]

    def manifest(self, job):
        return json.loads((job.snapshot_path / 'diagnostic_manifest.json').read_text())

    def test_combined_snapshot_csv_zip_identity_and_read_only_transport(self):
        self.remote.files['older.csv'] = CSV
        job = self.make_job()
        job.run()
        result, = self.events(job, 'collected')
        self.assertTrue(result['complete'])
        self.assertEqual(result['downloaded_files'], 4)
        manifest = self.manifest(job)
        self.assertEqual(manifest['serial'], '12345')
        self.assertTrue(manifest['robot_id'].startswith('robot-'))
        self.assertEqual(manifest['status'], 'complete')
        for record in manifest['files']:
            self.assertTrue((result['path'] / record['path']).is_file())
        self.assertEqual(len(self.remote.clients), 1)
        self.assertTrue(self.remote.clients[0].closed)
        self.assertTrue(all(call[0] in {'connect', 'open', 'stat', 'listdir_attr'}
                            for call in self.remote.calls))
        self.assertNotIn('private-password', repr(list(job.events.queue)) + json.dumps(manifest))
        self.assertIsNone(job.connection)
        self.assertFalse(list(job.snapshot_path.rglob('*.part*')))

    def test_missing_either_log_is_partial_and_retains_other_sources(self):
        for missing in ('log_history.txt', 'log_history.bak'):
            with self.subTest(missing=missing):
                self.remote.files = {'recording.zip': recording(),
                    ('log_history.bak' if missing.endswith('txt') else 'log_history.txt'): b'log'}
                job = self.make_job()
                job.run()
                result, = self.events(job, 'collected')
                self.assertFalse(result['complete'])
                self.assertEqual(result['downloaded_files'], 2)
                self.assertEqual(self.manifest(job)['status'], 'partial')
                self.assertTrue(any(item['status'] == 'missing' for item in self.manifest(job)['files']))

    def test_rotated_log_path_does_not_duplicate_backup_as_current(self):
        job = self.make_job()
        job.connection = SshConnection('192.0.2.1', 'root', 'private-password', '/root/log_history.bak')
        job.run()
        self.assertEqual((job.snapshot_path / 'logs/log_history.txt').read_bytes(), b'current log\n')
        self.assertEqual((job.snapshot_path / 'logs/log_history.bak').read_bytes(), b'previous log\n')
        logs = [record for record in self.manifest(job)['files'] if record['kind'] == 'log']
        self.assertEqual([record['remote_path'] for record in logs],
                         ['/root/log_history.bak', '/root/log_history.txt'])

    def test_missing_flight_directory_preserves_logs(self):
        self.remote.directory_error = FileNotFoundError(errno.ENOENT, 'Unavailable')
        job = self.make_job()
        job.run()
        result, = self.events(job, 'collected')
        self.assertFalse(result['complete'])
        self.assertEqual(result['downloaded_files'], 2)
        self.assertTrue(any('flight' in warning.lower() for warning in result['warnings']))

    def test_failed_archive_does_not_publish_or_lose_successful_files(self):
        self.remote.files['invalid.zip'] = b'bad archive'
        job = self.make_job()
        job.run()
        result, = self.events(job, 'collected')
        self.assertEqual(result['downloaded_files'], 3)
        self.assertFalse(result['complete'])
        self.assertFalse(list(job.snapshot_path.rglob('*.part*')))
        self.assertTrue(any(item['status'] == 'failed' for item in self.manifest(job)['files']))

    def test_cancel_preserves_completed_files_and_explicit_manifest(self):
        job = self.make_job()
        reads = 0
        def cancel_after_first_file():
            nonlocal reads
            reads += 1
            if reads == 3:
                job.cancel()
        self.remote.on_read = cancel_after_first_file
        job.run()
        self.assertFalse(self.events(job, 'collected'))
        result, = self.events(job, 'cancelled')
        self.assertEqual(result['path'], job.snapshot_path)
        self.assertEqual(result['downloaded_files'], 1)
        self.assertEqual(self.manifest(job)['status'], 'cancelled')
        self.assertFalse(list(job.snapshot_path.rglob('*.part*')))

    def test_limits_explicitly_mark_partial_coverage(self):
        job = self.make_job(max_files=2)
        job.run()
        result, = self.events(job, 'collected')
        self.assertFalse(result['complete'])
        self.assertTrue(any('limit' in warning.lower() for warning in result['warnings']))
        self.assertEqual(result['downloaded_files'], 2)
        job = self.make_job(max_total_bytes=30)
        job.run()
        result, = self.events(job, 'collected')
        self.assertFalse(result['complete'])
        self.assertLessEqual(sum(item.get('size', 0) for item in self.manifest(job)['files']
                                 if item['status'] == 'downloaded'), 30)

    def test_no_sources_is_error_and_does_not_claim_completion(self):
        self.remote.files.clear()
        job = self.make_job()
        job.run()
        self.assertFalse(self.events(job, 'collected'))
        self.assertTrue(self.events(job, 'error'))
        self.assertEqual(self.manifest(job)['status'], 'failed')

    def test_unsafe_names_are_not_downloaded_and_new_runs_do_not_overwrite(self):
        self.remote.files.update({'../escape.zip': recording(), 'bad\\path.zip': recording()})
        first = self.make_job()
        first.run()
        second = self.make_job()
        second.run()
        self.assertNotEqual(first.snapshot_path, second.snapshot_path)
        self.assertTrue((first.snapshot_path / 'diagnostic_manifest.json').exists())
        self.assertFalse(any('escape' in call[1] or 'bad\\path' in call[1]
                             for call in self.remote.calls if call[0] == 'open'))

    def test_transport_errors_redact_credentials(self):
        job = self.make_job()
        with patch.object(job, '_ensure_connection', side_effect=ValueError('private-password denied')):
            job.run()
        self.assertNotIn('private-password', repr(list(job.events.queue)) + json.dumps(self.manifest(job)))

    def test_host_key_confirmation_is_required_and_cancellable(self):
        job = self.make_job()
        import threading
        accepted = []
        thread = threading.Thread(target=lambda: accepted.append(job.confirm_host('192.0.2.1', 'SHA256:test')))
        thread.start()
        kind, question = job.events.get(timeout=2)
        self.assertEqual(kind, 'host_key')
        question.accepted = False
        question.answered.set()
        thread.join(timeout=2)
        self.assertEqual(accepted, [False])

    def test_growing_log_keeps_appended_bytes_under_limit(self):
        original = SFTP.stat
        def small_stat(sftp, path):
            attr = original(sftp, path)
            if path.endswith('log_history.txt'):
                attr.st_size = 2
            return attr
        job = self.make_job()
        with patch.object(SFTP, 'stat', small_stat):
            job.run()
        self.assertTrue(self.events(job, 'collected')[0]['complete'])
        self.assertEqual((job.snapshot_path / 'logs/log_history.txt').read_bytes(), b'current log\n')

    def test_growing_log_over_limit_is_not_published(self):
        original = SFTP.stat
        def small_stat(sftp, path):
            attr = original(sftp, path)
            if path.endswith('log_history.txt'):
                attr.st_size = 2
            return attr
        job = self.make_job()
        with patch.object(SFTP, 'stat', small_stat), patch('motionstudio.diagnostics.transfer.MAX_LOG_BYTES', 5):
            job.run()
        result, = self.events(job, 'collected')
        self.assertEqual(result['downloaded_files'], 1)
        self.assertFalse(result['complete'])
        self.assertFalse((job.snapshot_path / 'logs/log_history.txt').exists())

    def test_oversized_flight_is_rejected_before_open(self):
        original = SFTP.stat
        def huge_stat(sftp, path):
            attr = original(sftp, path)
            if path.endswith('.zip'):
                attr.st_size = 256 * 1024 * 1024 + 1
            return attr
        job = self.make_job()
        with patch.object(SFTP, 'stat', huge_stat):
            job.run()
        self.assertFalse(self.events(job, 'collected')[0]['complete'])
        self.assertFalse(any(call[0] == 'open' and call[1].endswith('.zip') for call in self.remote.calls))

    def test_scan_limit_is_explicit(self):
        self.remote.files = {'old.csv': CSV, 'new.csv': CSV, 'log_history.txt': b'log'}
        job = self.make_job(max_listing_entries=1)
        job.run()
        result, = self.events(job, 'collected')
        self.assertTrue(any('scan limit' in warning for warning in result['warnings']))
        self.assertEqual(len([r for r in self.manifest(job)['files']
                              if r['kind'] == 'flight' and r['status'] == 'downloaded']), 1)

    def test_changed_flight_is_not_published(self):
        original = SFTP.stat
        calls = 0
        def changed_stat(sftp, path):
            nonlocal calls
            attr = original(sftp, path)
            if path.endswith('.zip'):
                calls += 1
                attr.st_mtime += calls
            return attr
        job = self.make_job()
        with patch.object(SFTP, 'stat', changed_stat):
            job.run()
        result, = self.events(job, 'collected')
        self.assertFalse(result['complete'])
        self.assertEqual(result['downloaded_files'], 2)
        self.assertFalse(list(job.snapshot_path.rglob('*.zip')))

    def test_reconnect_rejects_identity_change(self):
        job = self.make_job()
        original = job._copy_source
        def disconnect_after_log(*args):
            result = original(*args)
            job._disconnect()
            self.remote.key = Key(b'another-robot')
            return result
        with patch.object(job, '_copy_source', side_effect=disconnect_after_log):
            job.run()
        result, = self.events(job, 'collected')
        self.assertFalse(result['complete'])
        self.assertEqual(result['downloaded_files'], 1)
        self.assertTrue(any('identity changed' in warning for warning in result['warnings']))



class DiagnosticReuseTests(unittest.TestCase):
    """Files the project already holds are copied locally, not downloaded again."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / 'Project'
        self.destination = self.project / 'Robot Status' / '192.0.2.1'
        self.destination.mkdir(parents=True)
        self.remote = Remote()
        self.remote.files.update({'log_history.txt': b'current log\n', 'log_history.bak': b'previous log\n'})

    def make_job(self, **kwargs):
        return DiagnosticSourceJob(connection=SshConnection('192.0.2.1', 'root', 'private-password'),
                destination=self.destination, client_factory=self.remote.factory, project=self.project,
                archive_name='Project-2026-11-20.tar', trust_path=Path(self.temp.name) / 'known_hosts', **kwargs)

    def write_backup(self, files, name='Project-2026-11-19.tar'):
        folder = self.project / 'Backups'
        folder.mkdir(exist_ok=True)
        (folder / name).write_bytes(robot_tar(files, self.remote.mtime))

    def downloads(self):
        return [path for kind, path in self.remote.calls if kind == 'open' and path != '/root/ur-serial']

    def run_job(self, **kwargs):
        job = self.make_job(**kwargs)
        job.run()
        events = [payload for event, payload in list(job.events.queue) if event == 'collected']
        self.assertEqual(len(events), 1, list(job.events.queue))
        manifest = json.loads((job.snapshot_path / 'diagnostic_manifest.json').read_text())
        return job, events[0], manifest

    def test_complete_backup_tar_needs_no_download(self):
        self.write_backup(self.remote.files)
        job, result, manifest = self.run_job()
        self.assertEqual(self.downloads(), [])
        self.assertEqual((result['reused_files'], result['downloaded_files']), (3, 0))
        self.assertTrue(result['complete'])
        self.assertEqual({r['origin'] for r in manifest['files']}, {'backup Project-2026-11-19.tar'})
        self.assertEqual((job.snapshot_path / 'logs/log_history.bak').read_bytes(), b'previous log\n')
        self.assertFalse(list(job.snapshot_path.rglob('*.part*')))

    def test_backup_and_flight_library_together_need_no_download(self):
        self.write_backup({k: v for k, v in self.remote.files.items() if k != 'recording.zip'})
        probe = self.make_job()
        probe._ensure_connection()
        robot_id, fingerprint = probe.robot_id, probe.fingerprint
        probe._disconnect()
        library = self.project / 'Flight reports' / robot_id
        library.mkdir(parents=True)
        data = self.remote.files['recording.zip']
        (library / 'flight-recording-abc.zip').write_bytes(data)
        import hashlib
        (library / 'manifest.json').write_text(json.dumps({'files': {'/root/flightreports/recording.zip': dict(
            local='flight-recording-abc.zip', fingerprint=fingerprint, size=len(data), mtime=self.remote.mtime,
            sha256=hashlib.sha256(data).hexdigest())}}))
        _job, result, manifest = self.run_job()
        self.assertEqual(self.downloads(), [])
        self.assertIn('Flight reports library', {r['origin'] for r in manifest['files']})
        self.assertEqual(result['reused_files'], 3)

    def test_only_the_missing_file_is_downloaded(self):
        self.write_backup({k: v for k, v in self.remote.files.items() if k != 'log_history.txt'})
        _job, result, _manifest = self.run_job()
        self.assertEqual(self.downloads(), ['/root/log_history.txt'])
        self.assertEqual((result['reused_files'], result['downloaded_files']), (2, 1))

    def test_changed_file_on_robot_is_downloaded_again(self):
        self.write_backup(self.remote.files)
        self.remote.files['log_history.txt'] = b'current log\nand a new line\n'
        _job, result, _manifest = self.run_job()
        self.assertEqual(self.downloads(), ['/root/log_history.txt'])

    def test_saved_log_is_reused_only_when_its_final_bytes_match(self):
        self.write_backup({k: v for k, v in self.remote.files.items() if k != 'log_history.bak'})
        logs = self.project / 'Logs'
        logs.mkdir()
        (logs / 'log_history.bak').write_bytes(b'previous lot\n')  # same size, different content
        _job, result, _manifest = self.run_job()
        self.assertEqual(result['downloaded_files'], 1)
        for folder in self.destination.glob('robot-diagnostics-*'):
            import shutil
            shutil.rmtree(folder)  # earlier datasets would otherwise supply it
        (logs / 'log_history.bak').write_bytes(b'previous log\n')
        self.remote.calls.clear()
        _job, result, manifest = self.run_job()
        self.assertEqual(self.downloads(), ['/root/log_history.bak'])  # the tail read only
        self.assertEqual(result['downloaded_files'], 0)
        self.assertIn('Logs folder', {r['origin'] for r in manifest['files']})

    def test_earlier_dataset_is_reused(self):
        _first, result, _manifest = self.run_job()  # no exec on this fake robot: files one by one
        self.assertEqual(result['downloaded_files'], 3)
        self.assertTrue(any('Single-archive download failed' in w for w in result['warnings']))
        self.remote.calls.clear()
        _job, result, manifest = self.run_job()
        self.assertEqual(self.downloads(), [])
        self.assertTrue(result['complete'])
        self.assertTrue(all(r['origin'].startswith('earlier Robot Status dataset') for r in manifest['files']))

    def test_nothing_local_downloads_one_tar_into_backups(self):
        commands = []
        remote = self.remote
        class Channel:
            def set_combine_stderr(self, value): pass
            def exec_command(self, command):
                commands.append(command)
                remote.files['robot-status.tar'] = robot_tar(
                    {k: v for k, v in remote.files.items() if k in REMOTE_PATHS}, remote.mtime)
            def recv_ready(self): return False
            def exit_status_ready(self): return True
            def recv_exit_status(self): return 0
            def close(self): pass
        removed = []
        with patch('tests.test_flight_transfer.Client.get_transport', lambda client: SimpleNamespace(
                get_remote_server_key=lambda: remote.key, open_session=lambda timeout=None: Channel())), \
             patch.object(SFTP, 'mkdir', lambda sftp, path, mode=0: removed.append(('mkdir', path)), create=True), \
             patch.object(SFTP, 'remove', lambda sftp, path: removed.append(('remove', path)), create=True), \
             patch.object(SFTP, 'rmdir', lambda sftp, path: removed.append(('rmdir', path)), create=True):
            job, result, manifest = self.run_job()
        archive = self.project / 'Backups' / 'Project-2026-11-20.tar'
        self.assertTrue(archive.is_file())
        self.assertEqual(result['archive'], archive)
        self.assertEqual(len(commands), 1)
        self.assertIn('/root/flightreports', commands[0])
        self.assertEqual([p for p in self.downloads() if not p.endswith('robot-status.tar')], [])
        self.assertEqual((result['reused_files'], result['downloaded_files']), (3, 0))
        self.assertEqual({r['origin'] for r in manifest['files']}, {'downloaded as Project-2026-11-20.tar'})
        self.assertEqual([kind for kind, _ in removed], ['mkdir', 'remove', 'rmdir'])

    def test_newest_complete_backup_means_older_tars_are_not_opened(self):
        self.write_backup(self.remote.files, 'Project-2026-11-19.tar')
        older = self.project / 'Backups' / 'Project-2026-10-01.tar'
        older.write_bytes(b'not read')
        import os
        os.utime(older, (1, 1))
        opened = []
        import motionstudio.diagnostics.transfer as transfer
        original = transfer._tar_members
        with patch.object(transfer, '_tar_members', lambda path: opened.append(path.name) or original(path)):
            _job, result, _manifest = self.run_job()
        self.assertEqual(opened, ['Project-2026-11-19.tar'])
        self.assertEqual(result['reused_files'], 3)

    def test_single_archive_respects_the_size_limit(self):
        _job, result, _manifest = self.run_job(max_total_bytes=30)
        self.assertFalse((self.project / 'Backups').exists())
        self.assertTrue(any('exceed the download size limit' in w for w in result['warnings']))

    def test_listing_is_read_before_downloads(self):
        def interleaving_breaks(*args, **kwargs):
            raise AssertionError('listdir_iter replies get mixed up when downloads interleave')
        with patch.object(SFTP, 'listdir_iter', interleaving_breaks, create=True):
            self.write_backup(self.remote.files)
            _job, result, _manifest = self.run_job()
        self.assertTrue(result['complete'])

    def test_empty_error_still_names_the_failure(self):
        job = self.make_job()
        self.assertEqual(job._error(EOFError()), 'EOFError')


if __name__ == '__main__':
    unittest.main()
