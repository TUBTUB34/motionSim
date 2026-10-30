"""Reproducible SIMULATED RTT comparison; never connects to a robot.

Run: .venv/bin/python scripts/benchmark_transfer.py --rtt-ms 4 --files 4 --mib 2

Models installed Paramiko's 32 KiB protocol cap. The previous 64 KiB log read loop
waits per protocol request; readv overlaps one bounded window's requests. Both
paths use identical data, in-memory output, RTT and file counts. This isolates
latency overhead: SSH encryption, network bandwidth, disk speed, robot CPU and
archive creation are deliberately excluded. These are not robot measurements.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transfer_stream import READ_SIZE, PIPELINE_REQUESTS, copy_remote


class SimulatedSFTP(io.BytesIO):
    def __init__(self, content, rtt):
        super().__init__(content)
        self.rtt = rtt
        self.round_trips = 0

    def wait(self):
        self.round_trips += 1
        time.sleep(self.rtt)

    def read(self, length):
        data = bytearray()
        while len(data) < length:
            self.wait()
            part = super().read(min(READ_SIZE, length - len(data)))
            data.extend(part)
            if not part:
                break
        return bytes(data)

    def readv(self, chunks, max_concurrent_prefetch_requests):
        assert len(chunks) <= max_concurrent_prefetch_requests
        self.wait()
        for offset, length in chunks:
            self.seek(offset)
            yield super().read(length)


def previous_copy(remote, local, size):
    while chunk := remote.read(65536):
        local.write(chunk)


def benchmark(files=4, mib=2, rtt_ms=4):
    content = (bytes(range(256)) * ((mib * 1048576 + 255) // 256))[:mib * 1048576]
    expected_hash = hashlib.sha256(content).hexdigest()
    results = {}
    for name, copier in [('before_serial', previous_copy),
                         ('previous_flight_pipeline_16', lambda r, l, s: copy_remote(r, l, s, pipeline_requests=16)),
                         ('after_bounded_pipeline', copy_remote)]:
        start, round_trips = time.perf_counter(), 0
        for _ in range(files):
            with SimulatedSFTP(content, rtt_ms / 1000) as remote, io.BytesIO() as local:
                copier(remote, local, len(content))
                assert len(local.getvalue()) == len(content)
                assert hashlib.sha256(local.getvalue()).hexdigest() == expected_hash
                round_trips += remote.round_trips
        elapsed = time.perf_counter() - start
        results[name] = {'seconds': round(elapsed, 4), 'mib_per_second': round(files * mib / elapsed, 2),
                         'simulated_round_trips': round_trips}
    results['speedup'] = round(results['before_serial']['seconds'] / results['after_bounded_pipeline']['seconds'], 2)
    return {'measurement': 'SIMULATED SFTP RTT only, not real robot throughput',
            'files': files, 'mib_per_file': mib, 'rtt_ms': rtt_ms, 'sha256': expected_hash,
            'pipeline_requests': PIPELINE_REQUESTS, 'max_inflight_bytes': READ_SIZE * PIPELINE_REQUESTS,
            'correctness': f'{files} files per variant; {files * len(content)} bytes; SHA-256 identical; zero missing files',
            **results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rtt-ms', type=float, default=4)
    parser.add_argument('--files', type=int, default=4)
    parser.add_argument('--mib', type=int, default=2)
    args = parser.parse_args()
    if args.rtt_ms < 0 or args.files < 1 or args.mib < 1:
        parser.error('Use nonnegative RTT and positive file count and MiB.')
    print(json.dumps(benchmark(args.files, args.mib, args.rtt_ms), indent=2))
