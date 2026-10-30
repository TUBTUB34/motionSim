import errno
import io
import json
from pathlib import Path
import queue
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

from flight_transfer import FlightTransfer, LogConnection, RemoteFlight, discover_library


def recording():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('files/realtimedata.csv',
                         'Timestamp [s],Actual position j0 [rad]\n1,0.1\n1.002,0.2\n')
        archive.writestr('summary.log', 'Serial number: 12345\n')
    return stream.getvalue()


class Key:
    def __init__(self, value=b'robot-a'):
        self.value = value

    def asbytes(self):
        return self.value


class Remote:
    def __init__(self):
        self.files = {'recording.zip': recording()}
        self.mtime = 123456
        self.calls = []
        self.clients = []
        self.key = Key()
        self.serial = b'12345\n'
        self.failures = 0
        self.on_read = None
        self.change_after_read = False
        self.directory_error = None

    def factory(self):
        client = Client(self)
        self.clients.append(client)
        return client


class Client:
    def __init__(self, remote):
        self.remote = remote
        self.closed = False

    def load_system_host_keys(self, path):
        pass

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, host, **kwargs):
        assert kwargs['look_for_keys'] is False and kwargs['allow_agent'] is False
        self.remote.calls.append(('connect', host))

    def get_transport(self):
        return SimpleNamespace(get_remote_server_key=lambda: self.remote.key)

    def open_sftp(self):
        return SFTP(self.remote)

    def close(self):
        self.closed = True


class SFTP:
    def __init__(self, remote):
        self.remote = remote

    def get_channel(self):
        return SimpleNamespace(settimeout=lambda value: None)

    def listdir_attr(self, directory):
        self.remote.calls.append(('listdir_attr', directory))
        if self.remote.directory_error:
            raise self.remote.directory_error
        return [SimpleNamespace(filename=name, st_size=len(value),
                st_mtime=self.remote.mtime, st_mode=stat.S_IFREG | 0o644)
                for name, value in self.remote.files.items()]

    def stat(self, path):
        self.remote.calls.append(('stat', path))
        name = Path(path).name
        if name not in self.remote.files:
            raise FileNotFoundError(errno.ENOENT, 'Remote file disappeared')
        return SimpleNamespace(st_size=len(self.remote.files[name]),
                               st_mtime=self.remote.mtime, st_mode=stat.S_IFREG | 0o644)

    def open(self, path, mode):
        assert mode == 'rb', 'Remote operations must be read-only'
        self.remote.calls.append(('open', path))
        if path == '/root/ur-serial':
            if self.remote.serial is None:
                raise FileNotFoundError(errno.ENOENT, 'Serial unavailable')
            return io.BytesIO(self.remote.serial)
        if self.remote.failures:
            self.remote.failures -= 1
            raise TimeoutError('Simulated disconnected transport')
        stream = io.BytesIO(self.remote.files[Path(path).name])
        remote = self.remote
        class Stream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                stream.close()

            def read(self, size):
                block = stream.read(size)
                if remote.on_read:
                    remote.on_read()
                if remote.change_after_read:
                    remote.mtime += 1
                    remote.change_after_read = False
                return block
        return Stream()


class FlightTransferTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.library = Path(self.temporary.name)
        self.remote = Remote()
        self.worker = self.make_worker()

    def make_worker(self):
        worker = FlightTransfer(LogConnection('192.0.2.1', 'root', 'not-a-real-secret'),
                library=self.library, client_factory=self.remote.factory,
                trust_path=self.library / 'known_hosts')
        self.addCleanup(worker.close)
        return worker

    def events(self):
        values = []
        while True:
            try:
                values.append(self.worker.events.get_nowait())
            except queue.Empty:
                return values

    def list(self):
        self.worker._list()
        return self.worker.entries

    def assert_no_partial(self):
        self.assertEqual(list(self.library.rglob('*.part*')), [])

    def test_one_metadata_listing_reuses_verified_connection(self):
        self.remote.files.update({'second.zip': recording(), 'robot_configuration123.zip': b'config',
                                  '../evil.zip': b'bad', 'readme.txt': b'text'})
        entries = self.list()
        self.assertEqual({e.name for e in entries}, {'recording.zip', 'second.zip'})
        self.list()
        self.assertEqual(len(self.remote.clients), 1)
        self.assertEqual(sum(c[0] == 'listdir_attr' for c in self.remote.calls), 2)
        self.assertFalse(any(c[0] == 'stat' for c in self.remote.calls))

    def test_download_validates_publishes_and_skips_unchanged(self):
        entry, = self.list()
        self.assertEqual(self.worker._download_batch([entry, entry.path]), (1, 0))
        paths = discover_library(self.library)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].read_bytes(), recording())
        self.assert_no_partial()
        calls = len(self.remote.calls)
        self.assertEqual(self.worker._download_batch(None), (0, 0))
        self.assertEqual(len(self.remote.calls), calls)
        self.list()
        self.assertTrue(self.worker.entries[0].downloaded)
        self.assertEqual(self.worker._download_batch([entry]), (1, 0))
        self.assertTrue([v for k, v in self.events() if k == 'downloaded'][-1]['skipped'])

    def test_downloaded_status_is_scoped_to_selected_local_folder(self):
        self.list()
        self.worker._download_batch(None)
        self.list()
        self.assertTrue(self.worker.entries[0].downloaded)
        original = self.library
        self.library = original / 'another-working-folder'
        other = self.make_worker()
        other._list()
        self.assertFalse(other.entries[0].downloaded)
        self.assertEqual(other._download_batch(None), (1, 0))
        self.assertEqual(len(discover_library(original)), 1)
        self.assertEqual(len(discover_library(self.library)), 1)
        self.library = original
        reopened = self.make_worker()
        reopened._list()
        self.assertTrue(reopened.entries[0].downloaded)

    def test_corrupted_local_copy_is_not_skipped(self):
        self.list()
        self.worker._download_batch(None)
        local, = discover_library(self.library)
        value = bytearray(local.read_bytes())
        value[0] ^= 1
        local.write_bytes(value)
        self.assertEqual(self.worker._download_batch(None), (1, 0))
        self.assertEqual(local.read_bytes(), recording())

    def test_changed_remote_not_published(self):
        self.list()
        self.remote.change_after_read = True
        self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertEqual(discover_library(self.library), [])
        self.assert_no_partial()

    def test_changed_since_listing_requires_refresh(self):
        self.list()
        self.remote.mtime += 1
        self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertEqual(discover_library(self.library), [])
        self.assertFalse(any(c == ('open', '/root/flightreports/recording.zip') for c in self.remote.calls))

    def test_invalid_zip_and_missing_telemetry_not_published(self):
        for value in (b'broken', b'PK\x03\x04'):
            with self.subTest(value=value):
                self.remote.files['recording.zip'] = value
                self.list()
                self.assertEqual(self.worker._download_batch(None), (0, 1))
                self.assertEqual(discover_library(self.library), [])
                self.assert_no_partial()

    def test_crc_corruption_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
            archive.writestr('files/realtimedata.csv', 'Timestamp [s]\n1\n2\n')
        value = stream.getvalue().replace(b'Timestamp', b'Uimestamp', 1)
        self.remote.files['recording.zip'] = value
        self.list()
        self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertEqual(discover_library(self.library), [])

    def test_disk_full_does_not_open_report(self):
        self.list()
        with patch('flight_transfer.shutil.disk_usage', return_value=SimpleNamespace(free=0)):
            self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assert_no_partial()
        self.assertEqual(discover_library(self.library), [])
        self.assertFalse(any(c == ('open', '/root/flightreports/recording.zip') for c in self.remote.calls))

    def test_actual_enospc_keeps_existing_report(self):
        self.list()
        self.worker._download_batch(None)
        original, = discover_library(self.library)
        self.remote.mtime += 1
        self.list()
        with patch('flight_transfer.copy_remote', side_effect=OSError(errno.ENOSPC, 'Disk full')):
            self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertTrue(original.is_file())
        self.assert_no_partial()

    def test_cancel_cleans_staging_and_allows_reconnect_retry(self):
        self.list()
        self.remote.on_read = self.worker.cancel
        with self.assertRaises(InterruptedError):
            self.worker._download_batch(None)
        self.assert_no_partial()
        self.assertEqual(discover_library(self.library), [])
        self.assertEqual(self.worker.failed, ['/root/flightreports/recording.zip'])
        self.remote.on_read = None
        self.worker.stopped.clear()
        self.assertEqual(self.worker._download_batch(self.worker.failed), (1, 0))
        self.assertEqual(len(self.remote.clients), 2)

    def test_transient_timeout_retry_reconnects_and_succeeds(self):
        self.list()
        self.remote.failures = 1
        self.assertEqual(self.worker._download_batch(None), (1, 0))
        self.assertEqual(len(self.remote.clients), 2)
        self.assert_no_partial()

    def test_retry_budget_is_bounded(self):
        self.list()
        self.remote.failures = 10
        self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertEqual(len(self.remote.clients), 3)
        self.assert_no_partial()

    def test_identity_change_blocks_mid_batch_reconnect(self):
        self.list()
        self.remote.key = Key(b'other-robot')
        self.worker._disconnect()
        with self.assertRaisesRegex(ValueError, 'identity changed'):
            self.worker._download_batch(None)
        self.assertEqual(discover_library(self.library), [])

    def test_same_filename_on_other_robot_has_separate_library(self):
        self.list()
        self.worker._download_batch(None)
        self.remote.key = Key(b'other-robot')
        second = self.make_worker()
        second._list()
        second._download_batch(None)
        paths = discover_library(self.library)
        self.assertEqual(len(paths), 2)
        self.assertNotEqual(paths[0].parent, paths[1].parent)

    def test_shared_host_key_different_serial_has_separate_library(self):
        self.list()
        self.worker._download_batch(None)
        self.remote.serial = b'67890\n'
        second = self.make_worker()
        second._list()
        second._download_batch(None)
        self.assertEqual(len(discover_library(self.library)), 2)
        self.assertNotEqual(second.robot_id, self.worker.robot_id)

    def test_serial_change_blocks_reconnect(self):
        self.list()
        self.worker._disconnect()
        self.remote.serial = b'67890\n'
        with self.assertRaisesRegex(ValueError, 'serial changed'):
            self.list()

    def test_missing_optional_serial_uses_verified_host_identity(self):
        self.remote.serial = None
        self.list()
        self.assertEqual(self.worker.serial, '')
        self.assertEqual(self.worker._download_batch(None), (1, 0))

    def test_failed_file_does_not_block_other_file(self):
        self.remote.files['broken.zip'] = b'broken'
        self.list()
        self.assertEqual(self.worker._download_batch(None), (1, 1))
        self.assertEqual(len(discover_library(self.library)), 1)
        self.assertEqual(self.worker.failed, ['/root/flightreports/broken.zip'])

    def test_manifest_failure_cleans_new_publish(self):
        self.list()
        with patch.object(self.worker, '_save_manifest', side_effect=OSError(errno.ENOSPC, 'Full')):
            self.assertEqual(self.worker._download_batch(None), (0, 1))
        self.assertEqual(discover_library(self.library), [])
        self.assertFalse(list(self.library.rglob('*.zip')))

    def test_manifest_traversal_and_staging_are_not_discovered(self):
        folder = self.library / 'robot-example'
        folder.mkdir()
        (folder / '.hidden.part.zip').write_bytes(recording())
        (self.library / 'outside.zip').write_bytes(recording())
        (folder / 'manifest.json').write_text(json.dumps({'files': {
            'a': {'local': '../outside.zip'}, 'b': {'local': '.hidden.part.zip'}}}))
        self.assertEqual(discover_library(self.library), [])

    def test_malformed_manifest_record_is_ignored(self):
        self.list()
        directory = self.library / self.worker.robot_id
        directory.mkdir()
        (directory / 'manifest.json').write_text(json.dumps({'files': {
            '/root/flightreports/recording.zip': 'malformed'}}))
        self.worker._disconnect()
        self.list()
        self.assertFalse(self.worker.entries[0].downloaded)

    def test_empty_listing_and_missing_timestamp_do_not_invent_cache_matches(self):
        self.remote.files.clear()
        self.assertEqual(self.list(), [])
        self.remote.files['recording.zip'] = recording()
        self.remote.mtime = 0
        self.list()
        self.worker._download_batch(None)
        self.list()
        self.assertFalse(self.worker.entries[0].downloaded)

    def test_failed_refresh_invalidates_old_listing(self):
        self.list()
        self.remote.directory_error = PermissionError(errno.EACCES, 'Denied')
        with self.assertRaises(PermissionError):
            self.list()
        self.assertEqual(self.worker.entries, [])

    def test_public_worker_busy_refresh_download_and_close(self):
        self.assertTrue(self.worker.refresh())
        self.assertFalse(self.worker.refresh())
        self.assertFalse(self.worker.download())
        self.worker.start()
        def wait_finished():
            events = []
            while True:
                event = self.worker.events.get(timeout=5)
                events.append(event)
                if event[0] == 'finished':
                    return events
        listed = wait_finished()
        self.assertTrue(any(k == 'listed' for k, _ in listed))
        self.assertTrue(self.worker.download())
        events = wait_finished()
        self.assertEqual(events[-1][1]['succeeded'], 1)
        self.assertTrue(any(k == 'progress' for k, _ in events))
        self.worker.close()
        self.worker.thread.join(2)
        self.assertFalse(self.worker.thread.is_alive())
        self.assertIsNone(self.worker.connection)

    def test_missing_directory_reports_error_not_empty_success(self):
        self.remote.directory_error = FileNotFoundError(errno.ENOENT, 'Missing directory')
        with self.assertRaises(FileNotFoundError):
            self.list()

    def test_invalid_directory_rejected(self):
        for value in ('relative', '/root/../etc', '/root\nother'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.worker.refresh(value)


if __name__ == '__main__':
    unittest.main()
