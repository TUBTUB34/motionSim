import gzip
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from program_check import (ProgramPair, ProgramSource, discover_programs, review_program,
                           calls_in, CATEGORIES, literal)


class ProgramCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def pair(self, script, version='5.24.0'):
        (self.base/'demo.urp').write_bytes(gzip.compress(f'<URProgram name="Demo" lastSavedIn="{version}"/>'.encode()))
        (self.base/'demo.script').write_text(script, encoding='utf-8')
        return discover_programs(self.base)[0]

    def test_local_program_names_exclude_parent_paths_and_pair_within_each_folder(self):
        nested = self.base / 'another cell'
        nested.mkdir()
        (self.base / 'demo.urp').write_text('<URProgram/>')
        (self.base / 'demo.script').write_text('movej(q)')
        (nested / 'demo.urp').write_text('<URProgram/>')
        pairs = discover_programs(self.base)
        self.assertEqual([pair.name for pair in pairs], ['demo', 'demo'])
        by_parent = {pair.urp.path.parent: pair for pair in pairs}
        self.assertEqual(by_parent[self.base].script.path, self.base / 'demo.script')
        self.assertIsNone(by_parent[nested].script)
        self.assertEqual(discover_programs(self.base / 'demo.urp')[0].name, 'demo')

    def test_archive_pairs_exact_folder_and_stem_without_extracting(self):
        path = self.base/'backup.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('files/programs/demo.urp', gzip.compress(b'<URProgram lastSavedIn="5.24"/>'))
            archive.writestr('files/programs/demo.script', 'movel(p[0,0,0,0,0,0], a=3)')
            archive.writestr('other/demo.script', 'movej(q)')
            archive.writestr('../bad.script', 'movej(q)')
        pairs = discover_programs(path)
        self.assertEqual(len(pairs), 2)
        paired = next(p for p in pairs if p.urp)
        result = review_program(paired)
        self.assertEqual(result.counts['movel'], 1)
        self.assertEqual(result.findings[4].category, CATEGORIES[4])
        self.assertTrue(any(f.status == 'Review recommended' and f.line == 1 for f in result.findings))
        self.assertFalse((self.base/'files').exists())
        self.assertEqual(len(result.hashes), 2)

    def test_lexer_ignores_comments_strings_labels_and_definitions(self):
        source = '# movej(q)\ntextmsg("movel(p, a=8)")\n$ 1 "movej(q)"\ndef movej(q):\nend\nmovel(\n pose_trans(p[0,0,0,0,0,0], p[1,0,0,0,0,0]),\n a=3, # speedj(q)\n r=0.02)\n'
        calls = calls_in(source)
        self.assertEqual([(c.name, c.line) for c in calls], [('movel', 6)])
        result = review_program(self.pair(source))
        self.assertEqual(next(f.status for f in result.findings if f.category == CATEGORIES[4]), 'Review recommended')

    def test_acceleration_units_time_priority_dynamic_and_optimove(self):
        script = '\n'.join(('movej(q, a=14)', 'movel(p, a=2.5)', 'movel(p, a=9, t=2)',
                            'movej(q, a=ACCEL)', 'optimovel(p, a=0.8)', 'movep(p, 1.2, 0.25, 0.02)'))
        result = review_program(self.pair(script))
        acceleration = [f for f in result.findings if f.category == CATEGORIES[4]]
        byline = {f.line: f for f in acceleration}
        self.assertEqual(byline[1].status, 'Review recommended')
        self.assertEqual(byline[2].status, 'Within guideline')
        self.assertIn('overrides', byline[3].detail)
        self.assertEqual(byline[4].status, 'Manual review')
        self.assertEqual(byline[5].status, 'Manual review')
        self.assertEqual(byline[6].status, 'Within guideline')
        self.assertTrue(any(f.line == 6 and '0.02 m' in f.detail for f in result.findings))

    def test_unconfirmed_family_never_receives_numeric_pass(self):
        result = review_program(self.pair('movej(q, a=1)', version='10.10'))
        self.assertFalse(any(f.status == 'Within guideline' for f in result.findings))
        confirmed = review_program(result.pair, 'CB-Series / e-Series')
        self.assertTrue(any(f.status == 'Within guideline' for f in confirmed.findings))

    def test_urp_only_is_explicitly_limited(self):
        pair = self.pair('movej(q)')
        result = review_program(ProgramPair('demo', urp=pair.urp))
        self.assertEqual(result.script_lines, 0)
        self.assertTrue(all(status == 'Manual review' for _, status in result.checklist))
        self.assertTrue(all('No matching generated .script' in f.detail for f in result.findings))

    def test_no_proven_blend_fault_or_execution_claim_for_version_two(self):
        result = review_program(self.pair('motion_version_set(2)\nset_payload(3.6, [0,0,.1])\nmovel(pose, r=.2)'))
        self.assertTrue(any('Version 2' in f.detail for f in result.findings))
        self.assertFalse(any(f.status == 'Review recommended' for f in result.findings if f.category == CATEGORIES[5]))
        self.assertIn('execution order', result.findings[0].detail)

    def test_source_read_and_xml_limits(self):
        pair = self.pair('movej(q)')
        with patch('program_check.MAX_BYTES', 4):
            with self.assertRaises(ValueError):
                pair.script.read()
        pair.urp.path.write_text('<!DOCTYPE doc [<!ENTITY x "attack">]><URProgram>&x;</URProgram>')
        with self.assertRaises(Exception):
            review_program(pair)
        self.assertIsNone(literal('__import__("os").system("false")'))
        self.assertIsNone(literal('1e999'))

    def test_folder_matches_referenced_installation_and_records_saved_context(self):
        self.pair('movel(p, a=3)')
        (self.base/'demo.urp').write_text('<URProgram lastSavedIn="5.24" installationRelativePath="tool"/>')
        installation = self.base/'tool.installation'
        installation.write_text('<Installation><TCPSettings toolPayload="2" toolPayloadCoG="0,0,.1"><tcp name="tip" offset="0,0,.2,0,0,0"/></TCPSettings></Installation>')
        (self.base/'unused.installation').write_text('<Installation/>')
        pair = discover_programs(self.base)[0]
        result = review_program(pair)
        self.assertEqual(result.metadata['reviewInstallation'], str(installation))
        self.assertIn(str(installation), result.hashes)
        self.assertTrue(any('2.0 kg' in f.detail for f in result.findings))
        self.assertTrue(any('tip' in f.detail and '0.2' in f.detail for f in result.findings))
        self.assertTrue(any('not interpreted as an active speed' in f.detail for f in result.findings))

    def test_installation_missing_ambiguous_and_invalid_do_not_discard_script_review(self):
        self.pair('movel(p, a=3)')
        for name in ('one', 'two'):
            (self.base/f'{name}.installation').write_text('<Installation/>')
        result = review_program(discover_programs(self.base)[0])
        self.assertNotIn('reviewInstallation', result.metadata)
        self.assertTrue(any('ambiguous' in f.detail for f in result.findings))
        (self.base/'demo.urp').write_text('<URProgram lastSavedIn="5.24" installation="missing"/>')
        result = review_program(discover_programs(self.base)[0])
        self.assertTrue(any('not found' in f.detail for f in result.findings))
        (self.base/'missing.installation').write_text('not XML')
        result = review_program(discover_programs(self.base)[0])
        self.assertTrue(any('could not be read' in f.detail for f in result.findings))
        self.assertTrue(any(f.line == 1 and f.status == 'Review recommended' for f in result.findings))

    def test_archive_installation_reference_uses_archive_members_without_extraction(self):
        archive = self.base/'sources.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('programs/job.urp', '<URProgram installationRelativePath="default" lastSavedIn="5.24"/>')
            z.writestr('programs/job.script', 'movej(q)')
            z.writestr('programs/default.installation', '<Installation/>')
            z.writestr('other/default.installation', '<Installation/>')
        result = review_program(discover_programs(archive)[0])
        self.assertIn('programs/default.installation', result.metadata['reviewInstallation'])
        self.assertFalse((self.base/'programs').exists())

    def test_local_parent_installation_and_untrusted_derived_metadata(self):
        subfolder = self.base/'subfolder'
        subfolder.mkdir()
        (subfolder/'job.urp').write_text('<URProgram lastSavedIn="5.24" installationRelativePath="../default" reviewInstallation="forged"/>')
        (subfolder/'job.script').write_text('movej(q)')
        installation = self.base/'default.installation'
        installation.write_text('<Installation/>')
        result = review_program(discover_programs(self.base)[0])
        self.assertEqual(result.metadata['reviewInstallation'], str(installation))
        installation.unlink()
        (subfolder/'job.urp').write_text('<URProgram lastSavedIn="5.24" reviewInstallation="forged" reviewInstallationNote="forged"/>')
        result = review_program(discover_programs(self.base)[0])
        self.assertNotIn('reviewInstallation', result.metadata)
        self.assertNotIn('reviewInstallationNote', result.metadata)

    def test_payload_units_and_invalid_literal_configuration(self):
        result = review_program(self.pair('set_target_payload(3.6, [.001,0,.132], [.013,.013,.013,0,0,0])\nset_tcp(p[0,0,.247,0,3.1414,0])\nset_target_payload(-2, [0,0], [-1,0,0,0,0,0])'))
        first = result.findings[0]
        self.assertIn('3.6 kg', first.detail)
        self.assertIn('kg·m²', first.detail)
        self.assertEqual(first.status, 'Source observation')
        invalid = next(f for f in result.findings if f.line == 3)
        self.assertEqual(invalid.status, 'Review recommended')
        self.assertIn('expected 3', invalid.detail)
        self.assertTrue(any('rotation vector in rad' in f.detail for f in result.findings))

    def test_supplied_configuration_counts_real_calls_not_embedded_text(self):
        archive = Path(__file__).resolve().parents[1] / 'assets/flightreports/robot_configuration20261014_19_16_56.zip'
        if not archive.exists():
            self.skipTest('Local reference archive is not distributed with source tests')
        pair = next(p for p in discover_programs(archive) if p.name == 'App_DoughFactory')
        result = review_program(pair)
        self.assertEqual(result.counts['movej'], 27)
        self.assertEqual(result.counts['movel'], 35)
        self.assertEqual(result.counts['optimovej'], 9)
        self.assertTrue(any(f.line == 448 and f.status == 'Review recommended' for f in result.findings))
        self.assertEqual(result.metadata['lastSavedIn'], '5.24.0')

    def test_pdf_contains_source_evidence_and_urp_only_limitation(self):
        from program_check_pdf import export_program_pdf
        result = review_program(self.pair('set_tcp(p[0,0,.1,0,0,0])\nmovel(p, a=3)'))
        destination = self.base/'review.pdf'
        export_program_pdf(result, destination)
        self.assertTrue(destination.read_bytes().startswith(b'%PDF'))
        self.assertGreater(destination.stat().st_size, 12000)
        self.assertEqual(list(self.base.glob('tmp*.pdf')), [])

    def test_workshop_small_blends_remain_contextual_review_not_fault(self):
        result = review_program(self.pair('\n'.join((
            'movel(pose, r=0)', 'movel(pose, r=.01)', 'movej(q, r=.015)',
            'movep(pose, r=.002)', 'movel(pose, r=process_radius)',
            '# movel(pose, r=.001)', 'textmsg("movel(pose,r=.001)")'))))
        small = [f for f in result.findings if 'workshop suggests about 15 mm' in f.detail]
        self.assertEqual([f.line for f in small], [2])
        self.assertEqual(small[0].status, 'Manual review')
        self.assertIn('intentional for clearance', small[0].detail)
        self.assertEqual(small[0].reference, 11)
        unknown = review_program(result.pair, 'Unconfirmed')
        self.assertFalse(any('workshop suggests about 15 mm' in f.detail for f in unknown.findings))

    def test_payload_transition_units_invalid_value_and_inertia_guidance(self):
        result = review_program(self.pair('\n'.join((
            'set_target_payload(2, [0,0,.1])',
            'set_target_payload(3, [0,0,.1], [.1,.1,.1,0,0,0], transition_time=1.5)',
            'set_target_payload(2, [0,0,.1], transition_time=-1)',
            'set_target_payload(2, [0,0,.1], transition_time=runtime_time)'))))
        source = [f for f in result.findings if f.category in CATEGORIES[:2] and f.line]
        self.assertIn('transition_time: 1.5 s', source[1].detail)
        self.assertEqual(source[2].status, 'Review recommended')
        self.assertIn('non-negative', source[2].detail)
        self.assertIn('dynamic or unresolved', source[3].detail)
        prompts = [f for f in result.findings if f.category == CATEGORIES[9]]
        self.assertEqual(len(prompts), 4)
        self.assertTrue(all(f.status == 'Manual review' for f in prompts))
        self.assertIn('not automatically a fault', prompts[0].detail)
        self.assertIn('program continues', prompts[1].detail)

    def test_process_waits_and_diagnostics_preserve_source_execution_uncertainty(self):
        result = review_program(self.pair('movep(pose, r=.002)\nsleep(.1)\nposition_deviation_warning(True, .4)\npause_on_error_code(171)\nsocket_open("192.0.2.1", 30002)\nsocket_close()'))
        process = next(f for f in result.findings if f.category == CATEGORIES[10])
        self.assertIn('controlled stopl', process.detail)
        self.assertIn('does not prove', process.detail)
        diagnostic = [f for f in result.findings if f.category == CATEGORIES[11] and f.status == 'Source observation']
        self.assertEqual([f.line for f in diagnostic], [3, 4])
        self.assertTrue(all('not proven' in f.detail for f in diagnostic))
        self.assertTrue(any('counts do not establish how often' in f.detail for f in result.findings))

    def test_lexer_diagnostic_commands_ignore_strings_comments_and_declarations(self):
        script = ('# pause_on_error_code(171)\ntextmsg("socket_open()")\n'
                  'def position_deviation_warning(enabled):\nend\nsync()\n'
                  'position_deviation_warning(True, .4)')
        self.assertEqual([(c.name, c.line) for c in calls_in(script)], [('sync', 5), ('position_deviation_warning', 6)])


if __name__ == '__main__':
    unittest.main()
