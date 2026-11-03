"""Incident association and evidence claims use only synthetic robot data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from flight_data import FlightReport, TELEMETRY_MEMBER
from log_data import LogData
from log_parser import read_events
from robot_diagnostics import analyze_data, analyze_directory


def event(code="C157A0", time="2026-04-01 12:00:00", **extra):
    return dict(time=time, date=time[:10], code=code, family=code.split("A")[0],
                joint="Base", session=1, uptime="102s", source_path="log_history.txt", source_line=5, **extra)


def flight(name="flight.zip", metadata=None, columns=None, times=None, breaks=None):
    times = np.array(times if times is not None else [100, 101, 102, 103, 104], dtype=float)
    data = {"Actual velocity j0 [rad/s]": np.array([0, 1, 2, 0, 0], dtype=float),
            "Target velocity j0 [rad/s]": np.array([0, 1, 3, 0, 0], dtype=float),
            "Actual current j0 [A]": np.array([1, 1, 5, 1, 1], dtype=float),
            "Current lower j0 [A]": np.array([-3]*5, dtype=float),
            "Current upper j0 [A]": np.array([3]*5, dtype=float)} if columns is None else columns
    data = {k: np.array(v, dtype=float) for k,v in data.items()}
    return FlightReport(Path(name), name, {"incident_time": "2026-04-01 12:00:00", **(metadata or {})}, times, times-times[0], data, break_before=breaks)


def logline(code="C157A0:6", uptime="102s"):
    return f"3.5 :: {uptime} :: 2026-04-01 12:00:00 :: 110 :: {code} :: Controller evidence\n"


class RobotDiagnosticTests(unittest.TestCase):
    def test_wall_association_does_not_invent_marker(self):
        report = analyze_data([event()], [flight()])
        self.assertEqual(len(report.incidents), 1)
        incident = report.incidents[0]
        self.assertIsNone(incident.charts[0].marker_elapsed)
        self.assertEqual(incident.charts[0].scope, "Recording-level")
        self.assertIn("not a conversion", " ".join(incident.observations))
        self.assertIn("cannot be attributed", " ".join(incident.missing_evidence))
        self.assertIn("not proof", " ".join(incident.counterevidence))

    def test_explicit_marker_has_pre_post_measurements_and_real_envelope(self):
        report = analyze_data([event()], [flight(metadata={"incident_uptime": "102"})])
        incident = report.incidents[0]
        self.assertEqual(incident.charts[0].marker_elapsed, 2)
        text = " ".join(incident.observations)
        self.assertIn("pre/post peak", text)
        self.assertIn("1/4 comparable samples", text)
        self.assertIn("velocity tracking error| 1", text)
        self.assertIn("current exceeds the recorded envelope", " ".join(incident.hypotheses))

    def test_fault_transition_is_marker_but_first_sample_high_is_not(self):
        for values, expected in [([0,0,1,1,1],2), ([1,1,1,1,1],None), ([0,1,0,1,0],None)]:
            with self.subTest(values=values):
                report = analyze_data([], [flight(columns={"Fault active": values})])
                self.assertEqual(report.incidents[0].charts[0].marker_elapsed, expected)

    def test_gap_does_not_create_false_acceleration_or_transition(self):
        f = flight(times=[100, 101, 120, 121, 122], columns={"Actual velocity j0 [rad/s]": [0,0,10,10,10], "Fault active": [0,0,1,1,1]})
        incident = analyze_data([], [f]).incidents[0]
        self.assertIsNone(incident.charts[0].marker_elapsed)
        self.assertIn("peak |derived acceleration| 0", " ".join(incident.observations))
        self.assertIn("data gaps", " ".join(incident.missing_evidence))

    def test_timezone_and_identity_conflicts_do_not_match(self):
        for metadata in ({"serial": "other"}, {"incident_time": "2026-04-01T12:00:00Z"}, {"incident_time": "2026-04-02 12:00:00"}):
            with self.subTest(metadata=metadata):
                report = analyze_data([event(robot_id="robot-one")], [flight(metadata=metadata)])
                self.assertEqual(len(report.incidents), 2)
                self.assertFalse(report.incidents[0].flight_names)

    def test_real_ur_summary_timezone_and_operator_offset(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"log_history.txt").write_text(logline())
            with zipfile.ZipFile(root/"flight.zip", "w") as archive:
                archive.writestr(TELEMETRY_MEMBER, "Timestamp [s],Actual velocity j0 [rad/s]\n100,0\n101,1\n")
                archive.writestr("summary.log", "Incident Time: Wed Apr 01 12:00:00 CEST 2026\n")
            default = analyze_directory(root)
            self.assertEqual(len(default.incidents), 2)
            matched = analyze_directory(root, log_utc_offset="+02:00")
            self.assertEqual(len(matched.incidents), 1)
            self.assertIn("Operator-supplied", " ".join(matched.coverage))
            self.assertEqual(len(matched.incidents[0].charts[0].source_sha256), 64)
            wrong = analyze_directory(root, log_utc_offset="+00:00")
            self.assertEqual(len(wrong.incidents), 2)

    def test_aware_log_preserved_and_unknown_zone_not_guessed(self):
        e = event(time="2026-04-01T10:00:00+00:00")
        known = flight(metadata={"incident_time": "Wed Apr 01 12:00:00 CEST 2026"})
        self.assertEqual(len(analyze_data([e], [known], log_utc_offset="-05:00").incidents), 1)
        unknown = flight(metadata={"incident_time": "Wed Apr 01 12:00:00 CST 2026"})
        self.assertEqual(len(analyze_data([e], [unknown]).incidents), 2)
        with self.assertRaises(ValueError):
            analyze_data([], [], log_utc_offset="+22:85")

    def test_mixed_aware_offsets_sort_by_instant_before_grouping(self):
        early = event("C157A0", time="2026-04-01T12:00:00+02:00")
        later = event("C153A0", time="2026-04-01T10:00:01+00:00")
        naive = event("C174A0", time="2026-04-01T10:00:00")
        report = analyze_data([later, naive, early], [])
        self.assertEqual(len(report.incidents), 2)
        self.assertEqual(report.incidents[0].primary_code, "C157A0")
        self.assertEqual(report.incidents[0].supporting_codes, ["C153A0"])
        self.assertEqual(report.incidents[1].primary_code, "C174A0")

    def test_log_uptime_anchor_requires_serial_clock_and_single_event_time(self):
        e = event(time="2026-04-01T10:00:00+00:00", robot_id="123", serial="123")
        e["uptime"] = "0d0h1m42.0s"
        f = flight(metadata={"serial": "123", "incident_time": "Wed Apr 01 12:00:00 CEST 2026"})
        incident = analyze_data([e], [f]).incidents[0]
        self.assertEqual(incident.charts[0].marker_elapsed, 2)
        self.assertIn("Explicit fault uptime from the log", incident.window_reason)
        for replacement in ("0d0h0m5.0s", "102", "102ms"):
            with self.subTest(uptime=replacement):
                changed = {**e, "uptime": replacement}
                incident = analyze_data([changed], [f]).incidents[0]
                self.assertIsNone(incident.charts[0].marker_elapsed)
        # Different boot sessions with compatible wall time remain ambiguous.
        reboot = {**e, "session": 2}
        self.assertEqual(len(analyze_data([e, reboot], [f]).incidents), 3)
        # A burst with separate fault uptimes is not forced to one sample.
        second = {**e, "code": "C153A0", "uptime": "0d0h1m43.0s"}
        incident = analyze_data([e, second], [f]).incidents[0]
        self.assertIsNone(incident.charts[0].marker_elapsed)
        other_robot = {**e, "serial": "456", "robot_id": "456"}
        self.assertEqual(len(analyze_data([other_robot], [f]).incidents), 2)

    def test_multiple_flights_or_log_sessions_are_ambiguous(self):
        report = analyze_data([event()], [flight(), flight("second.zip")])
        self.assertEqual(len(report.incidents), 3)
        self.assertFalse(report.incidents[0].flight_names)
        second = event()
        second["session"] = 2
        report = analyze_data([event(), second], [flight()])
        self.assertEqual(len(report.incidents), 3)
        self.assertFalse(report.incidents[0].flight_names)

    def test_duplicate_events_are_one_incident_with_combined_provenance(self):
        duplicate = event()
        duplicate["source_path"] = "log_history.bak"
        secondary = event("C153A0", time="2026-04-01 12:00:01")
        report = analyze_data([event(), duplicate, secondary], [])
        self.assertEqual(len(report.incidents), 1)
        incident = report.incidents[0]
        self.assertEqual(incident.primary_code, "C157A0")
        self.assertEqual(incident.supporting_codes, ["C153A0"])
        self.assertEqual(len(incident.provenance), 2)
        self.assertIn("causal order is not established", " ".join(incident.observations))

    def test_narrative_changes_for_torque_vs_payload_not_only_counts(self):
        torque = analyze_data([event("C174A0")], []).incidents[0]
        payload = analyze_data([event("C158A0")], []).incidents[0]
        self.assertNotEqual(torque.title, payload.title)
        self.assertIn("blend", " ".join(torque.actions))
        self.assertIn("zero", " ".join(payload.counterevidence + payload.hypotheses + payload.actions).lower())
        self.assertTrue(torque.hypotheses and torque.counterevidence)

    def test_same_code_with_different_motion_changes_evidence_and_priorities(self):
        times = np.arange(100, 102.01, .25)
        common = {"Target velocity j0 [rad/s]": [1]*9,
                  "Current lower j0 [A]": [-3]*9, "Current upper j0 [A]": [3]*9}
        normal = flight(metadata={"incident_uptime": "101.5"}, times=times,
                        columns={**common, "Actual velocity j0 [rad/s]": [1]*9,
                                 "Actual current j0 [A]": [1]*9})
        abnormal = flight(metadata={"incident_uptime": "101.5"}, times=times,
                          columns={**common, "Actual velocity j0 [rad/s]": [1]*6+[0]*3,
                                   "Actual current j0 [A]": [1]*6+[5]*3})
        quiet = analyze_data([event()], [normal]).incidents[0]
        stop = analyze_data([event()], [abnormal]).incidents[0]
        self.assertIn("stays within", " ".join(quiet.observations))
        self.assertIn("coincide", " ".join(stop.observations))
        self.assertIn("consistent with stopping", " ".join(stop.observations))
        self.assertNotEqual(quiet.actions[0], stop.actions[0])
        self.assertIn("coincide", stop.actions[0])

    def test_inverted_envelopes_and_gap_marker_do_not_claim_exceedance(self):
        f = flight(metadata={"incident_uptime": "110"}, times=[100,101,120,121,122],
                   columns={"Actual current j0 [A]": [10]*5,
                            "Current lower j0 [A]": [3]*5, "Current upper j0 [A]": [-3]*5})
        incident = analyze_data([], [f]).incidents[0]
        self.assertIsNone(incident.charts[0].marker_elapsed)
        self.assertIn("inverted", " ".join(incident.missing_evidence))
        self.assertNotIn("exceeds", " ".join(incident.hypotheses))

    def test_multiple_joints_in_one_burst_do_not_create_recurrence(self):
        report = analyze_data([event(), event("C157A1")], [])
        self.assertEqual(report.patterns, [])

    def test_distinct_same_time_messages_are_not_lost(self):
        events = [event(message="line 10"), event(message="line 11")]
        report = analyze_data(events, [])
        self.assertIn("line 10", " ".join(report.incidents[0].observations))
        self.assertIn("line 11", " ".join(report.incidents[0].observations))

    def test_no_channels_and_unrelated_reporting_time_remain_limited(self):
        f = flight(metadata={"incident_time": "", "reporting_time": "2026-04-01 12:00:00"}, columns={})
        report = analyze_data([event()], [f])
        self.assertEqual(len(report.incidents), 2)
        self.assertIn("No usable joint", " ".join(report.incidents[1].missing_evidence))

    def test_unknown_codes_preserved_without_guessing_joint(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"log_history.txt"
            path.write_text(logline("C204A1:6") + logline("C0A0:0") + logline("C162A0:6"))
            self.assertEqual(read_events(path)[0], [])
            events, _ = read_events(path, include_unmapped=True)
            self.assertEqual([e["code"] for e in events], ["C204A1", "C162A0"])
            self.assertEqual(events[0]["joint"], "Unknown")
            self.assertEqual(events[0]["source_line"], 1)
            report = analyze_directory(folder)
            self.assertIn("C204A1", report.incidents[0].codes)

    def test_workshop_controller_signals_reach_reports_without_joint_faults(self):
        from robot_status import build_robot_status
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'log_history.txt'
            path.write_text(logline('C171A1:6') + logline('C150A0:6') + logline('C152A0:6'))
            report = analyze_directory(folder)
        self.assertEqual(len(report.incidents), 1)
        incident = report.incidents[0]
        self.assertEqual(incident.joints, ['Controller'])
        self.assertEqual(incident.joint_codes, {})
        self.assertIn('larger radius', ' '.join(incident.actions))
        self.assertNotIn('smaller radius', ' '.join(incident.actions))
        self.assertIn('Preserve the risk-assessed limits', ' '.join(incident.actions))
        status = build_robot_status(report)
        self.assertEqual(status.overall_status, 'Controller review required')
        self.assertTrue(all(joint.status != 'Needs attention' for joint in status.joints))
        self.assertTrue(any('limit' in area.area.lower() for area in status.program_areas))

    def test_known_informational_messages_are_context_not_faults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"log_history.txt"
            path.write_text(logline("C100A0:6") + logline("C101A0:6") + logline("C102A0:6"))
            metadata = {}
            self.assertFalse(read_events(path, metadata, include_unmapped=True)[0])
            self.assertEqual(len(metadata["context"]), 3)

    def test_directory_dedup_partial_errors_manifest_and_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/"log_history.txt").write_text(logline())
            (root/"log_history.bak").write_text(logline())
            (root/"broken.zip").write_bytes(b"not a zip")
            (root/"diagnostic_manifest.json").write_text(json.dumps({"status": "partial", "serial": "123", "warnings": ["Rotated log not available"]}))
            with zipfile.ZipFile(root/"flight.zip", "w") as archive:
                archive.writestr(TELEMETRY_MEMBER, "Timestamp [s],Actual velocity j0 [rad/s]\n100,0\n101,1\n")
                archive.writestr("summary.log", "Incident Time: 2026-04-01 12:00:00\nSerial number: 123\n")
            report = analyze_directory(root)
            self.assertEqual(len(report.incidents), 1)
            self.assertIn("Duplicate file", " ".join(report.coverage))
            self.assertIn("broken.zip", " ".join(report.warnings))
            self.assertIn("incomplete", " ".join(report.warnings))
            self.assertNotIn("Robot identity is missing", " ".join(report.incidents[0].missing_evidence))
            with patch("robot_diagnostics.MAX_ANALYSIS_BYTES", 1):
                report = analyze_directory(root)
                self.assertIn("memory budget", " ".join(report.warnings))
                self.assertFalse(report.incidents[0].flight_names)

    def test_cancellation_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/"log_history.txt").write_text(logline())
            def cancel():
                raise InterruptedError("cancelled")
            with self.assertRaises(InterruptedError):
                analyze_directory(folder, check_cancel=cancel)

    def test_empty_directory_is_not_called_healthy(self):
        with tempfile.TemporaryDirectory() as folder:
            report = analyze_directory(folder)
            self.assertFalse(report.incidents)
            self.assertIn("does not demonstrate", " ".join(report.summary))


if __name__ == "__main__":
    unittest.main()
