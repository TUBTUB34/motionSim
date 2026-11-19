"""Background RTDE and installation jobs; UI reads snapshots on its own thread."""
from dataclasses import dataclass, field
import queue
import threading
import time

from rtde_client import DATA_TIMEOUT, RTDEClient
from transfer import InstallationTransfer

# The controller closes a stream its client (or the link) cannot keep up with,
# while the robot itself stays up. Such a drop is reopened for up to DATA_TIMEOUT;
# a link that drops again within STEADY_AFTER is asked for fewer samples per second.
RATES = (125., 50., 25., 10.)
RETRY_EVERY = 2.
STEADY_AFTER = 120.

def reconnect_reason(session):
    """Why the stream is being reopened, or None (also for sessions without reconnect support)."""
    reason = getattr(session, 'reconnecting', None)
    return reason if isinstance(reason, str) else None


@dataclass
class HostKeyQuestion:
    hostname: str
    fingerprint: str
    answered: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False
    replacement: bool = False

class RobotSession:
    def __init__(self, settings, client_factory=RTDEClient, transfer_factory=InstallationTransfer):
        self.settings = settings
        self.client_factory = client_factory
        self.client = client_factory(settings.ip)
        # Requested stream rate in Hz, lowered only when a link keeps dropping.
        self.rate = RATES[0]
        self.client.frequency = self.rate
        self.reconnects = 0
        # While the stream is being reopened: why it was closed. None otherwise.
        self.reconnecting = None
        self._connected_at = 0.
        self.transfer = transfer_factory()
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.latest = None
        # Optional callable receiving every sample on the receiver thread.
        self.sink = None
        self.sample_lock = threading.Lock()
        self.threads = []

    def start(self):
        worker = threading.Thread(target=self._receive, daemon=True)
        self.threads.append(worker)
        worker.start()

    def stop(self):
        self.stop_event.set()
        self.client.close()
        self.transfer.close()

    def sample(self):
        with self.sample_lock:
            return self.latest

    def _receive(self):
        try:
            self.client.connect()
            self._connected_at = time.monotonic()
            first = True
            while not self.stop_event.is_set():
                try:
                    sample = self.client.receive()
                except ConnectionError as error:
                    # Only a stream that worked is reopened; a robot that never
                    # streamed still fails at once with the real reason.
                    if first or self.stop_event.is_set():
                        raise
                    self._reopen(error)
                    continue
                if self.stop_event.is_set():
                    break
                with self.sample_lock:
                    self.latest = sample
                sink = self.sink
                if sink is not None:
                    sink(sample)
                if first:
                    first = False
                    self.events.put(("connected", None))
                    if self.settings.can_load_payload:
                        worker = threading.Thread(target=self._installation, daemon=True)
                        self.threads.append(worker)
                        worker.start()
                    else:
                        self.events.put(("installation_error",
                            "Payload info will not be loaded without both a username and password."))
        except Exception as error:
            if not self.stop_event.is_set():
                self.events.put(("error", self._message(error)))
        finally:
            self.stop_event.set()
            self.client.close()
            self.transfer.close()

    def _reopen(self, error):
        """Reconnect after the robot closed a working stream; raise when that fails for DATA_TIMEOUT."""
        reason = self._message(error)
        dropped_at = time.monotonic()
        # The first drop retries at the same rate; dropping again soon after a
        # reconnect means this link cannot carry it.
        if self.reconnects and dropped_at - self._connected_at < STEADY_AFTER and self.rate != RATES[-1]:
            self.rate = RATES[RATES.index(self.rate) + 1] if self.rate in RATES else RATES[-1]
        self.reconnecting = reason
        self.events.put(("reconnecting", reason))
        deadline = dropped_at + DATA_TIMEOUT
        while True:
            self.client.close()
            client = self.client_factory(self.settings.ip)
            client.frequency = self.rate
            self.client = client
            if self.stop_event.is_set():
                client.close()
                return
            try:
                client.connect()
            except (OSError, ConnectionError) as retry:
                if self.stop_event.is_set():
                    return
                if time.monotonic() + RETRY_EVERY >= deadline:
                    raise ConnectionError(f"{reason} Reconnecting failed for {DATA_TIMEOUT:.0f} s: "
                                          f"{self._message(retry)}") from retry
                self.stop_event.wait(RETRY_EVERY)
                continue
            self._connected_at = time.monotonic()
            self.reconnects += 1
            self.reconnecting = None
            self.events.put(("reconnected", self.rate))
            return

    def _message(self, error):
        message = str(error) or type(error).__name__
        if self.settings.password:
            message = message.replace(self.settings.password, "[redacted]")
        return message

    def _confirm_host(self, hostname, fingerprint, replacement=False):
        question = HostKeyQuestion(hostname, fingerprint, replacement=replacement)
        self.events.put(("host_key", question))
        deadline = time.monotonic() + 60
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            if question.answered.wait(.1):
                return question.accepted
        return False

    def _installation(self):
        try:
            data = self.transfer.fetch(self.settings, self.stop_event, self._confirm_host)
            if not self.stop_event.is_set():
                self.events.put(("installation", data))
        except Exception as error:
            if not self.stop_event.is_set():
                self.events.put(("installation_error", "Installation not loaded: " + self._message(error)))
