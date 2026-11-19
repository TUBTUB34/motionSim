"""AsyncSSH bulk reads against a real in-process SFTP server on localhost."""
import asyncio
import io
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

import paramiko

from motionstudio.remote import fast_transfer
from motionstudio.remote.stream import copy_remote

try:
    import asyncssh
except ImportError:  # pragma: no cover
    asyncssh = None


class _Server(asyncssh.SSHServer if asyncssh else object):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == 'root' and password == 'robot-password'


def start_server(root):
    """Run an SFTP server rooted at ``root``; returns (loop, server, port)."""
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    key = asyncssh.generate_private_key('ssh-ed25519')

    async def start():
        return await asyncssh.create_server(
            _Server, '127.0.0.1', 0, server_host_keys=[key],
            sftp_factory=lambda channel: asyncssh.SFTPServer(channel, chroot=str(root)),
            compression_algs=['zlib@openssh.com', 'zlib', 'none'])
    server = asyncio.run_coroutine_threadsafe(start(), loop).result(10)
    return loop, server, server.sockets[0].getsockname()[1]


@unittest.skipIf(asyncssh is None, 'AsyncSSH is not installed')
class FastTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name)
        cls.loop, cls.server, cls.port = start_server(cls.root)
        # Paramiko verifies the robot first, exactly as the jobs do.
        cls.client = paramiko.SSHClient()
        cls.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        cls.client.connect('127.0.0.1', cls.port, 'root', 'robot-password', look_for_keys=False, allow_agent=False)
        cls.host_key = cls.client.get_transport().get_remote_server_key()

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.close()
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.folder.cleanup()

    def fast(self, **kwargs):
        session = fast_transfer.FastSFTP.connect('127.0.0.1', 'root', 'robot-password', self.host_key,
                                                 port=self.port, **kwargs)
        self.addCleanup(session.close)
        return session

    def write(self, name, data):
        (self.root / name).write_bytes(data)
        return '/' + name

    def test_exact_copy_for_awkward_sizes(self):
        session = self.fast()
        for size in (0, 1, 32 * 1024 - 1, fast_transfer.BLOCK_SIZE, fast_transfer.BLOCK_SIZE + 1,
                     3 * 1024 * 1024 + 123):
            data = os.urandom(size)
            path = self.write(f'file-{size}.bin', data)
            local, seen = io.BytesIO(), []
            with session.open(path) as remote:
                self.assertEqual(copy_remote(remote, local, size, progress=seen.append), size)
            self.assertEqual(local.getvalue(), data)
            if size:
                self.assertEqual(seen[-1], size)

    def test_compressed_session_copies_text(self):
        data = b'2026-11-19 10:00:00 Protective stop C153A1\n' * 50000
        path = self.write('log_history.txt', data)
        local = io.BytesIO()
        with self.fast(compress=True).open(path) as remote:
            copy_remote(remote, local, len(data))
        self.assertEqual(local.getvalue(), data)

    def test_shorter_remote_file_is_an_error(self):
        path = self.write('short.bin', b'x' * 1000)
        with self.fast().open(path) as remote, self.assertRaises(OSError):
            copy_remote(remote, io.BytesIO(), 2000)

    def test_longer_remote_file_is_an_error_without_growth(self):
        path = self.write('long.bin', b'x' * 2000)
        with self.fast().open(path) as remote, self.assertRaisesRegex(OSError, 'changed'):
            copy_remote(remote, io.BytesIO(), 1000)

    def test_growing_log_includes_appended_bytes_up_to_ceiling(self):
        path = self.write('growing.txt', b'a' * 1000)
        with (self.root / 'growing.txt').open('ab') as stream:
            stream.write(b'b' * 500)
        local = io.BytesIO()
        with self.fast().open(path) as remote:
            self.assertEqual(copy_remote(remote, local, 1000, allow_growth=True, max_bytes=4000), 1500)
        self.assertEqual(local.getvalue(), b'a' * 1000 + b'b' * 500)
        with self.fast().open(path) as remote, self.assertRaisesRegex(ValueError, 'limit'):
            copy_remote(remote, io.BytesIO(), 1000, allow_growth=True, max_bytes=1200)

    def test_cancellation_stops_the_copy(self):
        path = self.write('cancel.bin', os.urandom(2 * 1024 * 1024))
        calls = []

        def check():
            calls.append(1)
            if len(calls) > 5:
                raise InterruptedError('cancelled')
        with self.fast().open(path) as remote, self.assertRaises(InterruptedError):
            copy_remote(remote, io.BytesIO(), 2 * 1024 * 1024, check_cancel=check)

    def test_different_host_key_is_refused(self):
        other = paramiko.RSAKey.generate(1024)
        with self.assertRaises(Exception) as caught:
            fast_transfer.FastSFTP.connect('127.0.0.1', 'root', 'robot-password', other, port=self.port)
        self.assertNotIsInstance(caught.exception, AssertionError)

    def test_wrong_password_is_refused(self):
        with self.assertRaises(Exception):
            fast_transfer.FastSFTP.connect('127.0.0.1', 'root', 'wrong', self.host_key, port=self.port)

    def test_close_releases_a_blocked_reader(self):
        session = self.fast()
        path = self.write('closing.bin', os.urandom(256 * 1024))
        remote = session.open(path)
        session.close()
        with self.assertRaises((ConnectionError, OSError, TimeoutError)):
            copy_remote(remote, io.BytesIO(), 256 * 1024)


