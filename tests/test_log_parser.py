"""Parser and diagnostic regressions migrated from the retired graph generator."""
import tempfile
import unittest
from pathlib import Path
from log_parser import read_events, JOINTS, summarize


def line(code, time='2026-04-01 12:00:00', uptime='1s', source='-3', message='test'):
    return f'3.5 :: {uptime} :: {time} :: {source} :: {code} :: {message}\n'


class DiagnosticsTests(unittest.TestCase):
    def parse(self, text):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'log.txt'
            path.write_text(text, encoding='utf-8')
            metadata = {}
            events, skipped = read_events(path, metadata)
            return events, skipped, metadata

    def test_pairs_sessions_and_torque_mapping(self):
        events, _, _ = self.parse(line('C174A0:5')+line('C174A0:6')+
                                 line('C174A11:6')+'******** Log start\n'+line('C174A0:6'))
        self.assertEqual([e['joint'] for e in events], ['Base','Wrist 3','Base'])

    def test_coverage_includes_non_faults_and_global_context(self):
        events, skipped, meta = self.parse(line('C153A5:6','2026-04-03 12:00:00')+
            line('C0A0:0','2026-06-01 00:00:00')+line('C162A0:5')+line('C162A0:6'))
        self.assertEqual(meta['last_date'],'2026-06-01')
        self.assertEqual(len(meta['context']),1)
        report=summarize(events,JOINTS,meta)
        self.assertEqual(report['30']['cards'][5]['total'],0)
        self.assertEqual(report['all']['cards'][5]['previous'],1)
        self.assertEqual(skipped,0)

    def test_source_mapping_and_invalid_records(self):
        events, skipped, meta=self.parse(line('C75A1:6',source='114')+
            line('C75A1:6',source='-3')+line('C261A4:6',source='115')+
            line('C153A0:6',time='invalid'))
        self.assertEqual([e['joint'] for e in events],['Wrist 2','Wrist 3'])
        self.assertEqual((skipped,meta['unmapped']),(1,1))

    def test_rolling_window_boundary_and_session_isolation(self):
        events, _, meta=self.parse(line('C153A0:6','2026-04-01 00:00:00','1s')+
            line('C153A0:6','2026-04-01 07:59:59','2s')+
            line('C153A0:6','2026-04-01 08:00:00','3s')+
            '******** Log start\n'+line('C153A0:6','2026-04-01 08:00:01','1s'))
        card=summarize(events,JOINTS,meta)['all']['cards'][0]
        self.assertEqual(card['peak'],2)
        self.assertFalse(card['comparison_available'])

