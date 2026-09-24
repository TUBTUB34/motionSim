"""Background RTDE and installation jobs; UI reads snapshots on its own thread."""
from dataclasses import dataclass, field
import queue
import threading
import time

from rtde_client import RTDEClient
from transfer import InstallationTransfer

@dataclass
class HostKeyQuestion:
    hostname: str
    fingerprint: str
    answered: threading.Event = field(default_factory=threading.Event)
    accepted: bool = False

class RobotSession:
    def __init__(self, settings, client_factory=RTDEClient, transfer_factory=InstallationTransfer):
        self.settings = settings
        self.client = client_factory(settings.ip)
        self.transfer = transfer_factory()
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.latest = None
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
            first = True
            while not self.stop_event.is_set():
                sample = self.client.receive()
                if self.stop_event.is_set():
                    break
                with self.sample_lock:
                    self.latest = sample
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

    def _message(self, error):
        message = str(error) or type(error).__name__
        if self.settings.password:
            message = message.replace(self.settings.password, "[redacted]")
        return message

    def _confirm_host(self, hostname, fingerprint):
        question = HostKeyQuestion(hostname, fingerprint)
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
