import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from flight_data import FlightReport
from flight_pdf import (default_pdf_name, export_flight_pdf, measurement_summary,
                        program_facts)


def report_fixture():
    time = np.array([0., 1., 2., 3., 4.])
    columns = {}
    for joint in range(6):
        columns[f'Actual position j{joint} [rad]'] = np.array([1., 2., np.nan, 3., 4.])
        columns[f'Actual velocity j{joint} [rad/s]'] = np.array([0., 1., np.nan, -2., 1.])
        columns[f'Actual current j{joint} [A]'] = np.array([3., 4., np.nan, 0., 0.])
    return FlightReport(Path('recording-test.zip'), 'recording-test.zip',
                        {'program': 'Actual program', 'serial': '12345'},
                        time+7200., time, columns, script='set_payload(2.5)\nset_tcp(p[0,0,0.2,0,0,0])\n')


class FlightPdfTests(unittest.TestCase):
    def test_static_call_counts_exclude_polyscope_strings_and_comments(self):
        source = ('# movel(p[0,0,0,0,0,0])\n'
                  '$ 25 "movel(p[0,0,0,0,0,0])"\n'
                  'movel(p[0,0,0,0,0,0], a=1.2, r=0.1)\n'
                  'optimovej(get_inverse_kin(target), a=0.2)\n'
                  'set_target_payload(3.6, [0,0,0.1], [1,1,1,0,0,0])\n'
                  ' # Source: UR Connect, 2.3.8, Universal Robots\n')
        facts = program_facts(source)
        self.assertEqual(len(facts.named('movel')), 1)
        self.assertEqual(facts.named('movel')[0][0], 3)
        self.assertEqual(len(facts.named('set_target_payload')), 1)
        self.assertEqual(facts.urcaps[0][0], 6)

    def test_summaries_use_all_finite_samples_and_do_not_fill_gaps(self):
        report = report_fixture()
        row = measurement_summary(report)[0]
        self.assertEqual(row['position_min'], 1.)
        self.assertEqual(row['position_max'], 4.)
        self.assertEqual(row['peak_speed'], 2.)
        self.assertEqual(row['position_samples'], 4)
        self.assertEqual(row['rms_current'], 2.5)
        report.columns['Actual current j0 [A]'][:] = np.nan
        self.assertIsNone(measurement_summary(report)[0]['rms_current'])

    def test_export_is_real_pdf_with_all_six_joint_pages(self):
        report = report_fixture()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/default_pdf_name(report)
            self.assertEqual(export_flight_pdf(report, target), target)
            content = target.read_bytes()
            self.assertTrue(content.startswith(b'%PDF-'))
            self.assertTrue(content.rstrip().endswith(b'%%EOF'))
            self.assertEqual(len(re.findall(rb'/Type\s*/Page\b', content)), 10)
            self.assertEqual(list(Path(directory).glob('.flight-report-*')), [])

    def test_failed_export_keeps_existing_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'existing.pdf'
            target.write_bytes(b'previous report')
            with patch('flight_pdf._summary_page', side_effect=RuntimeError('export failed')):
                with self.assertRaisesRegex(RuntimeError, 'export failed'):
                    export_flight_pdf(report_fixture(), target)
            self.assertEqual(target.read_bytes(), b'previous report')
            self.assertEqual(list(Path(directory).glob('.flight-report-*')), [])

    def test_filename_and_empty_report(self):
        report = report_fixture()
        report.name = 'name with spaces.zip'
        self.assertEqual(default_pdf_name(report), 'name_with_spaces_flight_report.pdf')
        report.time = np.array([])
        with self.assertRaisesRegex(ValueError, 'no timestamped samples'):
            export_flight_pdf(report, '/tmp/no-empty-flight.pdf')


if __name__ == '__main__':
    unittest.main()
