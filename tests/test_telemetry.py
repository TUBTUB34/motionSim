"""Synthetic RTDE frames exercise negotiation and decoding without a robot."""
import struct
import unittest
from dataclasses import replace

from rtde_client import RTDEClient
from telemetry import (BASE_FIELDS, STATUS_FIELDS, TYPE_FORMATS, format_value,
                       script_line_text, SAFETY_MODES, enum_text)
from tests.test_core import FragmentedSocket, packet

FIELDS = BASE_FIELDS + STATUS_FIELDS


def recipe(fields, missing=()):
    kinds = ["NOT_FOUND" if f.name in missing else f.kind for f in fields]
    return packet(79, (b"\0" if missing else b"\7") + ",".join(kinds).encode())


def data_packet(fields, overrides=None):
    overrides = overrides or {}
    data = bytearray(b"\7")
    for field in fields:
        count = int(field.kind[6]) if field.kind.startswith("VECTOR") else 1
        value = overrides.get(field.name, (0,) * count if count > 1 else 0)
        data.extend(struct.pack("!" + TYPE_FORMATS[field.kind], *(value if count > 1 else (value,))))
    return packet(85, data)


class TelemetryTests(unittest.TestCase):
    def client(self, setup, data=b""):
        sock = FragmentedSocket(packet(86, b"\1") + setup + packet(83, b"\1") + data)
        return RTDEClient("mock", socket_factory=lambda *a, **k: sock), sock

    def test_mixed_types_high_bits_and_immutable_snapshot(self):
        values = {"robot_mode": -1, "actual_q": (1., 2., 3., 4., 5., 6.),
                  "joint_mode": (253, 253, 253, 253, 253, 253), "script_control_line": 419,
                  "actual_digital_input_bits": (1 << 63) | (1 << 17), "payload_cog": (.1, .2, .3)}
        client, sock = self.client(recipe(FIELDS), data_packet(FIELDS, values))
        client.connect()
        sample = client.receive()
        for key, value in values.items():
            self.assertEqual(sample.telemetry[key], value)
        self.assertEqual(sample.joints, values["actual_q"])
        self.assertEqual([request[2] for request in sock.sent], [86, 79, 83])
        with self.assertRaises(TypeError):
            sample.telemetry["payload"] = 4

    def test_missing_optional_fields_retries_without_losing_supported_outputs(self):
        missing = ("script_control_line", "payload_cog", "safety_status")
        supported = tuple(f for f in FIELDS if f.name not in missing)
        client, sock = self.client(recipe(FIELDS, missing) + recipe(supported), data_packet(supported))
        client.connect()
        sample = client.receive()
        self.assertNotIn("script_control_line", sample.telemetry)
        self.assertIn("safety_mode", sample.telemetry)
        self.assertEqual([request[2] for request in sock.sent], [86, 79, 79, 83])
        self.assertNotIn(b"script_control_line", sock.sent[2])

    def test_all_optional_fields_missing_still_streams_pose(self):
        client, _ = self.client(recipe(FIELDS, [f.name for f in STATUS_FIELDS]) + recipe(BASE_FIELDS),
                                data_packet(BASE_FIELDS))
        client.connect()
        self.assertEqual(client.receive().joints, (0,) * 6)

    def test_missing_required_or_wrong_type_closes_connection(self):
        wrong = tuple(replace(f, kind="UINT32") if f.name == "payload" else f for f in FIELDS)
        for response in (recipe(FIELDS, ("actual_q",)), recipe(wrong), packet(79, b"\7DOUBLE")):
            client, sock = self.client(response)
            with self.assertRaises(ConnectionError):
                client.connect()
            self.assertTrue(sock.closed)

    def test_nonfinite_telemetry_and_bad_frames_rejected(self):
        for data in (data_packet(FIELDS, {"payload": float("nan")}), packet(85, b"\7short"),
                     data_packet(FIELDS).replace(b"\x55\x07", b"\x55\x08", 1)):
            client, _ = self.client(recipe(FIELDS), data)
            client.connect()
            with self.assertRaises(ConnectionError):
                client.receive()

    def test_line_zero_missing_and_active_are_distinct(self):
        self.assertIn("unavailable", script_line_text({}))
        self.assertIn("No thread", script_line_text({"script_control_line": 0}))
        self.assertEqual(script_line_text({"script_control_line": 81}), "URScript line 81")

    def test_flags_analog_units_and_unknown_states(self):
        fields = {f.name: f for f in FIELDS}
        self.assertIn("Protective stopped", format_value(fields["safety_status_bits"], 4, {}))
        self.assertIn("Unknown bits", format_value(fields["safety_status_bits"], 1 << 31, {}))
        self.assertIn("Unavailable", format_value(fields["safety_mode"], None, {}))
        self.assertEqual(enum_text(99, SAFETY_MODES), "Unknown (99)")
        self.assertEqual(format_value(fields["standard_analog_input0"], 4., {"analog_io_types": 0}), "4.000 mA")
        self.assertEqual(format_value(fields["standard_analog_output1"], 4., {"analog_io_types": 8}), "4.000 V")
        self.assertEqual(format_value(fields["speed_scaling"], .25, {}), "25.000 %")
