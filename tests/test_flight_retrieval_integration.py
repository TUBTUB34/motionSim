"""Offline retrieval-to-playback/PDF regression using a supplied robot recording."""
import hashlib
from pathlib import Path
import re
import tempfile
import unittest

import numpy as np

from flight_data import load_flight
from flight_motion import PlaybackTimeline, sample_at
from flight_pdf import export_flight_pdf
from flight_transfer import FlightTransfer, LogConnection, discover_library
from tests.test_flight_transfer import Remote


class FlightRetrievalIntegrationTests(unittest.TestCase):
    def test_real_recording_survives_simulated_retrieval_playback_and_pdf(self):
        references = Path(__file__).resolve().parents[1] / 'assets' / 'flightreports'
        recordings = sorted(references.glob('recording*.zip'))
        if not recordings:
            self.skipTest('Supplied recording assets are not bundled in this checkout')
        source = recordings[0]
        original = source.read_bytes()
        remote = Remote()
        remote.files = {source.name: original}
        # This transport is an in-memory SFTP double. No robot/network is used.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'library'
            worker = FlightTransfer(LogConnection('192.0.2.1', 'root', 'simulated-password'),
                    library=library, client_factory=remote.factory,
                    trust_path=root / 'known_hosts')
            worker.start()
            try:
                def finish():
                    result = []
                    while True:
                        event = worker.events.get(timeout=30)
                        result.append(event)
                        if event[0] == 'finished':
                            self.assertFalse(event[1]['cancelled'])
                            self.assertEqual(event[1]['failed'], 0, result)
                            self.assertFalse(any(kind == 'error' for kind, _ in result), result)
                            return result

                self.assertTrue(worker.refresh())
                listing_events = finish()
                entries = next(value for kind, value in listing_events if kind == 'listed')
                self.assertEqual([entry.name for entry in entries], [source.name])
                self.assertFalse(entries[0].downloaded)
                self.assertTrue(worker.download(entries))
                transfer_events = finish()
                self.assertEqual(transfer_events[-1][1]['succeeded'], 1)
                downloaded = next(value for kind, value in transfer_events if kind == 'downloaded')
                paths = discover_library(library)
                self.assertEqual(paths, [downloaded['path']])
                self.assertEqual(hashlib.sha256(paths[0].read_bytes()).digest(),
                                 hashlib.sha256(original).digest())
                self.assertEqual(list(library.rglob('*.part*')), [])
                self.assertEqual(len(remote.clients), 1, 'List and download should reuse SSH')

                report = load_flight(paths[0])
                self.assertEqual(report.sample_count, 15000)
                self.assertAlmostEqual(report.duration, 29.998, places=4)
                self.assertEqual(report.model, 'UR10e')
                self.assertTrue(report.metadata.get('serial'))
                index = report.sample_count // 2
                elapsed = float(report.time[index])
                sample = sample_at(report, elapsed)
                self.assertIsNotNone(sample)
                np.testing.assert_allclose(sample.joints,
                        [report.series('position', joint)[index] for joint in range(6)])
                expected_tcp = [report.columns[
                    f"Actual TCP pose {axis} [{'m' if len(axis) == 1 else 'rad'}]"][index]
                    for axis in ('x', 'y', 'z', 'rx', 'ry', 'rz')]
                np.testing.assert_allclose(sample.tcp_pose, expected_tcp)
                self.assertEqual(sample.tcp_source, 'Recorded TCP')

                timeline = PlaybackTimeline(report)
                timeline.seek(elapsed)
                timeline.speed = 2
                timeline.play()
                self.assertAlmostEqual(timeline.tick(.001), elapsed + .002)
                self.assertIsNotNone(sample_at(report, timeline.elapsed))
                timeline.pause()
                paused = timeline.elapsed
                self.assertEqual(timeline.tick(.5), paused)
                timeline.seek(report.duration - .001)
                timeline.play()
                self.assertEqual(timeline.tick(.5), report.duration)
                self.assertFalse(timeline.playing)
                timeline.restart()
                self.assertEqual(timeline.elapsed, 0)

                target = root / 'retrieved-flight-report.pdf'
                self.assertEqual(export_flight_pdf(report, target), target)
                content = target.read_bytes()
                self.assertTrue(content.startswith(b'%PDF-'))
                self.assertTrue(content.rstrip().endswith(b'%%EOF'))
                self.assertEqual(len(re.findall(rb'/Type\s*/Page\b', content)), 10)
                self.assertEqual(list(root.glob('.flight-report-*')), [])

                # PyMuPDF is available in the developer environment; keep this
                # extra structural/render check optional for normal installs.
                try:
                    import pymupdf as fitz
                except ImportError:
                    fitz = None
                if fitz is not None:
                    with fitz.open(target) as document:
                        self.assertEqual(document.page_count, 10)
                        for page in document:
                            self.assertTrue(page.get_text().strip())
                            pixmap = page.get_pixmap(matrix=fitz.Matrix(.5, .5), alpha=False)
                            self.assertGreater(pixmap.width, 100)
                            self.assertGreater(pixmap.height, 100)
                            self.assertLess(min(pixmap.samples), 240)

                self.assertTrue(worker.download())
                skipped_events = finish()
                self.assertEqual(skipped_events[-1][1]['succeeded'], 0)
                self.assertEqual(discover_library(library), paths)
            finally:
                worker.close()
                worker.thread.join(timeout=5)
            self.assertFalse(worker.thread.is_alive())


if __name__ == '__main__':
    unittest.main()
