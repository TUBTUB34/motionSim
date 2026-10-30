"""SIMULATED listing benchmark; never opens a robot connection.

The old application had no flight-listing workflow. This compares a conventional
listdir + stat-per-file baseline with the actual new FlightTransfer._list code.
Both use one already-authenticated session and identical names/metadata. RTT is
injected per operation; SSH setup, server processing and local hash checks for
previous downloads are excluded. These are not real-device measurements.
"""
import argparse
import json
from pathlib import Path
import stat
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flight_transfer import FlightTransfer
from log_transfer import LogConnection


class SimulatedDirectory:
    def __init__(self, entries, delay):
        self.entries = entries
        self.delay = delay
        self.requests = 0

    def wait(self):
        self.requests += 1
        time.sleep(self.delay)

    def listdir(self, directory):
        self.wait()
        return [entry.filename for entry in self.entries]

    def stat(self, path):
        self.wait()
        return next(entry for entry in self.entries if path.endswith('/' + entry.filename))

    def listdir_attr(self, directory):
        self.wait()
        return self.entries


def benchmark(rtt_ms=4, repeats=5):
    fixtures = sorted((Path(__file__).resolve().parents[1] / 'assets' / 'flightreports').glob('recording*.zip'))
    if not fixtures:
        raise RuntimeError('Supplied recording fixtures are required for this benchmark.')
    entries = [SimpleNamespace(filename=p.name, st_size=p.stat().st_size,
                               st_mtime=int(p.stat().st_mtime), st_mode=stat.S_IFREG | 0o444)
               for p in fixtures]
    results = {}
    with tempfile.TemporaryDirectory() as library:
        for name in ('baseline_names_then_stat', 'new_batched_attributes'):
            timings = []
            for _ in range(repeats):
                remote = SimulatedDirectory(entries, rtt_ms / 1000)
                job = FlightTransfer(LogConnection('127.0.0.1', 'fixture', 'fixture'), library=library)
                job.sftp = remote
                job.robot_id = 'robot-fixture'
                job.fingerprint = 'fixture'
                job._ensure_connection = lambda: None
                start = time.perf_counter()
                if name.startswith('baseline'):
                    listed = [remote.stat(job.directory + '/' + filename)
                              for filename in remote.listdir(job.directory)]
                else:
                    job._list()
                    listed = job.entries
                timings.append(time.perf_counter() - start)
                assert len(listed) == len(entries)
            results[name] = {'median_seconds': round(statistics.median(timings), 6),
                             'metadata_requests': remote.requests, 'connections_reused': 1}
    return {'measurement': 'SIMULATED metadata RTT; no robot connection or file transfer',
            'baseline': 'Conventional alternative; previous application had no flight listing',
            'files': len(entries), 'total_file_bytes': sum(entry.st_size for entry in entries),
            'payload_bytes_transferred': 0, 'rtt_ms': rtt_ms, 'repetitions': repeats, **results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtt-ms', type=float, default=4)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.rtt_ms < 0 or args.repeats < 1:
        parser.error('Use nonnegative RTT and positive repetitions.')
    print(json.dumps(benchmark(args.rtt_ms, args.repeats), indent=2))
