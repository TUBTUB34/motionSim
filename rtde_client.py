"""Read-only RTDE v2 client. No input recipes, scripts, or motion commands."""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping
from telemetry import BASE_FIELDS, STATUS_FIELDS, TYPE_FORMATS
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
    telemetry: Mapping = field(default_factory=lambda: MappingProxyType({}))

class RTDEClient:
    def __init__(self, host, port=30004, timeout=3., socket_factory=socket.create_connection, telemetry=True):
        self.host, self.port, self.timeout = host, port, timeout
        self.socket_factory = socket_factory
        self.sock = None
        self.recipe_id = None
        self.fields = BASE_FIELDS + (STATUS_FIELDS if telemetry else ())
        self.decoder = None

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
            self._setup_outputs()
            if self._request(83) != b"\x01":
                raise ConnectionError("Robot refused to start the RTDE stream.")
        except Exception:
            self.close()
            raise

    def _setup_outputs(self):
        # Older firmware rejects the entire recipe when any field is NOT_FOUND.
        # Remove only explicitly unsupported optional fields, then renegotiate.
        while True:
            names = ",".join(f.name for f in self.fields).encode("ascii")
            response = self._request(79, struct.pack("!d", 30.) + names)
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
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            kind, data = self._packet()
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
        raise TimeoutError("No data received from RTDE.")
