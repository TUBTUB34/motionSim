"""Offline integrity and bounded-memory checks for the shared download path."""
import io
import queue
import threading
import unittest

import paramiko
from paramiko.sftp import CMD_CLOSE, CMD_DATA, CMD_READ

from transfer_stream import PIPELINE_REQUESTS, READ_SIZE, copy_remote


class PipelinedFile(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.windows = []

    def readv(self, chunks, max_concurrent_prefetch_requests):
        self.windows.append((list(chunks), max_concurrent_prefetch_requests))
        for offset, length in chunks:
            self.seek(offset)
            yield self.read(length)


class MemorySFTPProtocol:
    """Exercise real Paramiko SFTPFile/readv with in-memory protocol replies."""
    def __init__(self, content):
        self.content = content
        self.responses = queue.Queue()
        self.threads = set()
        self.number = 0
        self.peak_pending = 0

    def _log(self, *args):
        pass

    def _async_request(self, file, command, handle, offset=0, length=0):
        if command == CMD_CLOSE:
            return 0
        assert command == CMD_READ
        self.threads.add(threading.current_thread())
        self.number += 1
        number = self.number
        self.responses.put((file, number, offset, length))
        self.peak_pending = max(self.peak_pending, self.responses.qsize())
        return number

    def _read_response(self):
        file, number, offset, length = self.responses.get(timeout=2)
        message = paramiko.Message()
        message.add_string(self.content[offset:offset + length])
        file._async_response(CMD_DATA, paramiko.Message(message.asbytes()), number)

    def _request(self, command, handle, offset=0, length=0):
        if command == CMD_CLOSE:
            return None, None
        assert command == CMD_READ
        if offset >= len(self.content):
            raise EOFError()
        message = paramiko.Message()
        message.add_string(self.content[offset:offset + length])
        return CMD_DATA, paramiko.Message(message.asbytes())


class TransferStreamTests(unittest.TestCase):
    def test_actual_paramiko_readv_finishes_bounded_prefetch_threads(self):
        content = bytes(range(251)) * 7001
        protocol = MemorySFTPProtocol(content)
        local = io.BytesIO()
        with paramiko.SFTPFile(protocol, b'handle', 'rb') as remote:
            copy_remote(remote, local, len(content))
        self.assertEqual(local.getvalue(), content)
        self.assertLessEqual(protocol.peak_pending, PIPELINE_REQUESTS)
        self.assertTrue(protocol.responses.empty())
        for thread in protocol.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_actual_paramiko_cancel_does_not_leave_prefetch_worker_running(self):
        content = b'x' * (READ_SIZE * PIPELINE_REQUESTS * 3)
        protocol = MemorySFTPProtocol(content)
        local = io.BytesIO()
        def check():
            if local.tell():
                raise InterruptedError('Cancelled')
        with paramiko.SFTPFile(protocol, b'handle', 'rb') as remote:
            with self.assertRaises(InterruptedError):
                copy_remote(remote, local, len(content), check_cancel=check)
        self.assertEqual(local.tell(), READ_SIZE)
        self.assertLessEqual(protocol.number, PIPELINE_REQUESTS)
        for thread in protocol.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_pipeline_bounds_requests_and_preserves_non_aligned_content(self):
        content = bytes(range(251)) * 12001
        remote, local, progress = PipelinedFile(content), io.BytesIO(), []
        self.assertEqual(copy_remote(remote, local, len(content), progress=progress.append), len(content))
        self.assertEqual(local.getvalue(), content)
        self.assertEqual(progress[-1], len(content))
        self.assertEqual(progress, sorted(set(progress)))
        self.assertGreater(len(remote.windows), 1)
        for chunks, limit in remote.windows:
            self.assertEqual(limit, PIPELINE_REQUESTS)
            self.assertLessEqual(len(chunks), limit)
            self.assertTrue(all(length <= READ_SIZE for _, length in chunks))

    def test_sequential_fallback_and_empty_file(self):
        for content in (b'', b'a' * (READ_SIZE + 71)):
            with self.subTest(size=len(content)):
                local = io.BytesIO()
                copy_remote(io.BytesIO(content), local, len(content))
                self.assertEqual(local.getvalue(), content)

    def test_truncated_and_growing_files_rejected_for_both_paths(self):
        for remote_type in (io.BytesIO, PipelinedFile):
            for actual, expected in ((20, 21), (21, 20), (1, 0)):
                with self.subTest(remote=remote_type.__name__, actual=actual):
                    with self.assertRaises(OSError):
                        copy_remote(remote_type(b'x' * actual), io.BytesIO(), expected)

    def test_live_log_includes_appended_bytes_with_bounded_tail(self):
        content = bytes(range(251)) * 1001
        for remote_type in (io.BytesIO, PipelinedFile):
            with self.subTest(remote=remote_type.__name__):
                local, progress = io.BytesIO(), []
                count = copy_remote(remote_type(content), local, len(content)-43,
                                    allow_growth=True, max_bytes=len(content), progress=progress.append)
                self.assertEqual(count, len(content))
                self.assertEqual(local.getvalue(), content)
                self.assertEqual(progress[-1], len(content))

    def test_live_log_growth_limit_and_truncation_remain_errors(self):
        for remote_type in (io.BytesIO, PipelinedFile):
            with self.subTest(remote=remote_type.__name__):
                local = io.BytesIO()
                with self.assertRaisesRegex(ValueError, 'size limit'):
                    copy_remote(remote_type(b'x' * 21), local, 10, allow_growth=True, max_bytes=20)
                self.assertEqual(local.getvalue(), b'x' * 10)
                with self.assertRaises(OSError):
                    copy_remote(remote_type(b'x' * 9), io.BytesIO(), 10, allow_growth=True, max_bytes=20)
        with self.assertRaises(ValueError):
            copy_remote(io.BytesIO(), io.BytesIO(), 0, allow_growth=True)

    def test_actual_paramiko_live_tail_retains_every_byte(self):
        content = bytes(range(251)) * 6001
        protocol, local = MemorySFTPProtocol(content), io.BytesIO()
        with paramiko.SFTPFile(protocol, b'handle', 'rb') as remote:
            copy_remote(remote, local, len(content)-41, allow_growth=True, max_bytes=len(content))
        self.assertEqual(local.getvalue(), content)
        self.assertLessEqual(protocol.peak_pending, PIPELINE_REQUESTS)
        for thread in protocol.threads:
            thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_cancel_after_first_chunk_queues_no_more_windows(self):
        remote = PipelinedFile(b'x' * (READ_SIZE * PIPELINE_REQUESTS * 3))
        local = io.BytesIO()
        def check():
            if local.tell():
                raise InterruptedError('Cancelled')
        with self.assertRaises(InterruptedError):
            copy_remote(remote, local, READ_SIZE * PIPELINE_REQUESTS * 3, check_cancel=check)
        self.assertEqual(len(local.getvalue()), READ_SIZE)
        self.assertEqual(len(remote.windows), 1)

    def test_cancel_during_read_does_not_write_chunk(self):
        cancelled = False
        class InterruptedRead(io.BytesIO):
            def read(self, length):
                nonlocal cancelled
                cancelled = True
                return super().read(length)
        def check():
            if cancelled:
                raise InterruptedError('Cancelled')
        local = io.BytesIO()
        with self.assertRaises(InterruptedError):
            copy_remote(InterruptedRead(b'abc'), local, 3, check_cancel=check)
        self.assertEqual(local.getvalue(), b'')

    def test_disk_full_short_write_and_timeout_propagate(self):
        class DiskFull(io.BytesIO):
            def write(self, data):
                raise OSError(28, 'No space left on device')
        class ShortWrite(io.BytesIO):
            def write(self, data):
                return 0
        class Timeout(io.BytesIO):
            def read(self, length):
                raise TimeoutError('stalled')
        for local in (DiskFull(), ShortWrite()):
            with self.subTest(output=type(local).__name__), self.assertRaises(OSError):
                copy_remote(io.BytesIO(b'abc'), local, 3)
        with self.assertRaises(TimeoutError):
            copy_remote(Timeout(b'abc'), io.BytesIO(), 3)

    def test_invalid_bounds_rejected(self):
        for limit in (0, 33):
            with self.assertRaises(ValueError):
                copy_remote(io.BytesIO(), io.BytesIO(), 0, pipeline_requests=limit)
        with self.assertRaises(ValueError):
            copy_remote(io.BytesIO(), io.BytesIO(), -1)
