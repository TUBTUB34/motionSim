"""Report grouping must reduce repetition without concealing distinct results."""
from pathlib import Path
import tempfile
import unittest

from program_check import CATEGORIES, Finding, ProgramPair, ProgramReview
from program_check_pdf import group_findings, export_program_pdf


class ProgramReportGroupingTests(unittest.TestCase):
    def finding(self, line, note='', value='3', status='Review recommended', unit='m/s²'):
        return Finding(CATEGORIES[4], status,
                       f'Configured a={value} {unit}. Exceeds user-supplied specification ≤2 {unit}. ' + note,
                       line, f'movel(target{line}, a=speed{line})', 0)

    def test_duplicate_warning_has_one_entry_with_all_lines_and_evidence(self):
        first, second = self.finding(10), self.finding(20)
        groups = group_findings([first, first, second])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].lines, (10, 20))
        self.assertEqual(len(groups[0].findings), 2)
        self.assertEqual({f.evidence for f in groups[0].findings}, {first.evidence, second.evidence})

    def test_equal_estimates_keep_different_definition_evidence(self):
        template = 'Source-definition estimate using {}. Initialization order, runtime changes and execution are not proven. '
        first = self.finding(10, template.format('x=1.5 (line 2)'))
        second = self.finding(20, template.format('y=3 (line 3)'))
        groups = group_findings([first, second, self.finding(30)])
        self.assertEqual(len(groups), 2, 'Literal and estimated results must remain separate')
        self.assertTrue(groups[0].estimated)
        self.assertEqual(groups[0].assumptions, ('x=1.5 (line 2)', 'y=3 (line 3)'))
        self.assertIn('≤2 m/s²', groups[0].detail)

    def test_different_values_units_status_and_limits_are_never_collapsed(self):
        findings = [self.finding(1), self.finding(2, value='4'),
                    self.finding(3, unit='rad/s²'), self.finding(4, status='Manual review')]
        findings.append(Finding(CATEGORIES[4], 'Review recommended', 'Configured a=3 m/s². Exceeds user-supplied specification ≤1 m/s².', 5))
        self.assertEqual(len(group_findings(findings)), 5)

    def test_missing_script_is_one_coverage_gap_not_nine_repeated_issues(self):
        groups = group_findings([Finding(category, 'Manual review', 'No matching generated .script file. Select the .script saved with this URP.') for category in CATEGORIES])
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].category, 'Source files')

    def test_pdf_keeps_grouped_lines_and_long_evidence_inside_pages(self):
        try:
            import pymupdf
        except ImportError:
            self.skipTest('Optional PyMuPDF is needed to inspect rendered page bounds')
        findings = [self.finding(n) for n in range(1, 121)]
        findings.append(Finding(CATEGORIES[8], 'Manual review', 'Unresolved runtime values. ' * 350, 500))
        review = ProgramReview(ProgramPair('Readability'), {}, {'/very-long/' + 'source-name-'*120: 'a'*64}, 500, [], findings, 'Unconfirmed family')
        with tempfile.TemporaryDirectory() as folder:
            path = export_program_pdf(review, Path(folder)/'report.pdf')
            with pymupdf.open(path) as pdf:
                text = '\n'.join(page.get_text() for page in pdf)
                self.assertEqual(text.count('Configured a=3 m/s².'), 1)
                self.assertIn('1–120', text)
                self.assertIn('1 issue group', text)
                for page in pdf:
                    boxes = []
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            x0, y0, x1, y1 = line['bbox']
                            self.assertGreaterEqual(x0, 0)
                            self.assertLessEqual(x1, page.rect.width)
                            self.assertGreaterEqual(y0, 0)
                            self.assertLessEqual(y1, page.rect.height)
                            self.assertFalse(y0 < 780 < y1, 'Body text intrudes into the footer')
                            rect = pymupdf.Rect(line['bbox'])
                            for previous in boxes:
                                overlap = rect & previous
                                self.assertFalse(overlap.width > 3 and overlap.height > 3,
                                                 'Text lines overlap')
                            boxes.append(rect)


if __name__ == '__main__':
    unittest.main()
