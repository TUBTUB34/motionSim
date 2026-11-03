"""Check durable review records, import boundaries and evidence-preserving export."""
import csv
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from commissioning import (DEPLOYMENT_CHECKS, DEPLOYMENT_REFERENCES, MAX_FILE_BYTES, RECORD_NOTICE,
    export_record_csv, load_record, new_record, review_counts, save_record, validate_record)


class CommissioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'review.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_new_review_never_claims_automatic_verification(self):
        record = new_record()
        self.assertEqual(review_counts(record)['Not reviewed'], len(DEPLOYMENT_CHECKS))
        self.assertEqual(review_counts(record)['Recorded'], 0)
        self.assertEqual(len({item.key for item in DEPLOYMENT_CHECKS}), len(DEPLOYMENT_CHECKS))
        self.assertTrue(all(item.sources and item.url.startswith('https://') for item in DEPLOYMENT_CHECKS))
        for _title, path in DEPLOYMENT_REFERENCES:
            self.assertTrue((Path(__file__).resolve().parents[1] / path).is_file())

    def test_round_trip_notes_unicode_and_dates(self):
        record = new_record()
        record['metadata'].update(project='Line 1 · Montréal', robot='UR5e / 2026-example',
                                  review_date='2026-11-02', next_review='2027-01-02')
        record['items']['mounting'] = {'status': 'Action needed', 'note': 'Owner: Alex\nPhoto: bracket.png'}
        save_record(self.path, record)
        self.assertEqual(load_record(self.path), record)
        self.assertEqual(list(Path(self.temp.name).glob('*.tmp')), [])

    def test_missing_new_checks_are_unreviewed_and_input_is_not_modified(self):
        record = new_record()
        record['items'] = {'mounting': {'status': 'Recorded', 'note': 'Inspection record #42'}}
        previous = deepcopy(record)
        normalized = validate_record(record)
        self.assertEqual(record, previous)
        self.assertEqual(normalized['items']['electrical']['status'], 'Not reviewed')
        self.assertEqual(review_counts(normalized)['Recorded'], 1)

    def test_invalid_imports_do_not_silently_drop_evidence(self):
        invalid = []
        for version in (True, 2, '1', None):
            record = new_record()
            record['version'] = version
            invalid.append(record)
        record = new_record()
        record['items']['future-check'] = {'status': 'Recorded', 'note': 'Do not discard this'}
        invalid.append(record)
        record = new_record()
        record['items']['mounting']['status'] = 'Certified safe'
        invalid.append(record)
        record = new_record()
        record['metadata']['review_date'] = '2026-02-30'
        invalid.append(record)
        record = new_record()
        record['metadata']['next_review'] = '20261102'
        invalid.append(record)
        record = new_record()
        record['items']['mounting']['note'] = 'x' * 8001
        invalid.append(record)
        record = new_record()
        record['items']['mounting']['extra_evidence'] = 'Do not silently discard'
        invalid.append(record)
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(ValueError):
                validate_record(record)

    def test_failed_write_keeps_existing_review_and_cleans_partial(self):
        original = new_record()
        save_record(self.path, original)
        changed = new_record()
        changed['metadata']['project'] = 'New content'
        with patch('commissioning.os.replace', side_effect=OSError('Disk unavailable')):
            with self.assertRaises(OSError):
                save_record(self.path, changed)
        self.assertEqual(load_record(self.path), original)
        self.assertEqual(len(list(Path(self.temp.name).iterdir())), 1)

    def test_large_and_malformed_files_are_rejected(self):
        for data in (b'{}', b'not json', b'\xff', b' ' * (MAX_FILE_BYTES + 1)):
            with self.subTest(size=len(data)):
                self.path.write_bytes(data)
                with self.assertRaises(ValueError):
                    load_record(self.path)

    def test_csv_has_all_items_identity_sources_and_safe_cells(self):
        record = new_record()
        record['metadata']['project'] = ' =HYPERLINK("example")'
        record['metadata']['reviewer'] = '\t=2+2'
        record['items']['mounting'] = {'status': 'Action needed', 'note': '@SUM(1,1)\nOwner: Alex'}
        csv_path = Path(self.temp.name) / 'review.csv'
        export_record_csv(csv_path, record)
        with csv_path.open(newline='', encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), len(DEPLOYMENT_CHECKS))
        self.assertTrue(rows[0]['Project'].startswith("'"))
        self.assertTrue(rows[0]['Reviewer'].startswith("'"))
        self.assertTrue(rows[0]['Evidence / actions'].startswith("'"))
        self.assertEqual(rows[0]['Status (operator entered)'], 'Action needed')
        self.assertEqual(rows[0]['Scope'], RECORD_NOTICE)
        self.assertTrue(all(row['Sources'] and row['Reference URL'] for row in rows))


if __name__ == '__main__':
    unittest.main()
