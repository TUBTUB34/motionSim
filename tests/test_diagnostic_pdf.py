"""Readable narratives, faithful traces and atomic PDF output."""
from pathlib import Path
import tempfile
import unittest

from diagnostic_pdf import export_diagnostic_pdf, incident_sections, _chart
from robot_diagnostics import DiagnosticReport, Incident, ChartEvidence, analyze_directory


class DiagnosticPdfTests(unittest.TestCase):
    def test_equivalent_text_appears_once_without_losing_distinct_evidence(self):
        incident = Incident(observations=['Recorded 2 A.', 'Recorded 2 A.', 'Recorded 3 A.'],
                            hypotheses=['Recorded 2 A.', 'Contact is one possible explanation.'])
        sections = incident_sections(incident)
        values = [value for _, items in sections for value in items]
        self.assertEqual(values.count('Recorded 2 A.'), 1)
        self.assertIn('Recorded 3 A.', values)
        self.assertIn('Contact is one possible explanation.', values)

    def test_synthetic_folder_pdf_contains_narrative_evidence_and_qualifications(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'log_history.txt').write_text('3.5 :: 102s :: 2026-04-01 12:00:00 :: 110 :: C157A0:6 :: Collision detected\n')
            (root/'flight.csv').write_text('Timestamp [s],Actual velocity j0 [rad/s],Target velocity j0 [rad/s],Actual current j0 [A],Target current j0 [A]\n100,0,0,1,1\n101,1,1,2,1\n102,2,3,5,1\n103,0,0,1,1\n')
            report = analyze_directory(root)
            path = export_diagnostic_pdf(report, root/'review.pdf', include_evidence=True)
            with pymupdf.open(path) as pdf:
                text = '\n'.join(page.get_text() for page in pdf)
                for required in ('Robot Status', 'What the evidence shows', 'Possible contributors',
                                 'What to check next', 'Evidence still needed', 'C157A0',
                                 'No exact fault sample', 'Actual velocity', 'rad/s', 'Sources and method'):
                    self.assertIn(required, text)
                self.assertNotIn('Dashed line: explicit incident marker.', text)
                for page in pdf:
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            x0, y0, x1, y1 = line['bbox']
                            self.assertGreaterEqual(x0, 0)
                            self.assertLessEqual(x1, page.rect.width)
                            self.assertGreaterEqual(y0, 0)
                            self.assertLessEqual(y1, page.rect.height)
                            self.assertFalse(y0 < 780 < y1, 'Body intrudes into footer')

    def test_long_evidence_paginated_without_truncation(self):
        import pymupdf
        sentence = 'Distinct motion observation with a recorded unit of 2 rad/s. '
        report = DiagnosticReport(source='/folder/'+'long-path-'*80,
                  incidents=[Incident(id='INC-001', title='Long evidence review',
                  observations=[sentence*150 + 'Final evidence retained.'],
                  provenance=['a'*1400])])
        with tempfile.TemporaryDirectory() as folder:
            path = export_diagnostic_pdf(report, Path(folder)/'review.pdf', include_evidence=True)
            with pymupdf.open(path) as pdf:
                self.assertIn('Final evidence retained.', '\n'.join(page.get_text() for page in pdf))
                self.assertGreater(len(pdf), 3)
                for page in pdf:
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            x0, y0, x1, y1 = line['bbox']
                            self.assertLessEqual(x1, page.rect.width)
                            self.assertFalse(y0 < 780 < y1)

    def test_cancelled_export_preserves_existing_report_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'review.pdf'
            path.write_bytes(b'original')
            checks = 0
            def cancel():
                nonlocal checks
                checks += 1
                if checks > 1:
                    raise InterruptedError('Cancelled')
            with self.assertRaises(InterruptedError):
                export_diagnostic_pdf(DiagnosticReport(), path, cancel)
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_plot_keeps_gap_and_explicit_marker_distinct(self):
        from matplotlib.backends.backend_pdf import PdfPages
        from program_check_pdf import _ProgramReport
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'flight.csv'
            path.write_text('Timestamp [s],Actual velocity j0 [rad/s]\n100,0\n101,1\n102,2\n120,1\n121,0\n')
            with PdfPages(Path(folder)/'chart.pdf') as pdf:
                document = _ProgramReport(pdf, str(path))
                evidence = ChartEvidence(str(path), path.name, 2, 0, 21, 'Incident window')
                _chart(document, evidence, 'INC-001')
                lines = document.figure.axes[0].lines
                traces = [line for line in lines if line.get_linestyle() != '--']
                self.assertEqual(len(traces), 2)
                self.assertEqual(list(traces[0].get_xdata()), [0, 1, 2])
                self.assertEqual(list(traces[1].get_xdata()), [20, 21])
                self.assertEqual(list(lines[-1].get_xdata()), [2, 2])
                document.finish_page()

    def test_changed_source_cannot_silently_replace_analyzed_chart(self):
        import hashlib
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'flight.csv'
            path.write_text('Timestamp [s],Actual velocity j0 [rad/s]\n1,0\n2,1\n')
            evidence = ChartEvidence(str(path), path.name, None, 0, 1, 'Recording-level',
                                     hashlib.sha256(path.read_bytes()).hexdigest())
            report = DiagnosticReport(incidents=[Incident(id='REC-001', charts=[evidence])])
            path.write_text('Timestamp [s],Actual velocity j0 [rad/s]\n1,80\n2,99\n')
            pdf_path = export_diagnostic_pdf(report, Path(folder)/'review.pdf', include_evidence=True)
            with pymupdf.open(pdf_path) as pdf:
                text = '\n'.join(page.get_text() for page in pdf)
                self.assertIn('source file changed after analysis', text)
                self.assertNotIn('Actual velocity [rad/s]', text)


if __name__ == '__main__':
    unittest.main()
