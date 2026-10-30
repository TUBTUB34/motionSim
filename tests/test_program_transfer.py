"""Robot report retrieval is tested with an in-memory SSH/SFTP provider."""
from contextlib import nullcontext
import errno
import io
from pathlib import Path, PurePosixPath
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from log_transfer import LogConnection
from program_check import discover_programs, review_program
from program_transfer import ProgramSourceJob


class FakeSFTP:
    def __init__(self, files, root='/programs', links=()):
        self.files = files
        self.root = root
        self.links = set(links)
        self.directories = {root}
        for path in (*files, *links):
            parent = PurePosixPath(path).parent
            while str(parent) not in ('/', '.'):
                self.directories.add(str(parent))
                parent = parent.parent
        self.channel = MagicMock()
        self.opened = []

    def get_channel(self):
        return self.channel

    def stat(self, path):
        if path in self.directories:
            mode, size = stat.S_IFDIR, 0
        elif path in self.links:
            mode, size = stat.S_IFLNK, 0
        elif path in self.files:
            mode, size = stat.S_IFREG, len(self.files[path])
        else:
            raise FileNotFoundError(errno.ENOENT, 'Missing', path)
        return SimpleNamespace(st_mode=mode | 0o755, st_size=size, st_mtime=123,
                               filename=PurePosixPath(path).name)

    lstat = stat

    def listdir_iter(self, path, read_aheads=8):
        for name in sorted(set(self.files) | self.directories | self.links):
            if str(PurePosixPath(name).parent) == path:
                yield self.stat(name)

    def open(self, path, mode):
        assert mode == 'rb'
        self.opened.append(path)
        return io.BytesIO(self.files[path])


class ProgramTransferTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)

    def make_job(self, files=None, *, root='/programs', links=()):
        if files is None:
            files = {
                root + '/app/demo.urp': b'<URProgram lastSavedIn="5.24" installation="../default"/>',
                root + '/app/demo.script': b'movel(pose,a=3)',
                root + '/default.installation': b'<Installation/>',
                root + '/ignored.txt': b'not report source',
            }
        sftp = FakeSFTP(files, root=root, links=links)
        client = MagicMock()
        client.open_sftp.return_value = nullcontext(sftp)
        job = ProgramSourceJob(connection=LogConnection('127.0.0.1', 'root', 'password-secret'),
                               destination=self.destination, client_factory=lambda: client,
                               trust_path=self.destination / 'known_hosts')
        return job, client, sftp

    def events(self, job, kind):
        return [value for event, value in list(job.events.queue) if event == kind]

    def test_snapshot_preserves_nested_pair_and_matching_installation(self):
        job, client, sftp = self.make_job()
        job.run()
        snapshots = self.events(job, 'downloaded')
        self.assertEqual(len(snapshots), 1, list(job.events.queue))
        snapshot = snapshots[0]
        self.assertEqual(job.snapshot_path, snapshot)
        pairs = discover_programs(snapshot)
        self.assertEqual(len(pairs), 1)
        review = review_program(pairs[0])
        self.assertEqual(review.counts['movel'], 1)
        self.assertIn('default.installation', review.metadata['reviewInstallation'])
        self.assertFalse((snapshot / 'programs/ignored.txt').exists())
        self.assertEqual(len(sftp.opened), 3)
        client.close.assert_called_once()
        client.exec_command.assert_not_called()

    def test_missing_installation_is_not_a_download_failure(self):
        job, _, sftp = self.make_job()
        del sftp.files['/programs/default.installation']
        job.run()
        review = review_program(discover_programs(self.events(job, 'downloaded')[0])[0])
        self.assertIn('not found', review.metadata['reviewInstallationNote'])
        self.assertEqual(review.counts['movel'], 1)

    def test_missing_script_still_lists_urp(self):
        job, _, _ = self.make_job({'/programs/demo.urp': b'<URProgram/>'})
        job.run()
        pair = discover_programs(self.events(job, 'downloaded')[0])[0]
        self.assertIsNotNone(pair.urp)
        self.assertIsNone(pair.script)

    def test_singular_program_folder_fallback(self):
        job, _, _ = self.make_job(root='/program')
        job.run()
        self.assertEqual(job.remote_root, '/program')
        self.assertEqual(len(self.events(job, 'downloaded')), 1)

    def test_permission_error_does_not_fallback(self):
        job, _, sftp = self.make_job()
        sftp.stat = MagicMock(side_effect=PermissionError(errno.EACCES, 'Denied'))
        job.run()
        sftp.stat.assert_called_once_with('/programs')
        self.assertEqual(len(self.events(job, 'error')), 1)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_links_are_not_downloaded_or_followed(self):
        job, _, sftp = self.make_job(links=('/programs/link.script', '/programs/elsewhere'))
        job.run()
        self.assertEqual(len(self.events(job, 'downloaded')), 1)
        self.assertNotIn('/programs/link.script', sftp.opened)
        self.assertTrue(any('symbolic links' in text for text in self.events(job, 'status')))

    def test_unsafe_windows_names_are_rejected_without_partial_snapshot(self):
        for name in ('CON.script', 'bad:demo.script', 'bad\\demo.script', 'NUL.urp', 'bad?.urp',
                     'NUL .script', 'CONIN$.urp'):
            with self.subTest(name=name):
                job, _, _ = self.make_job({'/programs/demo.urp': b'<URProgram/>', '/programs/' + name: b'source'})
                job.run()
                self.assertEqual(len(self.events(job, 'error')), 1)
                self.assertEqual(list(self.destination.iterdir()), [])

    def test_case_collisions_never_overwrite_windows_sources(self):
        job, _, _ = self.make_job({'/programs/Demo.urp': b'a', '/programs/demo.urp': b'b'})
        job.run()
        self.assertIn('Ambiguous', self.events(job, 'error')[0])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_no_urp_is_actionable_error_without_snapshot(self):
        job, _, _ = self.make_job({'/programs/demo.script': b'movel(pose)'})
        job.run()
        self.assertIn('No URP files', self.events(job, 'error')[0])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_source_change_discards_snapshot(self):
        job, _, sftp = self.make_job()
        original = sftp.lstat
        count = {}
        def changed(path):
            result = original(path)
            count[path] = count.get(path, 0) + 1
            if path.endswith('.script') and count[path] > 1:
                result.st_mtime += 1
            return result
        sftp.lstat = changed
        job.run()
        self.assertIn('changed during download', self.events(job, 'error')[0])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_cancellation_during_download_closes_and_removes_partial_snapshot(self):
        job, client, sftp = self.make_job()
        original = sftp.open
        def cancel(path, mode):
            job.cancel()
            return original(path, mode)
        sftp.open = cancel
        job.run()
        self.assertEqual(self.events(job, 'cancelled'), [None])
        self.assertEqual(self.events(job, 'downloaded'), [])
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertGreaterEqual(client.close.call_count, 1)

    def test_earlier_source_replaced_during_later_download_discards_snapshot(self):
        job, _, sftp = self.make_job()
        original = sftp.lstat
        count = {}
        def changed(path):
            result = original(path)
            count[path] = count.get(path, 0) + 1
            if path.endswith('.script') and count[path] > 2:
                result.st_mtime += 1
            return result
        sftp.lstat = changed
        job.run()
        self.assertIn('changed while downloading the snapshot', self.events(job, 'error')[0])
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_failure_redacts_credentials_and_preserves_existing_download(self):
        previous = self.destination / 'previous'
        previous.mkdir()
        (previous / 'demo.urp').write_bytes(b'old')
        job, _, sftp = self.make_job()
        sftp.open = MagicMock(side_effect=OSError('password-secret download failed'))
        job.run()
        self.assertNotIn('password-secret', self.events(job, 'error')[0])
        self.assertEqual(list(self.destination.iterdir()), [previous])
        self.assertEqual((previous / 'demo.urp').read_bytes(), b'old')

    def test_size_and_traversal_limits(self):
        for setting, value in (('MAX_BYTES', 1), ('MAX_TOTAL_BYTES', 1),
                               ('MAX_SOURCE_FILES', 1), ('MAX_DIRECTORIES', 1),
                               ('MAX_ENTRIES', 1), ('MAX_DEPTH', 0)):
            with self.subTest(setting=setting):
                job, _, _ = self.make_job()
                with patch('program_transfer.' + setting, value):
                    job.run()
                self.assertEqual(len(self.events(job, 'error')), 1)
                self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
