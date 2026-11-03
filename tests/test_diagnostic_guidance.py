"""Regression checks for evidence-preserving diagnostic recommendations."""

import unittest

from diagnostic_guidance import guidance_for_codes


class DiagnosticGuidanceTests(unittest.TestCase):
    def test_zero_payload_codes_keep_collision_or_tracking_context(self):
        for code in ("C158A2", "C159A5:6"):
            with self.subTest(code=code):
                result = guidance_for_codes([code])
                self.assertTrue(any("configured payload of zero" in text for text in result["hypotheses"]))
                self.assertTrue(any("not itself proof" in text for text in result["counterevidence"]))
                self.assertTrue(any("physical contact" in text for text in result["counterevidence"]))

    def test_mixed_incident_keeps_distinct_checks_without_duplicates(self):
        result = guidance_for_codes(["C153A0", "C157A2", "C163A2", "C174A8", "C174A8"])
        self.assertTrue(any("target" not in text and "acceleration" in text for text in result["actions"]))
        self.assertTrue(any("qualified UR service" in text for text in result["actions"]))
        self.assertTrue(any("torque" in text for text in result["hypotheses"]))
        for field in ("hypotheses", "actions", "counterevidence", "references"):
            self.assertEqual(len(result[field]), len(set(result[field])))

    def test_unknown_code_does_not_acquire_known_code_guidance(self):
        result = guidance_for_codes(["C1570A1", "prefix C157A1", "C999A0"])
        self.assertEqual(result["hypotheses"], ())
        self.assertTrue(any("no validated" in text for text in result["counterevidence"]))
        self.assertTrue(any("complete error message" in text for text in result["actions"]))

    def test_singularity_is_not_inferred_from_velocity(self):
        result = guidance_for_codes(["C154"])
        self.assertTrue(any("insufficient" in text for text in result["counterevidence"]))
        self.assertTrue(any("straight TCP path" in text for text in result["actions"]))

    def test_blend_subtypes_do_not_all_mean_overlap(self):
        small = guidance_for_codes(['C171A1', 'C171A10'])
        self.assertTrue(any('larger radius' in text for text in small['actions']))
        self.assertFalse(any('smaller radius' in text for text in small['actions']))
        overlap = guidance_for_codes(['C171A11'])
        self.assertTrue(any('smaller radius' in text for text in overlap['actions']))
        family = guidance_for_codes(['C171'])
        self.assertFalse(any('smaller radius' in text or 'larger radius' in text for text in family['actions']))
        self.assertTrue(any('C171 alone does not establish' in text for text in family['counterevidence']))

    def test_position_limit_guidance_preserves_validated_safety_configuration(self):
        result = guidance_for_codes(['C150', 'C151', 'C152'])
        self.assertTrue(any('Preserve the risk-assessed limits' in text for text in result['actions']))
        self.assertTrue(any('not establish a mechanical' in text for text in result['counterevidence']))
        self.assertEqual(len(result['references']), 3)


if __name__ == "__main__":
    unittest.main()
