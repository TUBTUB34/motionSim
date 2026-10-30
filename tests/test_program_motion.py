"""Static motion calculations must stay conservative around runtime behavior."""
import math
from pathlib import Path
import tempfile
import unittest
from program_check import ProgramPair, ProgramSource, review_program, CATEGORIES
from program_motion import resolve, Pose, distance, source_definitions, definition_estimate


class ProgramMotionTests(unittest.TestCase):
    def review(self, script, limits=None):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'demo.script'
            path.write_text(script)
            return review_program(ProgramPair('demo', script=ProgramSource(path)), 'CB-Series / e-Series', limits)

    def findings(self, review, category, line=None):
        return [f for f in review.findings if f.category == CATEGORIES[category] and (line is None or f.line == line)]

    def test_assignments_arithmetic_aliases_and_reassignment(self):
        result = self.review('base=1.5\na=base*2\nv=sqrt(4)/2\nmovel(p[0,0,0,0,0,0], a=a, v=v)\na=.5\nmovel(p[.1,0,0,0,0,0], a=a, v=v)', {'tcp_velocity': .8})
        self.assertEqual(self.findings(result, 4, 4)[0].status, 'Review recommended')
        self.assertIn('a=3 m/s²', self.findings(result, 4, 4)[0].detail)
        self.assertEqual(self.findings(result, 4, 6)[0].status, 'Within guideline')
        self.assertIn('Exceeds user-supplied', self.findings(result, 7, 4)[0].detail)

    def test_explicit_acceleration_specs_override_general_guidance(self):
        result = self.review('movej(q, a=2, v=1)\nmovel(pose, a=1, v=.1)', {'joint_acceleration': 1, 'joint_velocity': .5, 'tcp_acceleration': .5})
        self.assertEqual(self.findings(result, 4, 1)[0].status, 'Review recommended')
        self.assertEqual(self.findings(result, 4, 2)[0].status, 'Review recommended')
        self.assertEqual(self.findings(result, 7, 2)[0].status, 'Manual review')
        for value in (-1, 0, float('inf'), float('nan'), True):
            with self.assertRaises(ValueError):
                self.review('movej(q)', {'joint_velocity': value})

    def test_multiline_pose_variables_distance_and_overlap(self):
        result = self.review('first=p[0,0,0,\n0,0,0]\nx=.03\nsecond=p[x, .04, 0, 0,0,0]\nradius=.03\nmovel(first,r=radius)\nmovel(second,r=radius)\nmovel(p[.04,.04,0,0,0,0],r=0)')
        self.assertIn('move distance=0.05 m', self.findings(result, 8, 7)[0].detail)
        blend = self.findings(result, 5, 7)[0]
        self.assertEqual(blend.status, 'Review recommended')
        self.assertIn('Radius exceeds outgoing', blend.detail)
        self.assertIn('potential geometric overlap', blend.detail)
        self.assertIn('distance=0.06 m across 2 of 3', self.findings(result, 8, 0)[0].detail)

    def test_control_flow_helper_calls_and_functions_do_not_create_false_paths(self):
        scripts = (
            'a=1\nif input:\n a=20\nend\nmovel(pose,a=a)',
            'a=1\nhelper()\nmovel(pose,a=a)',
            'a=1\ndef helper():\n movel(pose,a=a)\nend',
            'a=1\nmovel(helper(),a=a)',
            'a=1; a=20; movel(pose,a=a)',
            'a=1\nthread mutator():\n a=20\nend\nmovel(pose,a=a)',
            'h=run external_thread()\na=1\nmovel(pose,a=a)',
            'def sqrt(x):\n return 20\nend\na=sqrt(1)\nmovel(pose,a=a)',
            'x=[1,2]\ny=x\nx[0]=20\nmovel(pose,a=y[0])',
        )
        for script in scripts:
            with self.subTest(script=script):
                self.assertEqual(self.findings(self.review(script), 4)[0].status, 'Manual review')
        result = self.review('movel(p[0,0,0,0,0,0])\nif input:\n movel(p[.01,0,0,0,0,0],r=.1)\nend\nmovel(p[.02,0,0,0,0,0])')
        self.assertIn('across 0 of 3', self.findings(result, 8, 0)[0].detail)

    def test_unknown_values_and_joint_targets_are_never_geometric_passes(self):
        result = self.review('q=[0,0,0,0,0,0]\nmovej(q,r=.1)\nmovej([1,0,0,0,0,0],r=.1)\nmovel(pose,r=radius)')
        self.assertTrue(all(f.status == 'Manual review' for f in self.findings(result, 8)))
        self.assertTrue(all(f.status == 'Manual review' for f in self.findings(result, 5)))

    def test_circular_arc_distance_and_correct_argument_positions(self):
        result = self.review('movel(p[1,0,0,0,0,0])\nmovec(p[0,1,0,0,0,0],p[-1,0,0,0,0,0],3,.5,.1)', {'tcp_velocity': .4})
        self.assertIn('distance=3.14159 m', self.findings(result, 8, 2)[0].detail)
        self.assertEqual(self.findings(result, 4, 2)[0].status, 'Review recommended')
        self.assertEqual(self.findings(result, 7, 2)[0].status, 'Review recommended')
        self.assertIn('r=0.1 m', self.findings(result, 5, 2)[0].detail)

    def test_large_circle_does_not_overflow_into_zero_length(self):
        a = Pose((1e100, 0., 0., 0., 0., 0.))
        via = Pose((0., 1e100, 0., 0., 0., 0.))
        b = Pose((-1e100, 0., 0., 0., 0., 0.))
        self.assertAlmostEqual(distance(a, b, via) / 1e100, math.pi)

    def test_speed_vectors_optimove_ranges_and_servo_semantics(self):
        result = self.review('speedj([0,-2,0,0,0,0],3)\nspeedl([.3,.4,0,0,0,1],2,aRot=-1)\noptimovej(q,a=1.2,v=.5)\noptimovel(pose,a=.5,v=2)\nservoj(q,a=999,v=999)', {'joint_velocity': 1, 'tcp_velocity': .4})
        self.assertEqual(self.findings(result, 7, 1)[0].status, 'Review recommended')
        self.assertTrue(any('v=0.5 m/s' in f.detail and f.status == 'Review recommended' for f in self.findings(result, 7, 2)))
        self.assertTrue(any('aRot=-1' in f.detail and f.status == 'Review recommended' for f in self.findings(result, 4, 2)))
        self.assertEqual(self.findings(result, 4, 3)[0].status, 'Review recommended')
        self.assertEqual(self.findings(result, 7, 4)[0].status, 'Review recommended')
        self.assertIn('unused', self.findings(result, 7, 5)[0].detail)

    def test_time_override_and_invalid_expressions(self):
        result = self.review('duration=2\nmovel(pose,a=99,v=99,t=duration)', {'tcp_velocity': 1})
        self.assertEqual(self.findings(result, 4, 2)[0].status, 'Manual review')
        self.assertIn('overrides', self.findings(result, 7, 2)[0].detail)
        for expression in ('__import__("os").system("false")', '1e999', '2**10000000', '[1]*10000000', '1/0', 'True', 'x.y', 'sqrt(-1)'):
            self.assertIsNone(resolve(expression, {}), expression)
        self.assertAlmostEqual(resolve('d2r(180)', {}), math.pi)
        self.assertEqual(resolve('v[1]*2', {'v': (1., 3.)}), 6.)
        self.assertIsInstance(resolve('p[0,0,0,0,0,0]', {}), Pose)

    def test_later_program_initializers_are_conditional_calculations_not_runtime_facts(self):
        script = '''def program():
  thread timer():
    sleep(.1)
  end
  def routine():
    movel(target, v=vl2*v_scale, a=al2*a_scale)
    movel(target, v=vr2*v_scale, a=ar2*a_scale)
    movej(q, v=vr2*v_scale, a=ar2*a_scale)
  end
  a_scale=2.5
  v_scale=4
  vl2=1.25
  al2=1.25
  vr2=d2r(100)
  ar2=d2r(100)
  routine()
end'''
        result = self.review(script, {'tcp_velocity': 2.0, 'joint_velocity': 5.0})
        acceleration = self.findings(result, 4)
        self.assertEqual([f.status for f in acceleration], ['Review recommended', 'Review recommended', 'Manual review'])
        self.assertIn('a=3.125 m/s²', acceleration[0].detail)
        self.assertIn('a=4.36332 m/s²', acceleration[1].detail)
        self.assertIn('a=4.36332 rad/s²', acceleration[2].detail)
        self.assertTrue(all('Source-definition estimate' in f.detail for f in acceleration))
        self.assertIn('Within UR suggested', acceleration[2].detail)
        velocity = self.findings(result, 7)
        self.assertTrue(all(f.status == 'Review recommended' for f in velocity))
        self.assertIn('v=6.98132 m/s', velocity[1].detail)

    def test_source_initializers_exclude_mutation_branches_and_parameter_shadowing(self):
        scripts = (
            'a=1\nif input:\n a=3\nend',
            'if input:\n a=1\nend',
            'a=1\ndef helper(a):\n return a\nend',
            'a=[1,2]\nb=a\na[0]=3',
            'a=struct(value=1)\na.value=3',
            'local a=1',
            'a=b*2\nb=1',
            'def unrelated():\n a=1\nend\nmovel(target,a=a)',
        )
        for script in scripts:
            with self.subTest(script=script):
                self.assertNotIn('a', source_definitions(script))
        definitions = source_definitions('scale=2\ndef d2r(x):\n return 9\nend')
        self.assertIsNone(definition_estimate('d2r(60)*scale', definitions)[0])

    def test_nested_payload_and_tcp_struct_initializers_are_reported(self):
        script = '''def program():
  def routine():
    set_target_payload(PAYLOADS.FULL.M, PAYLOADS.FULL.COG)
    set_payload(PAYLOADS.EMPTY.M, PAYLOADS.EMPTY.COG)
    set_tcp(TCPS.UNDER_FLANGE)
  end
  PAYLOADS=struct(EMPTY=struct(M=4.7,COG=[0,-.01,.1]),FULL=struct(M=6.3,COG=[0,-.01,.1]))
  TCPS=struct(UNDER_FLANGE=p[0,0,.247,0,d2r(179.99),0])
  routine()
end'''
        result = self.review(script)
        self.assertIn('Mass: 6.3 kg', self.findings(result, 0, 3)[0].detail)
        self.assertIn('Mass: 4.7 kg', self.findings(result, 1, 4)[0].detail)
        self.assertIn('[0.0, -0.01, 0.1]', self.findings(result, 0, 3)[0].detail)
        self.assertIn('3.1414181206645937', self.findings(result, 2, 5)[0].detail)
        self.assertIn('Source-definition estimate', self.findings(result, 2, 5)[0].detail)
        self.assertIsNone(resolve('v.__class__', {'v': 1}))
        self.assertIsNone(resolve('struct(**x)', {}))

    def test_relative_pose_geometry_and_branch_exclusions(self):
        script = '''thread background():
  sleep(.1)
end
if selection:
  movel(transition_pose,r=.0025)
  movel(pose_trans(transition_pose,p[0,-.021,.042,0,0,0]),r=.0025)
else:
  movel(transition_pose,r=.0025)
end'''
        result = self.review(script)
        self.assertIn('distance=0.0469574 m', self.findings(result, 8, 6)[0].detail)
        self.assertIn('Adjacent radii total=0.005 m', self.findings(result, 5, 6)[0].detail)
        self.assertNotEqual('Review recommended', self.findings(result, 5, 6)[0].status)
        self.assertIn('across 1 of 3', self.findings(result, 8, 0)[0].detail)
        for script in (
            'movel(target)\ntarget=runtime()\nmovel(pose_trans(target,p[.01,0,0,0,0,0]))',
            'movel(target)\ntarget=another\nmovel(pose_trans(target,p[.01,0,0,0,0,0]))',
            'movel(get_actual_tcp_pose())\nmovel(pose_trans(get_actual_tcp_pose(),p[.01,0,0,0,0,0]))',
            'if input:\n movel(p[0,0,0,0,0,0],r=.05)\nelse:\n movel(p[0,.04445,0,0,0,0],r=.05)\nend',
        ):
            with self.subTest(script=script):
                self.assertIn('across 0 of 2', self.findings(self.review(script), 8, 0)[0].detail)


if __name__ == '__main__':
    unittest.main()
