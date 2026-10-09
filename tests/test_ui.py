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
                    from telemetry import BASE_FIELDS, STATUS_FIELDS
                    from tests.test_telemetry import recipe, data_packet
                    fields = BASE_FIELDS + STATUS_FIELDS
                    for expected, response in [(86, packet(86, b"\1")), (79, recipe(fields)), (83, packet(83, b"\1"))]:
                        size, kind = struct.unpack("!HB", self.read(sock, 3))
                        payload = self.read(sock, size-3)
                        self.messages.append((kind, payload))
                        if kind != expected:
                            raise ValueError("Unexpected RTDE request")
                        sock.sendall(response)
                    tick = 0
                    while not self.stop.wait(.025):
                        tick += 1
                        values = (tick*.025, math.sin(tick*.02)*.4,-1.2,1.6,-1.8,-1.57,0,
                                  -.7,-.25,.7,0,0,0)
                        sock.sendall(data_packet(fields, {"timestamp": values[0], "actual_q": values[1:7],
                            "actual_TCP_pose": values[7:], "script_control_line": 42, "runtime_state": 2,
                            "safety_mode": 1, "safety_status": 1, "robot_mode": 7, "payload": 2.5}))
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

    def test_stationary_and_hidden_poses_skip_mesh_work(self):
        import tkinter as tk
        from dataclasses import replace
        from rtde_client import RobotSample
        from view import RobotView
        self.root = tk.Tk()
        viewer = RobotView(self.root, "UR15")
        sample = RobotSample(1, (0, -1, 1, 0, 0, 0), (.3, -.2, .7, 0, 0, 0), time.monotonic())
        try:
            viewer.update_robot(sample, None)
            with patch.object(viewer, "redraw", wraps=viewer.redraw) as draw:
                for i in range(100):
                    viewer.update_robot(replace(sample, timestamp=i), None)
                self.assertEqual(draw.call_count, 0)
                moved = replace(sample, joints=(.2, -1, 1, 0, 0, 0))
                viewer.update_robot(moved, None, render=False)
                self.assertEqual(draw.call_count, 0)
                self.assertIs(viewer.sample, moved)
                viewer.update_robot(sample, None)
                self.assertEqual(draw.call_count, 1)
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
                self.pump_until(lambda: "URScript line 42" in app.program_text.get())
                self.assertEqual(app.status_panel.tree.item("payload", "values"), ("2.500 kg",))
                app.tabs.select(app.status_panel)
                self.root.update()
                with patch.object(app.viewer, "redraw", wraps=app.viewer.redraw) as draw:
                    timestamp = app.last_sample.timestamp
                    self.pump_until(lambda: app.last_sample.timestamp > timestamp)
                    self.assertEqual(draw.call_count, 0)
                app.tabs.select(app.simulator_tab)
                self.root.update()
                self.assertIn("Payload: 2.5 kg", app.details.get())
                self.assertGreater(app.viewer.rendered_triangles, 100)
                unchanged_joints = app.last_sample.joints
                for frame in ("Robot base", "World (mounting)", "Live TCP"):
                    app.view_frame.set(frame)
                    app.frame_selector.event_generate("<<ComboboxSelected>>")
                    self.assertEqual(app.viewer.view_frame, frame)
                    self.assertEqual(app.last_sample.joints, unchanged_joints)
                    self.assertGreater(app.viewer.rendered_triangles, 100)
                app.viewer.zoom_by(1.2)
                app.viewer.reset_camera()
                app.disconnect()
                self.assertIsNone(app.session)
                self.assertIn("Last received", app.program_text.get())
                self.assertIn("Last:", app.status_panel.summary_labels["safety_status"].cget("text"))
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
