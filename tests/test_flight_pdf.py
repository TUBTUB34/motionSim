import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from flight_data import FlightReport
from flight_pdf import (_findings, _identity, default_pdf_name, export_flight_pdf,
                        measurement_summary, program_facts)
from flight_stats import joint_statistics


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


def page_text(path):
    try:
        import pymupdf
    except ImportError:
        raise unittest.SkipTest('Optional PyMuPDF is needed to read rendered page text')
    with pymupdf.open(path) as pdf:
        return [page.get_text() for page in pdf]


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
            # Summary, joint statistics, one chart page per joint, notes.
            self.assertEqual(len(re.findall(rb'/Type\s*/Page\b', content)), 9)
            self.assertEqual(list(Path(directory).glob('.flight-report-*')), [])

    def test_report_is_a_measurement_report_in_degrees_without_a_verdict(self):
        report = report_fixture()
        with tempfile.TemporaryDirectory() as directory:
            pages = page_text(export_flight_pdf(report, Path(directory)/'flight.pdf'))
        text = '\n'.join(pages)
        self.assertIn('Flight Recording Report', pages[0])
        self.assertIn('What the recording shows', pages[0])
        self.assertIn('controller uptime 7200.000 s', pages[0])
        for page_number, page in enumerate(pages, 1):
            self.assertIn(f'Page {page_number} of 9', page)
            self.assertIn('Generated ', page)
        for forbidden in ('Program Check Report', 'Compliance', 'Overall result', 'Checklist', 'Not recorded\n', 'collision detected'):
            self.assertNotIn(forbidden, text)
        # 1 to 4 rad is 57.3 to 229.2 degrees: a range of 171.9.
        for required in ('Joint statistics', '57.3', '229.2', '171.9', 'derived from speed', 'Base · joint 0',
                         'Wrist 3 · joint 5', 'Position [°]', 'Speed [°/s]', 'Elapsed time [s]',
                         'converted from the recorded radians to degrees', 'No limit is applied',
                         'Program context', '1 call statement; first at script line 1: set_payload(2.5)'):
            self.assertIn(required, text)
        # Torque, temperature and voltage are absent from this recording: stated, never zero.
        self.assertIn('Not recorded in this file: Peak torque, Highest temperature, Lowest voltage', ' '.join(text.split()))

    def test_recording_without_script_or_summary_omits_empty_sections(self):
        report = report_fixture()
        report.script, report.metadata = '', {'report_type': 'Motion Studio live recording', 'notes': 'After retune'}
        self.assertEqual([label for label, _value in _identity(report)],
                         ['Recording', 'Recording type', 'Recording length', 'Time axis', 'Notes'])
        with tempfile.TemporaryDirectory() as directory:
            pages = page_text(export_flight_pdf(report, Path(directory)/'live.pdf'))
        text = '\n'.join(pages)
        self.assertIn('Motion Studio live recording', pages[0])
        self.assertIn('After retune', pages[0])
        for absent in ('Program context', 'Not recorded\n', 'Incident time', 'Payload', 'URCap'):
            self.assertNotIn(absent, text)

    def test_findings_state_recorded_stops_and_peaks_without_naming_a_cause(self):
        report = report_fixture()
        report.columns['Safety mode'] = np.array([1., 1., 1., 3., 3.])
        report.columns['Speed scaling'] = np.array([1., 1., 1., 0., 0.])
        found = _findings(report, joint_statistics(report))
        self.assertIn('A protective stop is recorded from 3.00 s to the end of the recording.', found)
        self.assertIn('Safety mode: Normal at the start → Protective stop at 3.00 s.', found)
        self.assertIn('Highest joint speed: Base, −114.6 °/s at 3.00 s.', found)
        self.assertIn('Highest joint current: Base, 4.00 A at 1.00 s.', found)
        self.assertIn('Lowest speed scaling: 0 % at 3.00 s.', found)
        self.assertFalse(any(word in ' '.join(found).lower() for word in ('collision', 'fault', 'limit', 'exceed')))
        del report.columns['Safety mode']
        self.assertIn('Safety mode is not recorded, so protective stops cannot be identified from this file.',
                      _findings(report, joint_statistics(report)))

    def test_joint_without_any_channel_has_no_chart_page_and_is_named(self):
        time = np.array([0., .002])
        report = FlightReport(Path('minimal.csv'), 'minimal.csv', {}, time+1., time,
                              {'Actual position j0 [rad]': np.array([.1, .2])})
        with tempfile.TemporaryDirectory() as directory:
            pages = page_text(export_flight_pdf(report, Path(directory)/'minimal.pdf'))
        self.assertEqual(len(pages), 4)
        self.assertIn('Not recorded for this joint: speed, acceleration, current, torque', ' '.join(pages[2].split()))
        self.assertIn('No channel is recorded for Shoulder, Elbow, Wrist 1, Wrist 2, Wrist 3', ' '.join(pages[3].split()))

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
