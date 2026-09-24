import gzip
import math
import socket
import struct
import threading
import time
import unittest
from unittest.mock import Mock

import numpy as np
from connection import make_settings
from installation import decode_installation, numbers, MAX_FILE_SIZE
from kinematics import forward_kinematics, mounting_matrix, pose_matrix, view_base_transform
from rtde_client import RTDEClient, RobotSample
from session import RobotSession

# Minimal synthetic fixtures retain the field layout of UR's published
# PolyScope 3.2 / 5.1 installation examples, with non-zero test values.
XML = b"""<Installation>
<TCPSettings activePose="gripper" toolPayload="2.5" toolPayloadCenterOfGravity="0.01, 0.02, 0.1">
 <availablePoses>
  <tcp name="unused" offset="0,0,0,0,0,0"/>
  <tcp name="gripper" offset="0,0,0.2,0,0,1.5707963267948966"/>
 </availablePoses>
</TCPSettings>
<GeomFeatures><SetupFeatureContainerNode><CameraView><worldTransform>
 <WorldtoMarshal baseAngle="0.3" tiltAngle="1.5707963267948966"/>
</worldTransform></CameraView></SetupFeatureContainerNode></GeomFeatures>
</Installation>"""

# Synthetic values with the structure observed in the supplied PolyScope 5.22 file.
MODERN_XML = b"""<Installation>
<Version major="5" minor="22" bugfix="0"/>
<TCPSettings activePose="TCP"><availablePoses>
 <tcp name="TCP" offset="0, -0.2, 0.15, 3.1416, 0, 0"/>
</availablePoses></TCPSettings>
<Features><CameraView><worldTransform>
 <WorldtoMarshal baseAngle="0.4" tiltAngle="1.2"/>
</worldTransform></CameraView></Features>
<PayloadSettings>
 <Payload name="empty" mass="1" defaultPayload="false" centerOfGravity="0,0,0"/>
 <Payload name="full" mass="3" defaultPayload="false" centerOfGravity="0,0,0.1"/>
 <Payload name="tool" mass="2" defaultPayload="true" centerOfGravity="0.01,0.02,0.03"/>
</PayloadSettings>
</Installation>"""

def packet(kind, data=b""):
    return struct.pack("!HB", len(data)+3, kind) + data

class FragmentedSocket:
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = []
        self.closed = False
    def settimeout(self, timeout):
        pass
    def recv(self, size):
        size = min(size, 2, len(self.incoming))
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result
    def sendall(self, data):
        self.sent.append(data)
    def shutdown(self, _):
        pass
    def close(self):
        self.closed = True

def handshake():
    return packet(86, b"\x01") + packet(79, b"\x07DOUBLE,VECTOR6D,VECTOR6D") + packet(83, b"\x01")

def sample_packet(recipe=7, values=None):
    return packet(85, bytes([recipe]) + struct.pack("!13d", *(values or [1.] + list(range(6)) + [.1]*6)))

class SettingsTests(unittest.TestCase):
    def test_defaults_and_path(self):
        s = make_settings(" 127.0.0.1 ", "", "", " ")
        self.assertEqual((s.model, s.remote_path), ("UR15", "/programs/default.installation"))
        self.assertFalse(s.can_load_payload)

    def test_custom_path_extension_and_password(self):
        s = make_settings("::1", " operator ", " secret ", "my arm.installation", "/programs/test folder", "UR5e")
        self.assertEqual(s.remote_path, "/programs/test folder/my arm.installation")
        self.assertTrue(s.can_load_payload)
        self.assertEqual(s.password, " secret ")
        self.assertNotIn("secret", repr(s))

    def test_missing_one_credential(self):
        for user, password in [("", "p"), ("u", "")]:
            self.assertFalse(make_settings("127.0.0.1", user, password, "").can_load_payload)

    def test_invalid_inputs(self):
        for kwargs in ({"ip": ""}, {"ip": "999.2.3.4"}, {"installation": "../bad"},
                       {"directory": "relative"}, {"installation": ".installation"}, {"model": "unknown"}):
            values = dict(ip="127.0.0.1", username="", password="", installation="")
            values.update(kwargs)
            with self.assertRaises(ValueError):
                make_settings(**values)

