"""Client condition summaries retain attribution, uncertainty and source detail."""
from dataclasses import replace
import unittest

from flight_data import JOINTS
from log_data import LogData
from robot_diagnostics import DiagnosticReport, Incident, JointEvidence, ProgramEvidence, analyze_data
from robot_status import build_robot_status
from tests.test_robot_diagnostics import event, flight


class RobotStatusTests(unittest.TestCase):
    def joint(self, report, joint='Base'):
        return next(item for item in build_robot_status(report).joints if item.joint == joint)

    def test_empty_data_has_six_unassessed_joints_not_healthy(self):
        result = build_robot_status(DiagnosticReport())
        self.assertEqual([item.joint for item in result.joints], list(JOINTS))
        self.assertEqual({item.status for item in result.joints}, {'Not assessed'})
        self.assertEqual(result.overall_status, 'Insufficient evidence')
        self.assertIn('do not contain enough', ' '.join(result.assessment))

    def test_incident_dates_do_not_claim_complete_evidence_coverage(self):
        result = build_robot_status(analyze_data([event()], []))
        details = dict(result.robot_details)
        self.assertEqual(details['Incident dates'], '2026-04-01')
        self.assertNotIn('Evidence dates', details)

    def test_distinct_joint_faults_in_one_burst_keep_exact_attribution(self):
        first = event('C157A0')
        second = {**event('C174A2'), 'joint': 'Elbow'}
        report = analyze_data([first, second], [])
        self.assertEqual(len(report.incidents), 1)
        base, elbow, wrist = self.joint(report), self.joint(report, 'Elbow'), self.joint(report, 'Wrist 1')
        self.assertIn('collision', base.summary)
        self.assertNotIn('torque-demand', base.summary)
        self.assertIn('torque-demand', elbow.summary)
        self.assertNotIn('C157A0', ' '.join(elbow.evidence))
        self.assertEqual(wrist.status, 'Limited evidence')
        self.assertNotEqual(wrist.status, 'Needs attention')

    def test_fault_summary_is_not_a_joint_failure_diagnosis(self):
        result = build_robot_status(analyze_data([event()], [flight()]))
        self.assertEqual(result.overall_status, 'Attention required')
        self.assertIn('does not establish that the joints need replacement', ' '.join(result.assessment))
        self.assertIn('do not establish that the joint is damaged', result.joints[0].summary)
        self.assertTrue(result.joints[0].recommendations)
        self.assertIn('match is provisional', ' '.join(result.joints[0].limitations))

    def test_joint_summary_synthesizes_only_associated_motion(self):
        matched = analyze_data([event()], [flight()])
        self.assertIn('associated recording also contains current outside', self.joint(matched).summary)
        unmatched = analyze_data([event()], [flight(metadata={'incident_time': '2026-04-02 12:00:00'})])
        self.assertIn('No matched joint telemetry', self.joint(unmatched).summary)
        self.assertNotIn('associated recording also contains current outside', self.joint(unmatched).summary)

    def test_joint_summary_keeps_provisional_identity_visible(self):
        report = analyze_data([event()], [flight()])
        self.assertIn('link is provisional because source robot identity is unverified', self.joint(report).summary)
        report.incidents[0].association_verified = True
        self.assertNotIn('link is provisional', self.joint(report).summary)

    def test_shared_joint_action_is_grouped_but_drilldown_retains_own_instruction(self):
        report = analyze_data([event('C157A0'), {**event('C174A2'), 'joint': 'Elbow'}], [])
        result = build_robot_status(report)
        shared = [action for action in result.actions if 'at the affected motion stage:' in action]
        self.assertEqual(len(shared), 1)
        self.assertIn('Review Base, Elbow', shared[0])
        self.assertTrue(any(action.startswith('Review Base at') for action in result.joints[0].recommendations))
        self.assertTrue(any(action.startswith('Review Elbow at') for action in result.joints[2].recommendations))
        self.assertTrue(any('blend' in action.lower() for action in result.actions))

    def test_recording_excursion_without_log_is_motion_review(self):
        report = analyze_data([], [flight()])
        joint = self.joint(report)
        self.assertEqual(joint.status, 'Review motion evidence')
        self.assertIn('No validated joint-specific fault', joint.summary)
        self.assertIn('Controller log coverage is unavailable.', joint.limitations)
        self.assertEqual(self.joint(report, 'Elbow').status, 'Not assessed')

    def test_current_bound_missing_never_becomes_a_pass(self):
        clean = flight(columns={'Actual velocity j0 [rad/s]': [0, 0, 0, 0, 0], 'Actual current j0 [A]': [1]*5})
        report = analyze_data([LogData('log', [], 0, {}, {})], [clean])
        joint = self.joint(report)
        self.assertEqual(joint.status, 'Limited evidence')
        self.assertIn('no current-limit check', ' '.join(joint.limitations))

    def test_no_recorded_concern_requires_actual_motion_current_logs_and_full_bounds(self):
        measured = JointEvidence('Base', 'one.zip', sample_count=5, channels=['position', 'velocity', 'current'],
                                 current_comparable_samples=5, current_both_bound_samples=5)
        report = DiagnosticReport(log_source_count=1, incidents=[Incident(id='REC-001', joint_evidence=[measured])])
        joint = self.joint(report)
        self.assertEqual(joint.status, 'No recorded concern')
        self.assertIn('does not establish present mechanical condition', joint.summary)
        for changed in (replace(measured, current_both_bound_samples=4), replace(measured, has_gaps=True),
                        replace(measured, channels=['velocity', 'current'])):
            with self.subTest(changed=changed):
                report.incidents[0].joint_evidence = [changed]
                self.assertEqual(self.joint(report).status, 'Limited evidence')
        report.incidents[0].joint_evidence = [measured]
        report.warnings = ['One file could not be read']
        self.assertEqual(self.joint(report).status, 'Limited evidence')

    def test_one_complete_recording_does_not_hide_incomplete_coverage(self):
        good = JointEvidence('Base', 'one.zip', sample_count=5, channels=['position', 'velocity', 'current'], current_both_bound_samples=5)
        bad = JointEvidence('Base', 'two.zip', sample_count=5, channels=['velocity'])
        report = DiagnosticReport(log_source_count=1, incidents=[Incident(joint_evidence=[good, bad])])
        self.assertEqual(self.joint(report).status, 'Limited evidence')

    def test_marker_program_line_and_command_are_kept_without_claiming_cause(self):
        f = flight(metadata={'incident_uptime': '102', 'program': 'Pickup', 'installation': 'Tool.installation'},
                   columns={'Actual velocity j0 [rad/s]': [0, 1, 2, 0, 0], 'Script control line': [2,2,3,3,4]})
        f.script = '# header\nmovej(a)\nmovel(b, a=2)\nstopj(2)'
        result = build_robot_status(analyze_data([event('C174A0')], [f]))
        self.assertEqual(len(result.program_areas), 1)
        area = result.program_areas[0]
        self.assertEqual(area.program, 'Pickup')
        self.assertEqual(area.area, 'Move transitions and blending')
        self.assertIn('movel(b, a=2)', ' '.join(area.evidence))
        self.assertIn('script line 3', ' '.join(area.evidence))
        self.assertIn('not proven root cause', area.association)
        self.assertTrue(set(area.recommendations).issubset(result.actions))

    def test_unmatched_recording_cannot_name_program_for_unrelated_log(self):
        f = flight(metadata={'incident_time': '2026-04-02 12:00:00', 'program': 'DifferentProgram'})
        result = build_robot_status(analyze_data([event('C174A0')], [f]))
        transitions = next(area for area in result.program_areas if area.area == 'Move transitions and blending')
        self.assertEqual(transitions.program, 'Program not identified')
        other = next(area for area in result.program_areas if area.program == 'DifferentProgram')
        self.assertIn('without a unique log association', other.association)
        self.assertNotIn('C174', ' '.join(other.evidence))

    def test_repeated_cases_do_not_duplicate_program_issues_or_actions(self):
        report = analyze_data([event('C174A0')], [flight(metadata={'program': 'Pickup'})])
        report.incidents.append(report.incidents[0])
        result = build_robot_status(report)
        self.assertEqual(len(result.program_areas), 1)
        self.assertEqual(result.program_areas[0].incident_ids, ['INC-001'])
        self.assertEqual(len(result.actions), len(set(result.actions)))
        self.assertNotIn('more than one', result.joints[0].summary)

    def test_distinct_recurrences_keep_all_case_ids_in_one_program_area(self):
        program = ProgramEvidence(program='Pickup')
        report = DiagnosticReport(incidents=[Incident(id='INC-001', primary_code='C174A0', program_evidence=[program]),
                                             Incident(id='INC-002', primary_code='C174A0', program_evidence=[program])])
        result = build_robot_status(report)
        self.assertEqual(len(result.program_areas), 1)
        self.assertEqual(result.program_areas[0].incident_ids, ['INC-001', 'INC-002'])

    def test_unknown_code_does_not_invent_joint_diagnosis(self):
        report = analyze_data([event('C999A0')], [])
        joint = self.joint(report)
        self.assertEqual(joint.status, 'Limited evidence')
        self.assertIn('cannot reliably classify', joint.summary)
        self.assertEqual(build_robot_status(report).overall_status, 'Controller review required')

    def test_blend_and_limit_codes_create_targeted_program_review(self):
        cases = (('C171A10', 'Blend configuration and waypoint continuity'),
                 ('C152', 'Path margin to configured limits'))
        for code, title in cases:
            report = DiagnosticReport(incidents=[Incident(id='INC-001', primary_code=code)])
            status = build_robot_status(report)
            self.assertEqual(status.program_areas[0].area, title)
            self.assertEqual(status.program_areas[0].program, 'Program not identified')
            self.assertFalse(any(joint.status == 'Needs attention' for joint in status.joints))

    def test_mixed_robot_serials_fail_closed(self):
        report = analyze_data([event()], [flight()])
        for context in ([{'serial':'R1'}, {'serial':'R2'}], [{'robot_id':'A'}, {'robot_id':'B'}]):
            report.robot_context = context
            result = build_robot_status(report)
            self.assertEqual(result.overall_status, 'Separate robot datasets')
            self.assertEqual({j.status for j in result.joints}, {'Not assessed'})
            self.assertEqual(result.program_areas, [])

    def test_client_coverage_excludes_internal_paths_and_memory_budgets(self):
        report = DiagnosticReport(source='/private/customer/folder', coverage=['Memory 128 MiB; /private/source'],
                                  warnings=['/private/broken.zip: failed'], log_source_count=1)
        result = build_robot_status(report)
        text = ' '.join(result.coverage + result.assessment + result.limitations)
        self.assertNotIn('/private', text)
        self.assertNotIn('128 MiB', text)
        self.assertIn('Source warnings', text)
        self.assertIs(result.diagnostic_report, report)

    def test_engine_retains_structured_temperature_without_inventing_threshold(self):
        report = analyze_data([], [flight(columns={'Temperature j0 [C]': [30, 31, 32, 33, 34]})])
        joint = self.joint(report)
        self.assertEqual(joint.status, 'Limited evidence')
        self.assertIn('34 °C', ' '.join(joint.evidence))
        self.assertIn('thermal limit was not evaluated', ' '.join(joint.evidence))


if __name__ == '__main__':
    unittest.main()
