"""Faster bulk SFTP reads with AsyncSSH, behind the existing ``copy_remote`` contract.

Paramiko still opens, authenticates and verifies every robot connection. This
module opens a second connection only after that, pinned to the exact host key
paramiko verified, with password authentication only (no key files, no agent).
The adapter below keeps 256 block reads (8 MiB) in flight ahead of the
reader, so the link never idles between windows the way 32 x 32 KiB
paramiko readv windows do on high-latency links.

``RemoteFile`` looks like the paramiko file ``copy_remote`` expects, so the
exact-size, growth-ceiling, remote-changed and cancellation checks are
unchanged. Callers fall back to paramiko when AsyncSSH is unavailable.
"""
import asyncio
from collections import deque
import concurrent.futures
import errno
import threading
import time
from types import SimpleNamespace

from transfer_stream import DOWNLOAD_IDLE_TIMEOUT, SSH_TIMEOUT

try:
    import asyncssh
except ImportError:  # pragma: no cover - the paramiko path still works
    asyncssh = None

# 32 KiB is the request size every SFTP server accepts (older robot OpenSSH
# builds cap larger ones); depth, not request size, is what fills a slow link.
BLOCK_SIZE = 32 * 1024
IN_FLIGHT = 256  # 8 MiB requested ahead of the reader
WINDOW = 16 * 1024 * 1024  # SSH flow-control window; 2 MiB caps a 100 ms link near 20 MiB/s
COMPRESSION = ['zlib@openssh.com', 'zlib', 'none']


def available():
    return asyncssh is not None


def _run_loop(loop):
    try:
        loop.run_forever()
    finally:
        loop.close()


def _stop_loop(loop):
    """Cancel everything still pending (so waiting workers return), then stop; the loop thread closes it."""
    for task in asyncio.all_tasks(loop):
        task.cancel()
    loop.call_later(.5, loop.stop)


