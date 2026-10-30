import csv
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from flight_data import TELEMETRY_MEMBER, discover_flights, load_flight


class FlightDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def recording(self, headers, rows, *, name="flight.zip", context=None):
        text = io.StringIO()
        writer = csv.writer(text)
        writer.writerow(headers)
        writer.writerows(rows)
        path = self.root / name
        if path.suffix == ".csv":
            # csv.writer already supplies CRLF; prevent Windows translating
            # those newlines again and inserting spurious empty records.
            path.write_text(text.getvalue(), encoding="utf-8", newline="")
        else:
            with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(TELEMETRY_MEMBER, text.getvalue())
                for key, value in (context or {}).items():
                    archive.writestr(key, value)
        return path

    def test_source_units_timestamps_context_and_dh_model(self):
        path = self.recording(
            ["Timestamp [s]", "Actual position j1 [rad]", "Target position j1 [rad]"],
            [[10, -1.2, -1.3], [10.002, -1.1, -1.2]],
            context={"summary.log": "Incident Time: Mon Oct 12 15:10:47 CEST 2026\nSerial number: 123\nCurrent Program Name: Dough\nPayload [kg]: 4.7\n",
                     "metadata.json": '{"reportId":"report-1","reportType":"ERROR"}',
                     "files/polyscope.in.memory.program.script": "movej([0,0,0,0,0,0])",
                     "files/urcontrol/urcontrol.conf": "robot_type = 2 # UR10\na = [0,-.6127,-.57155,0,0,0]\nd = [.1807,0,0,.17415,.11985,.11655]\n"})
        report = load_flight(path)
        np.testing.assert_allclose(report.timestamps, [10, 10.002])
        np.testing.assert_allclose(report.time, [0, .002])
        np.testing.assert_allclose(report.series("position", 1), [-1.2, -1.1])
        np.testing.assert_allclose(report.series("position", 1, True), [-1.3, -1.2])
        self.assertEqual(report.model, "UR10e")
        self.assertEqual(report.metadata["serial"], "123")
        self.assertEqual(report.metadata["program"], "Dough")
        self.assertEqual(report.metadata["report_id"], "report-1")
        self.assertEqual(report.script, "movej([0,0,0,0,0,0])")
        self.assertAlmostEqual(report.duration, .002)
        self.assertFalse(report.timestamps.flags.writeable)
        self.assertIs(load_flight(path), report)

    def test_missing_and_malformed_measurements_preserve_gaps(self):
        path = self.recording(["Timestamp [s]", "Actual current j0 [A]"],
                              [[1, 2], [2, "bad"], [3], [4, "inf"], [5, 7]])
        report = load_flight(path)
        np.testing.assert_equal(report.series("current", 0), [2, np.nan, np.nan, np.nan, 7])
        self.assertEqual(report.sample_count, 5)
        self.assertTrue(any("malformed records" in w for w in report.warnings))
        self.assertTrue(np.isnan(report.series("position", 0)).all())
        self.assertTrue(np.isnan(report.series("current_upper", 0)).all())

    def test_acceleration_does_not_bridge_missing_values_or_timestamp_gaps(self):
        path = self.recording(["Timestamp [s]", "Actual velocity j0 [rad/s]"],
                              [[0, 0], [1, 2], [2, ""], [3, 90], [4, 94],
                               [20, 1000], [21, 1006], [22, 1012]])
        report = load_flight(path)
        np.testing.assert_equal(report.series("acceleration", 0), [2, 2, np.nan, 4, 4, 6, 6, 6])
        self.assertEqual([(s.start, s.stop) for s in report.valid_segments(report.series("velocity", 0))],
                         [(0, 2), (3, 5), (5, 8)])
        self.assertTrue(np.isnan(report.series("acceleration", 0, target=True)).all())

    def test_invalid_duplicate_and_backward_timestamps_are_reported(self):
        path = self.recording(["Timestamp [s]", "Actual current j0 [A]"],
                              [[0, 1], ["nan", 8], [1, 2], [1, 10], [.5, 12], [2, 3]])
        report = load_flight(path)
        np.testing.assert_equal(report.timestamps, [0, 1, 2])
        np.testing.assert_equal(report.break_before, [False, True, True])
        self.assertEqual([(s.start, s.stop) for s in report.valid_segments(report.series("current", 0))],
                         [(0, 1), (1, 2), (2, 3)])
        self.assertTrue(any("3 invalid or non-increasing" in w for w in report.warnings))

    def test_csv_irregular_rates_use_actual_intervals(self):
        path = self.recording(["Timestamp [s]", "Actual velocity j2 [rad/s]"],
                              [[100, 0], [100.1, .4], [100.25, 1]], name="telemetry.csv")
        report = load_flight(path)
        np.testing.assert_allclose(report.series("acceleration", 2), [4, 4, 4])
        self.assertIsNone(report.model)

    def test_single_sample_acceleration_unavailable(self):
        report = load_flight(self.recording(["Timestamp [s]", "Actual velocity j0 [rad/s]"], [[2, 8]]))
        self.assertEqual(report.duration, 0)
        self.assertTrue(np.isnan(report.series("acceleration", 0)).all())

    def test_recorded_acceleration_and_limits_are_not_mislabeled_as_derived_or_absent(self):
        report = load_flight(self.recording(
            ["Timestamp [s]", "Actual acceleration j0 [rad/s^2]", "Actual velocity j0 [rad/s]",
             "Current upper j0 [A]", "Current lower j0 [A]"], [[0, 3, 100, 8, -8], [1, 4, 200, 9, -9]]))
        np.testing.assert_equal(report.series("acceleration", 0), [3, 4])
        np.testing.assert_equal(report.series("current_upper", 0), [8, 9])
        np.testing.assert_equal(report.series("current_lower", 0), [-8, -9])
        self.assertIn("supplied directly", report.metadata["acceleration_note"])
        self.assertFalse(any("derived" in warning.lower() or "envelopes are not recorded" in warning
                             for warning in report.warnings))

    def test_reserved_duplicate_columns_are_ignored_but_named_duplicates_rejected(self):
        report = load_flight(self.recording(["Timestamp [s]", "reserved", "reserved"], [[0, 1, 2]]))
        self.assertNotIn("reserved", report.columns)
        path = self.recording(["Timestamp [s]", "Actual current j0 [A]", "Actual current j0 [A]"],
                              [[0, 1, 2]], name="duplicate.zip")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_flight(path)

    def test_discovery_excludes_configuration_zip_and_statistics(self):
        flight = self.recording(["Timestamp [s]"], [[0]])
        with zipfile.ZipFile(self.root / "config.zip", "w") as archive:
            archive.writestr("summary.log", "configuration")
        (self.root / "psm.ura").write_text("stats")
        (self.root / "bad.zip").write_text("not zip")
        self.assertEqual(discover_flights(self.root), [flight])
        with self.assertRaisesRegex(ValueError, "no files/realtimedata"):
            load_flight(self.root / "config.zip")
        with self.assertRaisesRegex(ValueError, "program statistics"):
            load_flight(self.root / "psm.ura")

    def test_limits_and_empty_records_fail_with_actionable_errors(self):
        path = self.recording(["Timestamp [s]"], [[0], [1]])
        with patch("flight_data.MAX_SAMPLES", 1), self.assertRaisesRegex(ValueError, "sample limit"):
            load_flight(path)
        empty = self.recording(["Timestamp [s]"], [], name="empty.zip")
        with self.assertRaisesRegex(ValueError, "no valid"):
            load_flight(empty)
        wrong = self.recording(["time"], [[0]], name="wrong.zip")
        with self.assertRaisesRegex(ValueError, "Timestamp"):
            load_flight(wrong)


class SuppliedFlightTests(unittest.TestCase):
    def test_all_supplied_flights_match_source_and_dimensions(self):
        directory = Path(__file__).resolve().parents[1] / "assets" / "flightreports"
        paths = discover_flights(directory)
        if not paths:
            self.skipTest("Local flight archives are not distributed with this checkout")
        for path in paths:
            with self.subTest(flight=path.name):
                report = load_flight(path)
                self.assertEqual(report.sample_count, 15_000)
                self.assertAlmostEqual(report.duration, 29.998, places=6)
                self.assertEqual(report.model, "UR10e")
                self.assertEqual(report.metadata["program"], "App_DoughFactory")
                with zipfile.ZipFile(path) as archive:
                    with io.TextIOWrapper(archive.open(TELEMETRY_MEMBER)) as stream:
                        rows = csv.DictReader(stream)
                        for index, row in enumerate(rows):
                            if index in (0, 7499, 14999):
                                for joint in range(6):
                                    self.assertEqual(report.series("position", joint)[index], float(row[f"Actual position j{joint} [rad]"]))
                                    self.assertEqual(report.series("current", joint)[index], float(row[f"Actual current j{joint} [A]"]))


if __name__ == "__main__":
    unittest.main()
