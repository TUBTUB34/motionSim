"""Opt-in desktop integration test: MOTIONSIM_UI_TESTS=1 python -m unittest tests.test_ui."""
import math
import os
import socket
import struct
import threading
import time
import unittest
from unittest.mock import patch

from rtde_client import RTDEClient
from session import RobotSession
from installation import decode_installation
from tests.test_core import XML, packet

class MockRobot:
    """Small loopback RTDE server with real framing and continuously changing joints."""
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.stop = threading.Event()
        self.messages = []
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    @staticmethod
    def read(sock, length):
        data = b""
        while len(data) < length:
            chunk = sock.recv(length-len(data))
            if not chunk:
                raise ConnectionError("closed")
            data += chunk
        return data

    def run(self):
        while not self.stop.is_set():
            try:
                sock, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with sock:
                sock.settimeout(1)
                try:
                    for expected, response in [(86,b"\1"), (79,b"\7DOUBLE,VECTOR6D,VECTOR6D"), (83,b"\1")]:
                        size, kind = struct.unpack("!HB", self.read(sock, 3))
                        payload = self.read(sock, size-3)
                        self.messages.append((kind, payload))
                        if kind != expected:
                            raise ValueError("Unexpected RTDE request")
                        sock.sendall(packet(kind, response))
                    tick = 0
                    while not self.stop.wait(.025):
                        tick += 1
                        values = (tick*.025, math.sin(tick*.02)*.4,-1.2,1.6,-1.8,-1.57,0,
                                  -.7,-.25,.7,0,0,0)
                        sock.sendall(packet(85,b"\7"+struct.pack("!13d",*values)))
                except (OSError, ConnectionError):
                    pass

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(2)

@unittest.skipUnless(os.environ.get("MOTIONSIM_UI_TESTS") == "1", "requires a graphical Tk session")
class UITests(unittest.TestCase):
    def pump_until(self, condition, timeout=3):
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(.01)
        self.fail("Timed out waiting for UI state")

    def test_tcp_alignment_locks_until_reselected(self):
        import tkinter as tk
        import numpy as np
        from view import RobotView
        from rtde_client import RobotSample
        from kinematics import forward_kinematics, pose_matrix
        self.root = tk.Tk()
        viewer = RobotView(self.root, "UR15")
        first = RobotSample(1, (0, -1, 1, 0, 0, 0), (.3, -.2, .7, .4, -.5, 1.2), time.monotonic())
        moved = RobotSample(2, (.2, -.8, 1.2, 0, 0, 0), (.6, -.1, .8, .6, -.4, 1.4), time.monotonic())
        try:
            viewer.set_view_frame("Live TCP")
            self.assertIsNone(viewer.tcp_view_reference)
            with patch("view.forward_kinematics", wraps=forward_kinematics) as fk:
                viewer.update_robot(first, None)
                locked = fk.call_args.args[2].copy()
                np.testing.assert_allclose(locked @ pose_matrix(first.tcp_pose), np.eye(4), atol=1e-12)
                viewer.update_robot(moved, None)
                np.testing.assert_allclose(fk.call_args.args[2], locked, atol=1e-12)
                self.assertFalse(np.allclose(locked @ pose_matrix(moved.tcp_pose), np.eye(4)))
                self.assertEqual(viewer.tcp_view_reference, first.tcp_pose)
                viewer.reset_camera()
                np.testing.assert_allclose(fk.call_args.args[2], locked, atol=1e-12)
                viewer.set_view_frame("Live TCP")
                np.testing.assert_allclose(fk.call_args.args[2] @ pose_matrix(moved.tcp_pose),
                                           np.eye(4), atol=1e-12)
                viewer.set_view_frame("Robot base")
                self.assertIsNone(viewer.tcp_view_reference)
                np.testing.assert_allclose(fk.call_args.args[2], np.eye(4), atol=1e-12)
        finally:
            self.root.destroy()

    def test_connect_live_render_disconnect_reconnect(self):
        import tkinter as tk
        from main import SimulatorApp
        robot = MockRobot()
        class Transfer:
            def fetch(self, settings, stop, confirm):
                return decode_installation(XML)
            def close(self):
                pass
        self.root = tk.Tk()
        app = SimulatorApp(self.root)
        errors = []
        self.root.report_callback_exception = lambda *args: errors.append(args)
        try:
            with patch("main.RobotSession", lambda settings: RobotSession(
                    settings, lambda host: RTDEClient(host, port=robot.port), Transfer)):
                app.ip.set("bad")
                app.submit()
                self.assertIsNone(app.session)
                self.assertIn("valid robot IP", app.error.get())
                app.ip.set("127.0.0.1")
                app.username.set("operator")
                app.password.set("synthetic-password")
                app.submit()
                self.pump_until(lambda: app.last_sample is not None and app.installation_data is not None)
                first = app.last_sample.timestamp
                self.pump_until(lambda: app.last_sample.timestamp > first)
                self.assertIn("Live", app.status.get())
                self.assertIn("Payload: 2.5 kg", app.details.get())
                self.assertGreater(sum(app.viewer.type(i) == "polygon" for i in app.viewer.find_all()), 100)
                unchanged_joints = app.last_sample.joints
                for frame in ("Robot base", "World (mounting)", "Live TCP"):
                    app.view_frame.set(frame)
                    app.frame_selector.event_generate("<<ComboboxSelected>>")
                    self.assertEqual(app.viewer.view_frame, frame)
                    self.assertEqual(app.last_sample.joints, unchanged_joints)
                    self.assertGreater(sum(app.viewer.type(i) == "polygon" for i in app.viewer.find_all()), 100)
                app.viewer.zoom_by(1.2)
                app.viewer.reset_camera()
                app.disconnect()
                self.assertIsNone(app.session)
                last = app.last_sample
                app.toggle_connection()
                self.pump_until(lambda: app.last_sample is not None and app.last_sample is not last)
                self.assertEqual(app.viewer.view_frame, "Live TCP")
                app.edit_connection()
                self.assertIsNone(app.session)
                self.assertEqual(app.model.get(), "UR15")
                self.assertEqual(app.directory.get(), "/programs")
                app.username.set("")
                app.password.set("")
                app.submit()
                self.pump_until(lambda: app.last_sample is not None and "without both" in app.details.get())
                self.assertIsNone(app.installation_data)
                self.assertEqual([kind for kind, _ in robot.messages], [86,79,83]*3)
                self.assertEqual(errors, [])
        finally:
            app.close()
            robot.close()

if __name__ == "__main__":
    unittest.main()
