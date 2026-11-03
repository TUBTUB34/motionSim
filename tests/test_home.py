"""Home summaries must distinguish ongoing work from previously loaded evidence."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from home_view import workspace_summary


def status(text):
    return SimpleNamespace(get=lambda: text)


class HomeSummaryTests(unittest.TestCase):
    def test_tk_busy_method_is_not_an_active_job(self):
        app = SimpleNamespace(log_window=SimpleNamespace(
            busy=lambda: None, job=None, status=status('Previous operation'),
            data=SimpleNamespace(events=[1], source='saved logs')))
        self.assertEqual(workspace_summary(app)['Logs'], '1 monitored incidents · saved logs')

    def test_active_work_takes_priority_then_returns_to_retained_evidence(self):
        app = SimpleNamespace(
            log_window=SimpleNamespace(job=object(), status=status('Reading replacement log…'),
                data=SimpleNamespace(events=[1, 2], source='previous logs')),
            flight_window=SimpleNamespace(loading=False, exporting=True,
                status=status('Exporting complete recording…'), report=SimpleNamespace(path=Path('/saved/flight.zip'))),
            flight_retrieval_window=SimpleNamespace(busy=True, status=status('Downloading recording 2 of 3…')),
            program_check_window=SimpleNamespace(busy=True, status=status('Reviewing new sources…'),
                review=SimpleNamespace(pair=SimpleNamespace(name='previous program'))),
            diagnostic_window=SimpleNamespace(busy=True, status=status('Collecting robot evidence…'), report=object()),
            backup_page=SimpleNamespace(job=object(), status=status('Downloading backup…')),
        )
        summaries = workspace_summary(app)
        for name, expected in (
            ('Logs', 'Reading replacement log…'),
            ('Flight recording', 'Exporting complete recording…'),
            ('Flight downloads', 'Downloading recording 2 of 3…'),
            ('Program review', 'Reviewing new sources…'),
            ('Robot Status', 'Collecting robot evidence…'),
            ('Backup', 'Downloading backup…'),
        ):
            with self.subTest(workflow=name):
                self.assertEqual(summaries.get(name), expected)

        app.log_window.job = None
        app.flight_window.exporting = False
        app.program_check_window.busy = False
        app.diagnostic_window.busy = False
        app.backup_page.job = None
        app.backup_page.status = status('Backup saved: cell-A.tar')
        summaries = workspace_summary(app)
        self.assertEqual(summaries['Logs'], '2 monitored incidents · previous logs')
        self.assertEqual(summaries['Flight recording'], 'flight.zip')
        self.assertEqual(summaries['Program review'], 'previous program')
        self.assertEqual(summaries['Robot Status'], 'Evidence loaded · Open to review coverage')
        self.assertEqual(summaries['Backup'], 'Backup saved: cell-A.tar')


if __name__ == '__main__':
    unittest.main()