class FastSFTP:
    """One AsyncSSH SFTP session on a private event loop thread; methods are for one worker thread."""

    def __init__(self, loop, thread, connection, sftp):
        self._loop, self._thread = loop, thread
        self._connection, self._sftp = connection, sftp
        self._closed = False
        self._lock = threading.Lock()

    @classmethod
    def connect(cls, host, username, password, host_key, *, port=22, compress=False, timeout=SSH_TIMEOUT,
                stop=None):
        """Open a session that trusts only ``host_key`` (a verified paramiko PKey).

``stop`` (a threading.Event) abandons a slow connection attempt promptly.
"""
        if asyncssh is None:
            raise RuntimeError('AsyncSSH is not installed.')
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=_run_loop, args=(loop,), name='fast-sftp', daemon=True)
        thread.start()
        # Trust exactly this key, whatever name or port the host is reached by.
        known = ([asyncssh.import_public_key(f'{host_key.get_name()} {host_key.get_base64()}')], [], [])

        async def open_session():
            connection = await asyncssh.connect(
                host, port, username=username, password=password, known_hosts=known,
                client_keys=None, agent_path=None, preferred_auth='password,keyboard-interactive',
                compression_algs=COMPRESSION if compress else ['none'],
                connect_timeout=timeout, login_timeout=timeout, window=WINDOW,
                # Notice a dead link within about a minute without limiting slow transfers.
                keepalive_interval=15, keepalive_count_max=4)
            try:
                return connection, await connection.start_sftp_client()
            except BaseException:
                connection.close()
                raise
        future = asyncio.run_coroutine_threadsafe(open_session(), loop)
        deadline = time.monotonic() + timeout * 3
        try:
            while True:
                try:
                    connection, sftp = future.result(.1)
                    break
                except concurrent.futures.TimeoutError:
                    if stop is not None and stop.is_set():
                        raise InterruptedError('Download cancelled.') from None
                    if time.monotonic() > deadline:
                        raise TimeoutError('AsyncSSH connection timed out.') from None
        except BaseException:
            future.cancel()
            loop.call_soon_threadsafe(_stop_loop, loop)
            raise
        return cls(loop, thread, connection, sftp)

    def submit(self, awaitable):
        async def run():
            return await awaitable
        # The lock makes "not closed yet" and "scheduled" one step, so nothing
        # is queued on a loop that close() is already stopping.
        with self._lock:
            if not self._closed:
                return asyncio.run_coroutine_threadsafe(run(), self._loop)
        getattr(awaitable, 'close', lambda: None)()
        raise ConnectionError('Fast transfer connection closed.')

    def wait(self, future, timeout=DOWNLOAD_IDLE_TIMEOUT):
        try:
            return future.result(timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise TimeoutError(f'Download stalled: no data received for {timeout} seconds.') from None
        except concurrent.futures.CancelledError:
            raise ConnectionError('Fast transfer connection closed.') from None
        except asyncssh.SFTPConnectionLost as error:
            raise ConnectionError(f'Robot connection lost: {error.reason or type(error).__name__}') from None
        except asyncssh.SFTPNoSuchFile as error:
            raise FileNotFoundError(errno.ENOENT, error.reason or 'No such file') from None
        except asyncssh.SFTPError as error:
            raise OSError(f'Robot SFTP error: {error.reason or type(error).__name__}') from None
        except asyncssh.Error as error:
            raise ConnectionError(f'Robot connection lost: {error.reason or type(error).__name__}') from None

    def lstat(self, path):
        """Attributes named like paramiko's: st_mode, st_size, st_mtime."""
        attrs = self.wait(self.submit(self._sftp.lstat(path)))
        return SimpleNamespace(st_mode=attrs.permissions, st_size=attrs.size, st_mtime=attrs.mtime)

    def open(self, path, size=None):
        """``size`` (when known) limits read-ahead, so small files do not request 8 MiB past their end."""
        return RemoteFile(self, self.wait(self.submit(
            self._sftp.open(path, 'rb', block_size=0))), size)

    def close(self):
        """Safe from any thread; pending reads are cancelled so blocked workers return."""
        with self._lock:
            if self._closed:
                return
            self._closed = True

        def shutdown():
            self._connection.close()
            _stop_loop(self._loop)
        try:
            self._loop.call_soon_threadsafe(shutdown)
        except RuntimeError:
            pass


class RemoteFile:
    """Sequential reader with a FIFO of parallel block reads; supports the calls ``copy_remote`` makes.

Each wait covers one 32 KiB block, so the idle timeout still means "no data
arrived", even on links far slower than the data in flight.
"""

    def __init__(self, session, handle, size=None):
        self._session, self._handle = session, handle
        # Enough requests to cover the expected size plus one EOF probe, at most IN_FLIGHT.
        self._depth = IN_FLIGHT if size is None else max(1, min(IN_FLIGHT, -(-size // BLOCK_SIZE) + 1))
        self._pending = deque()
        self._buffer = bytearray()
        self._position = self._requested = 0
        self._ended = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

    def _cancel(self):
        while self._pending:
            self._pending.popleft()[1].cancel()

    def close(self):
        self._cancel()
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                self._session.wait(self._session.submit(handle.close()), timeout=SSH_TIMEOUT)
            except (OSError, ConnectionError, TimeoutError):
                pass

    def _read_at(self, offset, length):
        if offset != self._position:
            self._cancel()
            self._buffer.clear()
            self._position = self._requested = offset
            self._ended = False
        if self._ended and not self._buffer:
            # Past the end seen so far: a growing log may have more now.
            block = self._session.wait(self._session.submit(self._handle.read(length, offset)))
            self._position += len(block)
            self._requested = self._position
            return block
        while len(self._buffer) < length and not self._ended:
            while len(self._pending) < self._depth:
                self._pending.append((self._requested, self._session.submit(
                    self._handle.read(BLOCK_SIZE, self._requested))))
                self._requested += BLOCK_SIZE
            offset, future = self._pending.popleft()
            data = self._session.wait(future)
            # A server may answer with fewer bytes than asked without being at
            # the end; only an empty reply means end of file.
            while data and len(data) < BLOCK_SIZE:
                more = self._session.wait(self._session.submit(
                    self._handle.read(BLOCK_SIZE - len(data), offset + len(data))))
                if not more:
                    break
                data += more
            self._buffer += data
            if len(data) < BLOCK_SIZE:
                self._ended = True
                self._cancel()
                self._requested = self._position + len(self._buffer)
        block = bytes(self._buffer[:length])
        del self._buffer[:length]
        self._position += len(block)
        return block

    def readv(self, chunks, max_concurrent_prefetch_requests=None):
        for offset, length in chunks:
            yield self._read_at(offset, length)

    def read(self, size):
        return self._read_at(self._position, size)
