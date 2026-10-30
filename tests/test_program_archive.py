import gzip
import io
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from program_check import discover_programs, review_program


class ProgramArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, entries, name='full-backup.tar'):
        path = self.root / name
        with tarfile.open(path, 'w:gz' if name.endswith('.gz') else 'w') as archive:
            for member, data in entries:
                if isinstance(member, str):
                    member = tarfile.TarInfo(member)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return path

    def test_full_backup_pairs_absolute_paths_and_installation_without_extraction(self):
        path = self.archive([
            ('/programs/app/demo.urp', gzip.compress(b'<URProgram lastSavedIn="5.24" installation="../default"/>')),
            ('/programs/app/demo.script', b'movel(p[0,0,0,0,0,0], a=3)'),
            ('/programs/default.installation', b'<installation/>'),
            ('/root/log_history.txt', b'not program source'),
            ('/programs/../bad.script', b'movej(q)'),
        ])
        pairs = discover_programs(path)
        self.assertEqual(len(pairs), 1)
        result = review_program(pairs[0])
        self.assertEqual(result.counts['movel'], 1)
        self.assertIn('/programs/default.installation', result.metadata['reviewInstallation'])
        self.assertEqual(len(result.hashes), 3)
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_compressed_tar_and_relative_member_paths(self):
        path = self.archive([('./programs/demo.script', b'movej(q)')], 'backup.tar.gz')
        pairs = discover_programs(path)
        self.assertEqual(pairs[0].script.read(), b'movej(q)')

    def test_does_not_follow_links_or_ambiguous_normalized_names(self):
        link = tarfile.TarInfo('/programs/link.script')
        link.type = tarfile.SYMTYPE
        link.linkname = '/etc/passwd'
        path = self.archive([(link, b''), ('/programs/demo.script', b'movej(q)')])
        self.assertEqual(len(discover_programs(path)), 1)
        path = self.archive([('/programs/demo.script', b'movej(q)'), ('programs/demo.script', b'movel(p)')])
        with self.assertRaisesRegex(ValueError, 'Ambiguous'):
            discover_programs(path)

    def test_archive_source_limits_apply_before_reading(self):
        path = self.archive([('programs/demo.script', b'movej(q)')])
        pair = discover_programs(path)[0]
        with patch('program_check.MAX_BYTES', 4):
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                pair.script.read()


if __name__ == '__main__':
    unittest.main()
