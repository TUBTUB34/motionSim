"""Client-facing report structure, scope and readable pagination."""
from pathlib import Path
import tempfile
import unittest

from diagnostic_pdf import export_diagnostic_pdf
from flight_data import JOINTS
from robot_diagnostics import DiagnosticReport, Incident
from robot_status import RobotStatusReport, JointStatus, ProgramArea


def client_report():
    diagnostic = DiagnosticReport(source='/private/operator/workspaces/customer-data', incidents=[
        Incident(id='INC-001', title='Contact or motion demand', primary_code='C157A0',
                 observations=['Technical evidence reserved for the appendix.'])])
    return RobotStatusReport(
        source=diagnostic.source, overall_status='Needs attention',
        assessment=['A recorded warning affects the base joint during the pickup approach. Verify load settings and clearance before attributing the warning to a component.'],
        joints=[JointStatus(joint=joint, status='Needs attention' if index == 0 else 'Not assessed',
                summary='The controller reported a motion warning at the base.' if index == 0 else 'No joint-specific measurements were supplied.')
                for index, joint in enumerate(JOINTS)],
        program_areas=[ProgramArea(program='Client_pickup', area='Pickup approach and load changes',
                       priority='Review first', summary='Confirm the active load and approach clearance at the recorded pickup stage.',
                       script_lines=[42, 43], incident_ids=['INC-001'], association='Matched recording; cause unconfirmed')],
        actions=['Verify the carried mass at pickup.', 'Verify the carried mass at pickup.', 'Inspect fixture clearance during the approach.'],
        coverage=['Evidence supplied for one recorded operating interval.'],
        limitations=['The supplied files do not establish current mechanical condition.'],
        robot_details=[('Model', 'UR10e'), ('Serial', 'SYNTHETIC-DEMO')],
        diagnostic_report=diagnostic)


class RobotStatusPdfTests(unittest.TestCase):
    def test_default_report_is_a_client_assessment_with_all_six_joints(self):
        import pymupdf
        report = client_report()
        with tempfile.TemporaryDirectory() as folder:
            path = export_diagnostic_pdf(report, Path(folder)/'status.pdf',
                metadata={'client': 'Example Client', 'robot_label': 'Packaging cell 2', 'prepared_by': 'Service team'})
            with pymupdf.open(path) as pdf:
                text = '\n'.join(page.get_text() for page in pdf)
                self.assertIn('Example Client', pdf[0].get_text())
                self.assertIn('Packaging cell 2', pdf[0].get_text())
                for required in ('Robot Status', 'Needs attention', 'Joint condition', 'Program areas to review',
                                 'Client_pickup', '42–43', 'Recommended action plan', 'Not assessed', *JOINTS):
                    self.assertIn(required, text)
                self.assertEqual(text.count('Verify the carried mass at pickup.'), 1)
                self.assertNotIn('/private/operator', text)
                self.assertNotIn('Technical evidence reserved for the appendix.', text)
                self.assertNotIn('Technical evidence appendix', text)
                self.assertLessEqual(len(pdf), 5, 'Ordinary client report should not become an incident dump')
                self.assertIn('not an inspection', text)
                self.assertIn('Physical installation follow-up', text)
                self.assertIn('Not assessed by this report', text)
                self.assertIn('Safety validation', ' '.join(text.split()))

    def test_evidence_appendix_is_explicit_opt_in(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as folder:
            path = export_diagnostic_pdf(client_report(), Path(folder)/'status.pdf', include_evidence=True)
            with pymupdf.open(path) as pdf:
                text = '\n'.join(page.get_text() for page in pdf)
                self.assertIn('Technical evidence appendix', text)
                self.assertIn('Technical evidence reserved for the appendix.', text)
                self.assertIn('C157A0', text)

    def test_long_client_details_and_joint_text_wrap_inside_page_bounds(self):
        import pymupdf
        report = client_report()
        report.joints[0].summary = 'Recorded concern; obtain more evidence before deciding on service. ' * 50
        with tempfile.TemporaryDirectory() as folder:
            path = export_diagnostic_pdf(report, Path(folder)/'status.pdf', metadata={'client': 'Client-' * 160})
            with pymupdf.open(path) as pdf:
                for page in pdf:
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            x0, y0, x1, y1 = line['bbox']
                            self.assertGreaterEqual(x0, 0)
                            self.assertLessEqual(x1, page.rect.width)
                            self.assertGreaterEqual(y0, 0)
                            self.assertLessEqual(y1, page.rect.height)
                            self.assertFalse(y0 < 780 < y1, 'Body text intrudes into footer')


if __name__ == '__main__':
    unittest.main()
