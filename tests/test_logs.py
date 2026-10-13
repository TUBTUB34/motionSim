import io
import errno
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

from log_data import MAX_LOG_BYTES, chart_series, load_log, select_events
from log_transfer import DOWNLOAD_IDLE_TIMEOUT, LogConnection, LogJob


def line(code='C153A0:6', day='2026-04-01', uptime='1s'):
    return f'3.5 :: {uptime} :: {day} 12:00:00 :: 110 :: {code} :: Example incident\n'


class LogTests(unittest.TestCase):
    def test_fetched_logs_can_be_saved_as_original_bytes(self):
        content = line().encode()
        job, _, _ = self.make_job(content)
        with tempfile.TemporaryDirectory() as folder:
            job.save_path = Path(folder) / 'chosen.txt'
            job.run()
            self.assertEqual(job.save_path.read_bytes(), content)
            self.assertEqual(job.save_path.with_suffix('.bak').read_bytes(), content)
            kind, data = self.results(job)[-1]
            self.assertEqual(kind, 'loaded')
            self.assertEqual(len(data.metadata['saved_logs']), 2)
            self.assertEqual(len(list(Path(folder).iterdir())), 2)

    def test_log_save_failure_still_loads_graph(self):
        job, _, _ = self.make_job(line().encode())
        with tempfile.TemporaryDirectory() as folder:
            job.save_path = Path(folder) / 'missing' / 'chosen.txt'
            job.run()
        kind, data = self.results(job)[-1]
        self.assertEqual(kind, 'loaded')
        self.assertTrue(data.metadata['save_error'])
        self.assertEqual(len(data.events), 1)

    def test_missing_remote_backup_does_not_mix_with_old_local_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'chosen.txt'
            backup = destination.with_suffix('.bak')
            backup.write_bytes(b'old backup')
            source = Path(folder) / 'download.txt'
            source.write_bytes(b'new log')
            job = LogJob(save_path=destination)
            with self.assertRaisesRegex(ValueError, 'older .bak'):
                job.save_downloads([source])
            self.assertEqual(backup.read_bytes(), b'old backup')
            self.assertFalse(destination.exists())

    def test_aggregate_totals_respect_filters_and_separate_codes(self):
        events = [dict(date=day, joint=joint, code=code, category='Collision')
                  for day, joint, code in [('2026-04-01', 'Base', 'C157A0'),
                                           ('2026-04-02', 'Base', 'C157A0'),
                                           ('2026-04-02', 'Shoulder', 'C157A1')]]
        labels, series, label = chart_series(events, aggregate=True)
        self.assertEqual([str(day) for day in labels], ['2026-04-01', '2026-04-02'])
        self.assertEqual(series['Base'], [1, 2])
        self.assertEqual(series['Shoulder'], [0, 1])
        selected = select_events(events, start='2026-04-02', joint='Base')
        _, series, _ = chart_series(selected, split_code=True, aggregate=True)
        self.assertEqual(series, {'Base · C157A0': [1]})
        self.assertEqual(chart_series([], aggregate=True), ([], {}, 'Day'))
        many_codes = [dict(date='2026-04-01', joint='Base', code=str(n)) for n in range(30)]
        _, series, _ = chart_series(many_codes, split_code=True, aggregate=True)
        self.assertEqual(len(series), 18)
        self.assertEqual(sum(sum(counts) for counts in series.values()), 30)

    def test_aggregate_is_running_total_and_carries_across_empty_days(self):
        events = [dict(date=day, joint='Base', code='C153A0')
                  for day, count in [('2026-04-01', 5), ('2026-04-02', 10)]
                  for _ in range(count)]
        _, daily, _ = chart_series(events, end='2026-04-03')
        _, cumulative, _ = chart_series(events, end='2026-04-03', aggregate=True)
        self.assertEqual(daily['Base'], [5, 10, 0])
        self.assertEqual(cumulative['Base'], [5, 15, 15])
        for period in ('Week', 'Month'):
            with self.subTest(period=period):
                _, counts, _ = chart_series(events, period, end='2026-06-01', aggregate=True)
                self.assertTrue(all(value == 15 for value in counts['Base']))

    def test_repeated_undated_session_headers_keep_distinct_incidents(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log.txt'
            path.write_text(('******** Log start\n' + line()) * 2)
            self.assertEqual(len(load_log(path).events), 2)

    def test_parser_cancellation_propagates(self):
        from log_parser import read_events
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log.txt'
            path.write_text(line())
            cancel = MagicMock(side_effect=InterruptedError('cancelled'))
            with self.assertRaises(InterruptedError):
                read_events([path], check_cancel=cancel)
            cancel.assert_called_once_with()

    def test_parse_deduplicate_and_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log.txt'
            path.write_text(line() + line('C153A0:5') + line('C157A1:6', uptime='2s'))
            data = load_log(path)
        self.assertEqual(len(data.events), 2)
        self.assertEqual(len(select_events(data.events, joint='Shoulder')), 1)
        self.assertEqual(len(select_events(data.events, category='Collision')), 1)
        self.assertEqual(data.diagnostics['all']['cards'][0]['total'], 1)
        with self.assertRaises(ValueError):
            select_events(data.events, start='2026-04-02', end='2026-04-01')
        with self.assertRaises(ValueError):
            select_events(data.events, start='bad')

    def test_zero_fill_calendar_and_bounds(self):
        events = [dict(date='2026-01-31', joint='Base', code='C153A0'),
                  dict(date='2026-03-01', joint='Base', code='C153A0')]
        labels, series, _ = chart_series(events, 'Month')
        self.assertEqual([str(x) for x in labels], ['2026-01-01', '2026-02-01', '2026-03-01'])
        self.assertEqual(series['Base'], [1, 0, 1])
        labels, series, _ = chart_series(events, start='1900-01-01', end='2100-01-01')
        self.assertLessEqual(len(labels), 366)
        self.assertEqual(sum(series['Base']), 2)
        labels, series, _ = chart_series(events, 'Week')
        self.assertTrue(all(x.weekday() == 0 for x in labels))
        events = [dict(date='2026-04-01', joint='Base', code=str(n)) for n in range(30)]
        _, series, _ = chart_series(events, split_code=True)
        self.assertEqual(len(series), 18)
        self.assertEqual(sum(sum(v) for v in series.values()), 30)

    def test_invalid_connection_and_secret_repr(self):
        with self.assertRaises(ValueError):
            LogConnection('bad', 'root', 'secret')
        with self.assertRaises(ValueError):
            LogConnection('127.0.0.1', 'root', '')
        with self.assertRaises(ValueError):
            LogConnection('127.0.0.1', 'root', 'secret', '../log')
        self.assertNotIn('secret', repr(LogConnection('127.0.0.1', 'root', 'secret')))

    def make_job(self, content=None):
        client = MagicMock()
        sftp = client.open_sftp.return_value.__enter__.return_value
        sftp.stat.return_value.st_size = len(content or b'')
        sftp.open.side_effect = lambda *args: io.BytesIO(content or b'')
        job = LogJob(connection=LogConnection('127.0.0.1', 'root', 'secret'), client_factory=lambda: client)
        return job, client, sftp

    def results(self, job):
        return list(job.events.queue)

    def test_progressing_download_can_exceed_old_deadline_and_reports_speed(self):
        content = line().encode() + b'\n' * 180000
        job, _, sftp = self.make_job(content)
        clock = [0.]
        class SlowStream(io.BytesIO):
            def read(self, size):
                clock[0] += 30
                return super().read(size)
        sftp.open.side_effect = lambda *args: SlowStream(content)
        with patch('log_transfer.time.monotonic', side_effect=lambda: clock[0]):
            job.run()
        self.assertGreater(clock[0], 60)
        self.assertEqual(self.results(job)[-1][0], 'loaded')
        updates = [value for kind, value in self.results(job) if kind == 'status' and 'KiB/s' in value]
        self.assertTrue(any('log_history.bak' in text for text in updates))
        self.assertTrue(any('log_history.txt' in text for text in updates))
        self.assertIn('2.1 KiB/s', updates[0])
        self.assertIn(' / ', updates[0])
        sftp.get_channel.return_value.settimeout.assert_called_once_with(DOWNLOAD_IDLE_TIMEOUT)

    def test_stalled_download_reports_idle_timeout_and_closes(self):
        job, client, sftp = self.make_job()
        stream = MagicMock()
        stream.__enter__.return_value = stream
        stream.read.side_effect = TimeoutError()
        sftp.open.side_effect = lambda *args: stream
        job.run()
        self.assertEqual(self.results(job)[-1],
                         ('error', 'Download stalled: no data received for 60 seconds. Try again.'))
        client.close.assert_called_once()

    def test_cancellation_during_read_discards_partial_download(self):
        job, client, sftp = self.make_job()
        stream = MagicMock()
        stream.__enter__.return_value = stream
        def read(size):
            job.cancel()
            return line().encode()
        stream.read.side_effect = read
        sftp.open.side_effect = lambda *args: stream
        job.run()
        self.assertEqual(self.results(job)[-1][0], 'cancelled')
        self.assertFalse(any(kind == 'loaded' for kind, _ in self.results(job)))
        self.assertTrue(client.close.called)

    def test_mock_download_parses_closes_and_cleans_temp(self):
        job, client, sftp = self.make_job(line().encode())
        job.run()
        loaded = [value for kind, value in self.results(job) if kind == 'loaded']
        self.assertEqual(len(loaded[0].events), 1)
        client.close.assert_called_once()
        self.assertEqual(sftp.open.call_args_list, [call('/root/log_history.bak', 'rb'), call('/root/log_history.txt', 'rb')])
        self.assertEqual(loaded[0].metadata['files'], ['log_history.bak', 'log_history.txt'])
        self.assertIsNone(job.connection)

    def test_pair_merge_rotation_overlap_and_reboots(self):
        with tempfile.TemporaryDirectory() as folder:
            current = Path(folder) / 'log_history.txt'
            backup = current.with_suffix('.bak')
            header = '******** Log start (2026-04-01 00:00:00) ********\n'
            backup.write_text(header + line() + line('C162A0:5', uptime='2s'))
            current.write_text(header + line('C153A0:5') + line('C162A0:6', uptime='2s')
                               + line('C157A1:6', uptime='3s')
                               + '******** Log start (2026-04-02 00:00:00) ********\n'
                               + line(day='2026-04-02'))
            data = load_log(current)
            self.assertEqual(len(data.events), 3)
            self.assertEqual(len(data.metadata['context']), 1)
            self.assertEqual(data.metadata['first_date'], '2026-04-01')
            self.assertEqual(data.metadata['last_date'], '2026-04-02')
            self.assertEqual(data.diagnostics['all']['cards'][0]['total'], 2)
            self.assertEqual(data.diagnostics['all']['cards'][0]['peak'], 1)
            self.assertEqual(load_log(backup).events, data.events)
            # Rotation can split one incident's variants without a new session header.
            current.write_text(line('C153A0:5') + line('C157A1:6', uptime='3s'))
            data = load_log(current)
            self.assertEqual(len(data.events), 2)
            self.assertEqual(len({e['session'] for e in data.events}), 1)

    def test_remote_pair_and_missing_backup(self):
        job, client, sftp = self.make_job()
        sftp.open.side_effect = lambda path, mode: io.BytesIO(
            line(day='2026-03-01', uptime='1s').encode() if path.endswith('.bak')
            else line(day='2026-04-01', uptime='2s').encode())
        job.run()
        data = self.results(job)[-1][1]
        self.assertEqual(len(data.events), 2)
        self.assertEqual(data.metadata['first_date'], '2026-03-01')
        self.assertEqual(data.metadata['last_date'], '2026-04-01')
        client.connect.assert_called_once()
        for number in (errno.ENOENT, errno.EACCES):
            job, client, sftp = self.make_job(line().encode())
            def stat(path):
                if path.endswith('.bak'):
                    raise OSError(number, 'unavailable')
                return MagicMock(st_size=100)
            sftp.stat.side_effect = stat
            job.run()
            kind, value = self.results(job)[-1]
            if number == errno.ENOENT:
                self.assertEqual(kind, 'loaded')
                self.assertIn('Backup not found', value.metadata['warning'])
                self.assertEqual(len(value.events), 1)
            else:
                self.assertEqual(kind, 'error')

    def test_missing_current_fails_instead_of_partial_history(self):
        job, _, sftp = self.make_job(line().encode())
        def stat(path):
            if path.endswith('.txt'):
                raise FileNotFoundError(errno.ENOENT, 'current log missing')
            return MagicMock(st_size=100)
        sftp.stat.side_effect = stat
        job.run()
        self.assertEqual(self.results(job)[-1][0], 'error')

    def test_size_limit_and_error_redaction(self):
        job, client, sftp = self.make_job()
        sftp.stat.return_value.st_size = MAX_LOG_BYTES + 1
        job.run()
        self.assertIn('64 MiB', self.results(job)[-1][1])
        sftp.open.assert_not_called()
        client.close.assert_called_once()
        job, client, _ = self.make_job()
        client.connect.side_effect = RuntimeError('failed with secret')
        job.run()
        self.assertEqual(self.results(job)[-1], ('error', 'failed with [redacted]'))

    def test_cancel_and_rejected_host(self):
        job, client, _ = self.make_job()
        job.cancel()
        job.run()
        client.connect.assert_not_called()
        self.assertEqual(self.results(job)[-1][0], 'cancelled')
        job, client, _ = self.make_job()
        def connect(*args, **kwargs):
            policy = client.set_missing_host_key_policy.call_args.args[0]
            key = MagicMock()
            key.asbytes.return_value = b'key'
            policy.missing_host_key(client, '127.0.0.1', key)
        client.connect.side_effect = connect
        job.start()
        while True:
            kind, question = job.events.get(timeout=2)
            if kind == 'host_key':
                question.answered.set()
                break
        job.thread.join(2)
        self.assertFalse(job.thread.is_alive())
        self.assertEqual(self.results(job)[-1][0], 'error')
        client.open_sftp.assert_not_called()


if __name__ == '__main__':
    unittest.main()