class InstallationTests(unittest.TestCase):
    def test_plain_and_gzip(self):
        for raw in (XML, gzip.compress(XML)):
            data = decode_installation(raw)
            self.assertEqual(data.tcp_name, "gripper")
            self.assertEqual(data.tcp[:3], (0., 0., .2))
            self.assertEqual(data.payload_mass, 2.5)
            self.assertEqual(data.payload_cog, (.01, .02, .1))
            self.assertAlmostEqual(data.mounting[1], math.pi/2)
            self.assertEqual(data.warnings, ())

    def test_java_class_tag(self):
        data = decode_installation(XML.replace(b"TCPSettings", b"com.ur.view.tcp.domain.TCPSettingsImpl"))
        self.assertEqual(data.tcp_name, "gripper")

    def test_unknown_active_tcp_is_not_first_tcp(self):
        data = decode_installation(XML.replace(b'activePose="gripper"', b'activePose="missing"'))
        self.assertIsNone(data.tcp)
        self.assertEqual(data.payload_mass, 2.5)
        self.assertTrue(data.warnings)

    def test_missing_and_invalid_fields_are_explicit(self):
        data = decode_installation(b"<Installation/>")
        self.assertIsNone(data.tcp)
        self.assertIsNone(data.payload_mass)
        self.assertIsNone(data.mounting)
        self.assertEqual(len(data.warnings), 3)
        data = decode_installation(XML.replace(b'toolPayload="2.5"', b'toolPayload="-1"'))
        self.assertIsNone(data.payload_mass)

    def test_named_payload_selection(self):
        payload = b'<PayloadSettings defaultPayload="heavy"><payloads><payload name="light" mass="1" cog="0,0,0"/><payload name="heavy" mass="5" cog="0,0,0.3"/></payloads></PayloadSettings>'
        data = decode_installation(XML.replace(b"</Installation>", payload+b"</Installation>"))
        self.assertEqual(data.payload_mass, 5)
        self.assertEqual(data.payload_cog, (0, 0, .3))

    def test_polyscope_522_payload_flag_and_features_mounting(self):
        for raw in (MODERN_XML, gzip.compress(MODERN_XML)):
            data = decode_installation(raw)
            self.assertEqual(data.tcp, (0, -.2, .15, 3.1416, 0, 0))
            self.assertEqual(data.payload_name, "tool")
            self.assertEqual(data.payload_mass, 2)
            self.assertEqual(data.payload_cog, (.01, .02, .03))
            self.assertEqual(data.mounting, (.4, 1.2))
            self.assertEqual(data.warnings, ())

    def test_multiple_or_no_default_payload_is_not_guessed(self):
        for raw in (MODERN_XML.replace(b'defaultPayload="false"', b'defaultPayload="true"'),
                    MODERN_XML.replace(b'defaultPayload="true"', b'defaultPayload="false"')):
            data = decode_installation(raw)
            self.assertIsNone(data.payload_mass)
            self.assertTrue(any("ambiguous" in warning for warning in data.warnings))
            self.assertEqual(data.mounting, (.4, 1.2))

    def test_explicit_payload_selection_overrides_default_flag(self):
        raw = MODERN_XML.replace(b"<PayloadSettings>", b'<PayloadSettings activePayload="full">')
        data = decode_installation(raw)
        self.assertEqual(data.payload_name, "full")
        self.assertEqual(data.payload_mass, 3)

    def test_mounting_ignores_urcap_transforms(self):
        extra = b'<Contributions><WorldtoMarshal baseAngle="9" tiltAngle="9"/></Contributions>'
        data = decode_installation(MODERN_XML.replace(b"</Installation>", extra+b"</Installation>"))
        self.assertEqual(data.mounting, (.4, 1.2))
        self.assertEqual(data.warnings, ())

    def test_missing_cog_not_invented(self):
        data = decode_installation(XML.replace(b' toolPayloadCenterOfGravity="0.01, 0.02, 0.1"', b""))
        self.assertEqual(data.payload_mass, 2.5)
        self.assertIsNone(data.payload_cog)
        self.assertTrue(data.warnings)

    def test_reject_nonfinite_wrong_root_and_entities(self):
        for value in ("nan,0,0", "inf,0,0", "1,2", "bad"):
            with self.assertRaises(ValueError):
                numbers(value, 3)
        with self.assertRaises(ValueError):
            decode_installation(b"<Other/>")
        with self.assertRaises(Exception):
            decode_installation(b'<!DOCTYPE Installation [<!ENTITY x "evil">]><Installation>&x;</Installation>')

    def test_compressed_size_limit(self):
        with self.assertRaises(ValueError):
            decode_installation(gzip.compress(b"x" * (MAX_FILE_SIZE+1)))

