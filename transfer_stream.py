"""Bounded SFTP pipelining shared by robot downloads and backups.

Paramiko caps each protocol read at 32 KiB. Increasing read()'s argument alone
still waits once per request. Small readv windows overlap those waits without
queueing offsets (or retaining data) for an entire multi-gigabyte archive.
"""

READ_SIZE = 32 * 1024
PIPELINE_REQUESTS = 32
DOWNLOAD_IDLE_TIMEOUT = 60
STREAM_BUFFER_SIZE = 256 * 1024


def copy_remote(remote, local, size, *, check_cancel=lambda: None,
                progress=None, pipeline_requests=PIPELINE_REQUESTS,
                allow_growth=False, max_bytes=None):
    """Copy exactly ``size`` bytes, checking EOF and cancellation.

``remote`` is an open binary SFTP file positioned at zero; ``local`` is an
open binary output file. The caller owns closing, fsync, atomic replacement,
remote metadata verification and retries. Progress receives cumulative bytes.
At most ``pipeline_requests`` 32 KiB requests are queued at a time. Streams
without readv (e.g. ordinary local files) use sequential bounded reads.
Live logs may set ``allow_growth`` with an explicit ``max_bytes`` ceiling to
include bytes appended after stat; archives and recordings retain exact-size
verification. A shortened initial snapshot is always an error.
    """
    if size < 0:
        raise ValueError('Expected file size cannot be negative.')
    if not 1 <= pipeline_requests <= 32:
        raise ValueError('Pipeline request limit must be between 1 and 32.')
    if allow_growth and (max_bytes is None or max_bytes < size):
        raise ValueError('Growing downloads require a size ceiling at least as large as the snapshot.')
    total = 0
    readv = getattr(remote, 'readv', None)
    while total < size:
        check_cancel()
        chunks = [(offset, min(READ_SIZE, size - offset))
                  for offset in range(total, min(size, total + READ_SIZE * pipeline_requests), READ_SIZE)]
        if callable(readv):
            blocks = iter(readv(chunks, max_concurrent_prefetch_requests=pipeline_requests))
        else:
            blocks = (remote.read(length) for _, length in chunks)
        for _, length in chunks:
            check_cancel()
            try:
                block = next(blocks)
            except StopIteration:
                raise OSError('Incomplete download; remote file ended early.') from None
            check_cancel()
            if len(block) != length:
                raise OSError('Incomplete download; remote file size changed.')
            written = local.write(block)
            if written != len(block):
                raise OSError('Incomplete local write; download was not saved.')
            total += len(block)
            if progress:
                progress(total)
        # Exhaust Paramiko's readv generator before starting its next window.
        try:
            next(blocks)
        except StopIteration:
            pass
        else:
            raise OSError('Unexpected extra download block.')
    check_cancel()
    if allow_growth:
        while True:
            block = remote.read(READ_SIZE)
            check_cancel()
            if not block:
                break
            if total + len(block) > max_bytes:
                raise ValueError(f'Download exceeds size limit ({max_bytes} bytes).')
            if local.write(block) != len(block):
                raise OSError('Incomplete local write; download was not saved.')
            total += len(block)
            if progress:
                progress(total)
            check_cancel()
    elif remote.read(1):
        raise OSError('Remote file changed during download. Please retry.')
    check_cancel()
    return total
