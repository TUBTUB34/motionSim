"""Diagnostic collection uses synthetic, read-only SSH providers."""
import errno
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diagnostic_transfer import DiagnosticSourceJob
from log_transfer import LogConnection
from tests.test_flight_transfer import Remote, SFTP, Key, recording


CSV = b'Timestamp [s],Actual position j0 [rad]\n1,0.1\n1.002,0.2\n'


class DiagnosticTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.remote = Remote()
        self.remote.files.update({'log_history.txt': b'current log\n',
                                  'log_history.bak': b'previous log\n'})

    def make_job(self, **kwargs):
        return DiagnosticSourceJob(connection=LogConnection('192.0.2.1', 'root', 'private-password'),
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
        job.connection = LogConnection('192.0.2.1', 'root', 'private-password', '/root/log_history.bak')
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
        with patch.object(SFTP, 'stat', small_stat), patch('diagnostic_transfer.MAX_LOG_BYTES', 5):
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


if __name__ == '__main__':
    unittest.main()