class KinematicsTests(unittest.TestCase):
    def test_ur15_zero_pose_against_dh_sum(self):
        frames = forward_kinematics("UR15", [0]*6)
        np.testing.assert_allclose(frames[-1][:3, 3], [-1.1639, -.3258, .0825], atol=1e-10)
        self.assertEqual(len(frames), 7)

    def test_rotation_vector_and_mounting_composition(self):
        r = pose_matrix([0,0,.2,0,0,math.pi/2])
        np.testing.assert_allclose(r[:3,:3] @ [1,0,0], [0,1,0], atol=1e-12)
        q = [.1,-1.2,.3,-.7,.5,.9]
        m = mounting_matrix(.3, math.pi)
        base = forward_kinematics("UR15", q)
        mounted = forward_kinematics("UR15", q, m)
        for a,b in zip(base, mounted):
            np.testing.assert_allclose(b, m @ a)
        np.testing.assert_allclose(m[:3,:3] @ [0,0,1], [0,0,-1], atol=1e-12)

    def test_display_frame_alignment(self):
        mount = mounting_matrix(math.pi, math.pi/4)
        pose = [.3, -.2, .7, .4, -.5, 1.2]
        np.testing.assert_allclose(view_base_transform("World (mounting)", mount, pose), mount)
        np.testing.assert_allclose(view_base_transform("Robot base", mount, pose), np.eye(4))
        tcp_base = view_base_transform("Live TCP", mount, pose)
        np.testing.assert_allclose(tcp_base @ pose_matrix(pose), np.eye(4), atol=1e-12)
        with self.assertRaises(ValueError):
            view_base_transform("invalid", mount, pose)

    def test_view_frames_preserve_arm_and_marker_geometry(self):
        mount = mounting_matrix(math.pi, math.pi/4)
        pose = [.3, -.2, .7, .4, -.5, 1.2]
        q = [.1, -1.2, .3, -.7, .5, .9]
        world_frames = forward_kinematics("UR15", q, mount)
        saved_offset = pose_matrix([0, 0, .2, .3, 0, 0])
        cog = np.array([.02, .04, .1, 1])
        for frame in ("World (mounting)", "Robot base", "Live TCP"):
            base = view_base_transform(frame, mount, pose)
            world_to_view = base @ np.linalg.inv(mount)
            displayed = forward_kinematics("UR15", q, base)
            for world, shown in zip(world_frames, displayed):
                np.testing.assert_allclose(shown, world_to_view @ world, atol=1e-12)
            np.testing.assert_allclose(displayed[-1] @ saved_offset,
                                       world_to_view @ world_frames[-1] @ saved_offset, atol=1e-12)
            np.testing.assert_allclose(displayed[-1] @ cog,
                                       world_to_view @ world_frames[-1] @ cog, atol=1e-12)

    def test_tcp_and_cog_follow_flange(self):
        flange = forward_kinematics("UR15", [0]*6)[-1]
        tcp = flange @ pose_matrix([0,0,.2,0,0,0])
        cog = flange @ np.array([0,0,.1,1])
        self.assertAlmostEqual(np.linalg.norm(tcp[:3,3]-flange[:3,3]), .2)
        self.assertAlmostEqual(np.linalg.norm(cog[:3]-flange[:3,3]), .1)

