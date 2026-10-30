"""Compare the supplied real backup with an independently calculated oracle."""
import hashlib
import json
import math
from pathlib import Path
import re
import unittest

from program_check import CATEGORIES, discover_programs, review_program

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / 'tests/full-backup.tar'
ORACLE = ROOT / 'reports/app-doughfactory-verification/expected-values.json'


@unittest.skipUnless(BACKUP.is_file() and ORACLE.is_file(), 'Optional supplied backup and audit oracle')
class SuppliedBackupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(ORACLE.read_text())
        cls.pair = next(p for p in discover_programs(BACKUP) if p.name == 'App_DoughFactory')
        cls.review = review_program(cls.pair)

    def finding(self, category, line, review=None):
        return next(f for f in (review or self.review).findings
                    if f.category == CATEGORIES[category] and f.line == line)

    def test_source_identity_counts_and_installation_match(self):
        self.assertEqual(hashlib.sha256(self.pair.script.read()).hexdigest(), self.expected['script_sha256'])
        self.assertEqual(self.review.script_lines, self.expected['expected_script_lines'])
        for command, count in self.expected['expected_call_counts'].items():
            self.assertEqual(self.review.counts[command], count, command)
        self.assertTrue(self.review.metadata['reviewInstallation'].endswith('/programs/default.installation'))
        self.assertEqual(len(self.review.hashes), 3)

    def test_every_motion_parameter_matches_independent_arithmetic(self):
        for row in self.expected['source_parameters']:
            for parameter, category in (('a', 4), ('v', 7), ('r', 5)):
                with self.subTest(line=row['script_line'], parameter=parameter):
                    finding = self.finding(category, row['script_line'])
                    match = re.search(r'\b' + parameter + r'=([-+\d.eE]+)', finding.detail)
                    self.assertIsNotNone(match, finding.detail)
                    actual, expected = float(match.group(1)), row[parameter + '_value']
                    self.assertTrue(math.isclose(actual, expected, rel_tol=5e-6, abs_tol=1e-8),
                                    (finding.detail, expected))

    def test_acceleration_comparisons_and_unknown_velocity_limits(self):
        warnings = sorted(f.line for f in self.review.findings
                          if f.category == CATEGORIES[4] and f.status == 'Review recommended')
        self.assertEqual(warnings, self.expected['expected_standard_tcp_acceleration_above_guidance_lines'])
        for row in self.expected['source_parameters']:
            if not row['command'].startswith('optimo'):
                self.assertEqual(self.finding(7, row['script_line']).status, 'Manual review')
        limited = review_program(self.pair, limits={'joint_velocity': 5, 'tcp_velocity': 2})
        for row in self.expected['source_parameters']:
            if row['command'].startswith('optimo'):
                continue
            threshold = 5 if row['command'] == 'movej' else 2
            exceeds = self.finding(7, row['script_line'], limited).status == 'Review recommended'
            self.assertEqual(exceeds, row['v_value'] > threshold, row['script_line'])

    def test_geometry_does_not_turn_branches_or_unknown_targets_into_zero_distance(self):
        detail = self.finding(8, 828).detail
        self.assertIn('0.0469574', detail)
        self.assertNotEqual(self.finding(5, 828).status, 'Review recommended')
        for line in (737, 741):
            self.assertIn('unresolved', self.finding(8, line).detail.lower())
        summary = self.finding(8, 0).detail
        self.assertIn('1 of 71', summary)
        self.assertNotIn('distance=0 m', summary)


if __name__ == '__main__':
    unittest.main()
