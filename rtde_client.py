"""Read-only RTDE v2 client. No input recipes, scripts, or motion commands."""
from dataclasses import dataclass
import math
import socket
import struct
import time

@dataclass(frozen=True)
class RobotSample:
    timestamp: float
    joints: tuple
    tcp_pose: tuple
    received_at: float

class RTDEClient:
    def __init__(self, host, port=30004, timeout=3., socket_factory=socket.create_connection):
        self.host, self.port, self.timeout = host, port, timeout
        self.socket_factory = socket_factory
        self.sock = None
        self.recipe_id = None

    def close(self):
        sock, self.sock = self.sock, None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _read_exact(self, size):
        data = bytearray()
        while len(data) < size:
            sock = self.sock
            if sock is None:
                raise ConnectionError("RTDE connection closed.")
            block = sock.recv(size - len(data))
            if not block:
                raise ConnectionError("Robot closed the RTDE connection.")
            data.extend(block)
        return bytes(data)

    def _packet(self):
        size, kind = struct.unpack("!HB", self._read_exact(3))
        if size < 3:
            raise ConnectionError("Invalid RTDE packet length.")
        return kind, self._read_exact(size - 3)

    def _request(self, kind, data=b""):
        self.sock.sendall(struct.pack("!HB", len(data) + 3, kind) + data)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            received_kind, payload = self._packet()
            if received_kind == kind:
                return payload
            if received_kind != 77:
                raise ConnectionError("Unexpected RTDE handshake response.")
        raise TimeoutError("RTDE handshake timed out.")

    def connect(self):
        try:
            self.sock = self.socket_factory((self.host, self.port), timeout=self.timeout)
            self.sock.settimeout(self.timeout)
            if self._request(86, struct.pack("!H", 2)) != b"\x01":
                raise ConnectionError("Robot does not support RTDE protocol v2.")
            names = b"timestamp,actual_q,actual_TCP_pose"
            response = self._request(79, struct.pack("!d", 30.) + names)
            if not response or response[0] == 0 or response[1:] != b"DOUBLE,VECTOR6D,VECTOR6D":
                raise ConnectionError("Robot rejected the joint/TCP RTDE output recipe.")
            self.recipe_id = response[0]
            if self._request(83) != b"\x01":
                raise ConnectionError("Robot refused to start the RTDE stream.")
        except Exception:
            self.close()
            raise

    def receive(self):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            kind, data = self._packet()
            if kind == 77:
                continue
            if kind != 85 or len(data) != 105 or data[0] != self.recipe_id:
                raise ConnectionError("Invalid RTDE joint data packet.")
            values = struct.unpack("!13d", data[1:])
            if not all(math.isfinite(v) for v in values):
                raise ConnectionError("Robot sent non-finite joint/TCP values.")
            return RobotSample(values[0], values[1:7], values[7:13], time.monotonic())
        raise TimeoutError("No joint data received from RTDE.")