class RTDETests(unittest.TestCase):
    def make_client(self, incoming):
        sock = FragmentedSocket(incoming)
        client = RTDEClient("127.0.0.1", socket_factory=lambda *a, **kw: sock)
        return client, sock

    def test_fragmented_handshake_and_data(self):
        client, sock = self.make_client(handshake()+sample_packet())
        client.connect()
        sample = client.receive()
        self.assertEqual(sample.joints, tuple(range(6)))
        self.assertEqual(sample.tcp_pose, (.1,)*6)
        self.assertEqual([msg[2] for msg in sock.sent], [86,79,83])
        self.assertEqual(sock.sent[1][11:], b"timestamp,actual_q,actual_TCP_pose")
        client.close()
        self.assertTrue(sock.closed)

    def test_rejected_protocol_and_recipe_close(self):
        for incoming in (packet(86,b"\0"), packet(86,b"\1")+packet(79,b"\0NOT_FOUND"),
                         packet(86,b"\1")+packet(79,b"\1DOUBLE,VECTOR6D,VECTOR6D")+packet(83,b"\0")):
            client, sock = self.make_client(incoming)
            with self.assertRaises(ConnectionError):
                client.connect()
            self.assertTrue(sock.closed)

    def test_reject_bad_packet_and_nonfinite_values(self):
        for bad in (sample_packet(2), packet(85,b"short"), sample_packet(values=[float("nan")]*13)):
            client, _ = self.make_client(handshake()+bad)
            client.connect()
            with self.assertRaises(ConnectionError):
                client.receive()

    def test_eof_and_invalid_header(self):
        for incoming in (b"", struct.pack("!HB",2,85)):
            client, _ = self.make_client(handshake()+incoming)
            client.connect()
            with self.assertRaises(ConnectionError):
                client.receive()

    def test_text_message_does_not_break_stream(self):
        client, _ = self.make_client(packet(77,b"notice")+handshake()+packet(77,b"info")+sample_packet())
        client.connect()
        self.assertEqual(client.receive().timestamp, 1)

    def test_timeout_is_propagated(self):
        client, sock = self.make_client(handshake())
        client.connect()
        sock.recv = Mock(side_effect=socket.timeout("timed out"))
        with self.assertRaises(TimeoutError):
            client.receive()

class SessionTests(unittest.TestCase):
    def test_installation_failure_does_not_stop_joint_stream(self):
        stopped = threading.Event()
        class Client:
            def __init__(self, _):
                self.counter = 0
            def connect(self):
                pass
            def receive(self):
                if stopped.wait(.005):
                    raise ConnectionError("closed")
                self.counter += 1
                return RobotSample(self.counter, (0,)*6, (0,)*6, time.monotonic())
            def close(self):
                stopped.set()
        transfer = Mock()
        transfer.fetch.side_effect = ValueError("bad installation")
        session = RobotSession(make_settings("127.0.0.1","u","p",""), Client, lambda: transfer)
        session.start()
        deadline = time.monotonic()+2
        event = None
        while time.monotonic() < deadline:
            kind, data = session.events.get(timeout=1)
            if kind == "installation_error":
                event = data
                break
        self.assertIsNotNone(event)
        before = session.sample().timestamp
        time.sleep(.03)
        self.assertGreater(session.sample().timestamp, before)
        session.stop()
        for thread in session.threads:
            thread.join(1)
            self.assertFalse(thread.is_alive())

    def test_no_credentials_skips_scp_and_stop_during_connect(self):
        entered, release = threading.Event(), threading.Event()
        class Client:
            def __init__(self, _):
                self.closed = False
            def connect(self):
                entered.set()
                release.wait(1)
            def receive(self):
                raise AssertionError("Must not receive after cancellation")
            def close(self):
                self.closed = True
        transfer = Mock()
        session = RobotSession(make_settings("127.0.0.1","","",""), Client, lambda: transfer)
        session.start()
        self.assertTrue(entered.wait(1))
        session.stop()
        release.set()
        session.threads[0].join(1)
        transfer.fetch.assert_not_called()
        self.assertTrue(session.client.closed)

if __name__ == "__main__":
    unittest.main()
