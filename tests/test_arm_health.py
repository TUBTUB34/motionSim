"""Arm health grouping, severity and trend rules (diagnostics.health.METHOD) on synthetic incidents."""
import unittest

from motionstudio.diagnostics.analysis import DiagnosticReport, Incident, ProgramEvidence
from motionstudio.diagnostics.health import INFERENCE, METHOD, UR_DOCUMENTED, build_arm_health, trend
from datetime import datetime


def burst(number, code, day, joint=None):
    return Incident(id=f'INC-{number:03d}', time=f'2026-{day} 10:00:00', primary_code=code,
                    joints=[joint or 'Unknown'], joint_codes={joint: [code]} if joint else {})


class ArmHealthTests(unittest.TestCase):
    def health(self, incidents):
        return build_arm_health(DiagnosticReport(incidents=incidents, log_source_count=1))

    def test_one_finding_per_class_and_joint_with_status_from_worst(self):
        health = self.health([burst(1, 'C157A2', '07-01', 'Elbow'), burst(2, 'C157A2', '08-01', 'Elbow'),
                              burst(3, 'C157A0', '09-01', 'Base'), burst(4, 'C264A1', '09-02')])
        titles = {f.title: f for f in health.findings}
        self.assertEqual(titles['Collision detection · Elbow'].count, 2)
        self.assertEqual(titles['Collision detection · Elbow'].severity, 'High')
        self.assertEqual(titles['Collision detection · Base'].severity, 'Medium')
        self.assertEqual(titles['Uninterpreted controller codes'].severity, 'Info')
        status = {row.joint: row.status for row in health.joints}
        self.assertEqual((status['Elbow'], status['Base'], status['Wrist 1']), ('Attention required', 'Monitor', 'Good'))
        self.assertEqual(health.overall, 'Attention required')

    def test_rising_trend_escalates_one_level_as_stated_in_method(self):
        self.assertIn('Rising trend moves up one level', ' '.join(METHOD))
        blends = [burst(1, 'C171A11', '07-01')] + [burst(n, 'C171A11', f'11-{n:02d}') for n in range(2, 7)]
        finding = self.health(blends).findings[0]
        self.assertEqual((finding.trend, finding.severity, finding.basis), ('Rising', 'High', INFERENCE))
        steady = self.health([burst(n, 'C171A11', f'{month:02d}-01') for n, month in enumerate(range(7, 11), 1)]).findings[0]
        self.assertEqual((steady.severity, steady.basis), ('Medium', UR_DOCUMENTED))

    def test_trend_needs_four_events(self):
        start, end = datetime(2026, 1, 1), datetime(2026, 12, 31)
        self.assertEqual(trend([datetime(2026, 12, 1)] * 3, start, end)[0], 'Too few')
        self.assertEqual(trend([datetime(2026, 2, 1)] * 2 + [datetime(2026, 11, 1)] * 2, start, end)[0], 'Stable')

    def test_payload_note_is_described_as_part_of_stops(self):
        health = self.health([burst(1, 'C162A0', '07-01'), burst(2, 'C162A0', '08-01')])
        finding = health.findings[0]
        self.assertIn('the controller named the payload', finding.what)
        self.assertNotIn('Controller', health.hot_spots[0].types.split('(')[1])


    def test_hot_spot_cites_related_program_check_findings_of_its_program(self):
        recorded = burst(1, 'C157A2', '07-01', 'Elbow')
        recorded.flight_names, recorded.program_evidence = ['rec.zip'], [ProgramEvidence(program='Cell_Main')]
        report = DiagnosticReport(incidents=[recorded, burst(2, 'C157A2', '08-01', 'Elbow')], log_source_count=1,
                                  program_checks=[dict(program='Cell_Main', recording='rec.zip', score=85, findings=[
                                      dict(id='P-01', severity='Medium', category='Acceleration recommendations', location='Script line 9', issue='a=3'),
                                      dict(id='P-02', severity='Medium', category='Blend radius overlap', location='Script line 12', issue='r')])])
        health = build_arm_health(report)
        spot = next(h for h in health.hot_spots if h.location == 'Cell_Main')
        self.assertEqual(spot.program_refs, ['P-01'])
        self.assertIn('P-01 acceleration recommendations', health.program[0])
        self.assertEqual(next(h for h in health.hot_spots if h.location != 'Cell_Main').program_refs, [])

if __name__ == '__main__':
    unittest.main()
