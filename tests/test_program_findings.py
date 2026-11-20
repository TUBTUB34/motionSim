"""Program Check findings: severity, fixes with target values, observations and score."""
import unittest

from motionstudio.programs.check import CATEGORIES, Finding, ProgramPair, ProgramReview
from motionstudio.programs.findings import fix_for, program_report


def review(findings):
    return ProgramReview(ProgramPair('Fixture'), {}, {}, 10, [], findings, 'CB-Series / e-Series deployment guidance')


class ProgramFindingTests(unittest.TestCase):
    def test_findings_are_rated_ordered_and_scored(self):
        result = program_report(review([
            Finding(CATEGORIES[4], 'Review recommended', 'Configured a=3 m/s². Exceeds UR suggested starting value ≤2.5 m/s². This is not a safety limit or measured acceleration.', 448, 'movel(p, a=3.0)', 0),
            Finding(CATEGORIES[0], 'Review recommended', 'First source occurrence; invalid mass.', 12, 'set_payload(-1)', 4),
            Finding(CATEGORIES[7], 'Manual review', 'Enter the applicable TCP velocity specification to check this value.', 20),
            Finding(CATEGORIES[4], 'Within guideline', 'Configured a=1 m/s². Within UR suggested starting value ≤2.5 m/s².', 30),
        ]))
        self.assertEqual([(f.id, f.severity, f.category) for f in result.findings],
                         [('P-01', 'High', CATEGORIES[0]), ('P-02', 'Medium', CATEGORIES[4])])
        self.assertEqual(result.score, 100 - 10 - 5)
        self.assertEqual([o.category for o in result.observations], [CATEGORIES[7]])
        self.assertEqual(len(result.values), 1)
        self.assertIn('Lower a to 2.5 m/s² or less', result.findings[1].fix)
        self.assertEqual(result.findings[1].location, 'Script line 448')
        self.assertTrue(result.actions()[0].endswith('[P-01]'))

    def test_specification_and_blend_fixes(self):
        self.assertIn('Lower v to 0.5 m/s or less', fix_for(CATEGORIES[7], 'Configured v=0.8 m/s. Exceeds user-supplied specification ≤0.5 m/s. Configured'))
        self.assertIn('half of each neighbouring move distance', fix_for(CATEGORIES[5], 'Adjacent radii exceed connecting move distance'))

    def test_clean_program_scores_100(self):
        self.assertEqual(program_report(review([])).score, 100)


if __name__ == '__main__':
    unittest.main()
