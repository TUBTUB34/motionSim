"""Read-only RTDE v2 client. No input recipes, scripts, or motion commands."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
from motionstudio.core.telemetry import BASE_FIELDS, STATUS_FIELDS, TYPE_FORMATS
import math
import socket
import struct
import time

# Slow robot links can pause the stream for several seconds without being broken.
# Connecting stays short so a wrong address fails quickly; only a long silence
# on an established stream ends the session.
CONNECT_TIMEOUT = 15.
DATA_TIMEOUT = 60.
# Seconds without a sample before the UI says the link is slow (not broken).
STALE_AFTER = 2.
# The controller closes the stream when the client falls behind and its buffer
# overflows. Room for several seconds of 125 Hz packets rides out a busy PC; it
# is set before connecting so the TCP window can open that far.
RECEIVE_BUFFER = 4 * 1024 * 1024


def open_socket(address, timeout):
    """socket.create_connection with a large receive buffer requested before the handshake."""
    error = None
    for family, kind, protocol, _name, target in socket.getaddrinfo(*address, type=socket.SOCK_STREAM):
        sock = socket.socket(family, kind, protocol)
        try:
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RECEIVE_BUFFER)
            except OSError:
                pass  # The system default still works; it is only less forgiving.
            sock.settimeout(timeout)
            sock.connect(target)
            return sock
        except OSError as exc:
            error = exc
            sock.close()
    raise error or OSError(f"Could not resolve {address[0]}.")

@dataclass(frozen=True)
class RobotSample:
    timestamp: float
    joints: tuple
    tcp_pose: tuple
    received_at: float
    telemetry: Mapping = field(default_factory=lambda: MappingProxyType({}))

class RTDEClient:
    def __init__(self, host, port=30004, timeout=CONNECT_TIMEOUT, socket_factory=open_socket, telemetry=True,
                 frequency=125., data_timeout=DATA_TIMEOUT):
        self.host, self.port, self.timeout, self.data_timeout = host, port, timeout, data_timeout
        # The controller clamps the requested output rate to what it supports.
        self.frequency = frequency
        self.socket_factory = socket_factory
        self.sock = None
        self._buffer = bytearray()
        self.recipe_id = None
        self.fields = BASE_FIELDS + (STATUS_FIELDS if telemetry else ())
        self.decoder = None

    def close(self):
        sock, self.sock = self.sock, None
        self._buffer = bytearray()
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _read_exact(self, size):
        # One recv takes everything waiting (often many packets), so the reader
        # needs far fewer turns on the interpreter than one recv per header and body.
        buffer = self._buffer
        while len(buffer) < size:
            sock = self.sock
            if sock is None:
                raise ConnectionError("RTDE connection closed.")
            block = sock.recv(65536)
            if not block:
                raise ConnectionError("Robot closed the RTDE connection.")
            buffer.extend(block)
        data = bytes(buffer[:size])
        del buffer[:size]
        return data

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
            self._setup_outputs()
            if self._request(83) != b"\x01":
                raise ConnectionError("Robot refused to start the RTDE stream.")
            self.sock.settimeout(self.data_timeout)
        except Exception:
            self.close()
            raise

    def _setup_outputs(self):
        # Older firmware rejects the entire recipe when any field is NOT_FOUND.
        # Remove only explicitly unsupported optional fields, then renegotiate.
        while True:
            names = ",".join(f.name for f in self.fields).encode("ascii")
            response = self._request(79, struct.pack("!d", self.frequency) + names)
            if not response:
                raise ConnectionError("Empty RTDE output recipe response.")
            try:
                kinds = response[1:].decode("ascii").split(",")
            except UnicodeDecodeError as error:
                raise ConnectionError("Invalid RTDE output types.") from error
            if len(kinds) != len(self.fields):
                raise ConnectionError("Robot returned an incomplete RTDE output recipe.")
            missing = []
            for item, kind in zip(self.fields, kinds):
                if kind == "NOT_FOUND" and item not in BASE_FIELDS:
                    missing.append(item)
                elif kind != item.kind:
                    raise ConnectionError(f"Robot rejected RTDE output {item.name}: {kind}.")
            if missing:
                self.fields = tuple(f for f in self.fields if f not in missing)
                continue
            if response[0] == 0:
                raise ConnectionError("Robot rejected the RTDE output recipe.")
            self.recipe_id = response[0]
            self.decoder = struct.Struct("!" + "".join(TYPE_FORMATS[f.kind] for f in self.fields))
            return

    def receive(self):
        deadline = time.monotonic() + self.data_timeout
        while time.monotonic() < deadline:
            try:
                kind, data = self._packet()
            except socket.timeout:
                break
            if kind == 77:
                continue
            if kind != 85 or len(data) != self.decoder.size + 1 or data[0] != self.recipe_id:
                raise ConnectionError("Invalid RTDE data packet.")
            unpacked = self.decoder.unpack(data[1:])
            if not all(math.isfinite(v) for v in unpacked):
                raise ConnectionError("Robot sent non-finite RTDE values.")
            values = {}
            offset = 0
            for item in self.fields:
                count = int(item.kind[6]) if item.kind.startswith("VECTOR") else 1
                values[item.name] = unpacked[offset:offset+count] if count > 1 else unpacked[offset]
                offset += count
            return RobotSample(values["timestamp"], values["actual_q"], values["actual_TCP_pose"],
                               time.monotonic(), MappingProxyType(values))
        raise TimeoutError(f"No RTDE data from the robot for {self.data_timeout:.0f} s.")