@unittest.skipIf(asyncssh is None, 'AsyncSSH is not installed')
class JobIntegrationTests(unittest.TestCase):
    """A real LogJob: paramiko verifies the saved key, AsyncSSH carries the bytes."""
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.root = Path(cls.folder.name) / 'robot'
        cls.root.mkdir()
        cls.loop, cls.server, cls.port = start_server(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.server.close()
        cls.loop.call_soon_threadsafe(cls.loop.stop)
        cls.folder.cleanup()

    def job(self, kind=None, **kwargs):
        from motionstudio.logs.transfer import LogConnection, LogJob
        kind = kind or LogJob
        port = self.port

        class PortClient(paramiko.SSHClient):
            def connect(self, hostname, **kwargs):
                super().connect(hostname, port=port, **kwargs)
        local = Path(tempfile.mkdtemp(dir=self.folder.name))
        # Trust the server up front (as after the user's first "yes"), in a temp file only.
        probe = paramiko.SSHClient()
        probe.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        probe.connect('127.0.0.1', port, 'root', 'robot-password', look_for_keys=False, allow_agent=False)
        key = probe.get_transport().get_remote_server_key()
        probe.close()
        trust = local / 'known_hosts'
        trust.write_text(f'[127.0.0.1]:{port} {key.get_name()} {key.get_base64()}\n')
        if 'destination' in kwargs and kwargs['destination'] is None:
            kwargs['destination'] = local
        job = kind(connection=LogConnection('127.0.0.1', 'root', 'robot-password', '/log_history.txt'),
                   client_factory=PortClient, trust_path=trust, **kwargs)
        self.addCleanup(job.close_fast)
        return job, local

    def test_log_download_uses_asyncssh_with_compression(self):
        data = b'2026-11-19 10:00:00 C153A1 Protective stop\n' * 40000
        (self.root / 'log_history.txt').write_bytes(data)
        job, local = self.job()
        paths, missing_backup = job.download(local / 'log_history.txt')
        self.assertTrue(missing_backup)
        self.assertEqual(job.transfer_engine, 'AsyncSSH')
        self.assertEqual(paths[0].read_bytes(), data)
        self.assertTrue(job.events.empty() or all(kind == 'status' for kind, _ in job.events.queue))

    def test_program_snapshot_downloads_files_in_parallel_with_asyncssh(self):
        from motionstudio.programs.transfer import ProgramSourceJob
        programs = self.root / 'programs'
        expected = {}
        for index in range(30):
            folder = programs / f'cell{index % 4}'
            folder.mkdir(parents=True, exist_ok=True)
            for suffix in ('.urp', '.script'):
                data = os.urandom(1000 + index * 37)
                (folder / f'prog{index}{suffix}').write_bytes(data)
                expected[f'cell{index % 4}/prog{index}{suffix}'] = data
        (programs / 'notes.txt').write_bytes(b'not a program source')
        job, local = self.job(ProgramSourceJob, destination=None)
        job.run()
        events = list(job.events.queue)
        kind, snapshot = events[-1]
        self.assertEqual(kind, 'downloaded', events[-3:])
        self.assertEqual(job.transfer_engine, 'AsyncSSH')
        copied = {path.relative_to(snapshot / 'programs').as_posix(): path.read_bytes()
                  for path in (snapshot / 'programs').rglob('*') if path.is_file()}
        self.assertEqual(copied, expected)

    def test_program_snapshot_failure_stops_other_files_and_reports_the_cause(self):
        from unittest.mock import patch
        from motionstudio.programs.transfer import ProgramSourceJob
        programs = self.root / 'programs'
        programs.mkdir(exist_ok=True)
        for index in range(40):
            (programs / f'big{index:02d}.urp').write_bytes(os.urandom(300000))
        job, _local = self.job(ProgramSourceJob, destination=None)
        original = fast_transfer.FastSFTP.lstat
        def lstat(session, path):
            if path.endswith('big03.urp'):
                raise OSError('simulated robot read error')
            return original(session, path)
        with patch.object(fast_transfer.FastSFTP, 'lstat', lstat):
            job.run()
        kind, message = list(job.events.queue)[-1]
        self.assertEqual(kind, 'error')
        self.assertIn('simulated robot read error', message)
        import shutil
        shutil.rmtree(programs)

    def test_small_file_requests_only_what_it_needs(self):
        data = os.urandom(5000)
        session = _InlineSession()
        self.addCleanup(session.loop.close)
        handle = _CountingHandle(data)
        with fast_transfer.RemoteFile(session, handle, size=len(data)) as remote:
            copy_remote(remote, io.BytesIO(), len(data))
        # Data, short-read refill check, EOF probe, final EOF check: 4, not 256.
        self.assertLessEqual(handle.requests, 4)

    def test_connect_attempt_can_be_cancelled(self):
        import socket
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen()  # accepts TCP but never speaks SSH
        self.addCleanup(listener.close)
        stop = threading.Event()
        threading.Timer(.3, stop.set).start()
        started = time.monotonic()
        with self.assertRaises(InterruptedError):
            fast_transfer.FastSFTP.connect('127.0.0.1', 'root', 'x', paramiko.RSAKey.generate(1024),
                                           port=listener.getsockname()[1], stop=stop)
        self.assertLess(time.monotonic() - started, 5)

    def test_falls_back_to_paramiko_when_asyncssh_cannot_connect(self):
        data = os.urandom(200000)
        (self.root / 'log_history.txt').write_bytes(data)
        job, local = self.job()
        from unittest.mock import patch
        with patch.object(fast_transfer.FastSFTP, 'connect', side_effect=OSError('refused with robot-password')):
            paths, _ = job.download(local / 'log_history.txt')
        self.assertEqual(paths[0].read_bytes(), data)
        self.assertIn('paramiko', job.transfer_engine)
        self.assertNotIn('robot-password', job.transfer_engine)


class _ShortReadHandle:
    """A server that answers at most 10,000 bytes per request, as SFTP allows."""
    def __init__(self, data):
        self.data = data

    async def read(self, size, offset):
        return self.data[offset:offset + min(size, 10000)]

    async def close(self):
        pass


class _CountingHandle(_ShortReadHandle):
    requests = 0

    async def read(self, size, offset):
        self.requests += 1
        return self.data[offset:offset + size]


class _InlineSession:
    def __init__(self):
        self.loop = asyncio.new_event_loop()

    def submit(self, awaitable):
        from concurrent.futures import Future
        future = Future()
        future.set_result(self.loop.run_until_complete(awaitable))
        return future

    def wait(self, future, timeout=None):
        return future.result()


class ShortReadTests(unittest.TestCase):
    def test_short_server_reads_are_refilled_not_treated_as_end_of_file(self):
        data = os.urandom(300000)
        session = _InlineSession()
        self.addCleanup(session.loop.close)
        local = io.BytesIO()
        with fast_transfer.RemoteFile(session, _ShortReadHandle(data)) as remote:
            self.assertEqual(copy_remote(remote, local, len(data)), len(data))
        self.assertEqual(local.getvalue(), data)


if __name__ == '__main__':
    unittest.main()
