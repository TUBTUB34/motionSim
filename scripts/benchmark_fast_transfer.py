"""Paramiko vs AsyncSSH bulk download speed over SIMULATED links; never connects to a robot.

Run: .venv/bin/python scripts/benchmark_fast_transfer.py [bytes]   (default 16 MiB)

A local AsyncSSH SFTP server and a delaying/bandwidth-limiting TCP proxy run in
separate processes, so the measured client has its own interpreter. The server
is Python, not the robot's OpenSSH, so loopback speed is server-bound; the
latency and slow-link rows show the effect of request depth, window size and
zlib. These are not robot measurements.
"""
import asyncio, io, os, sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paramiko
from tests.test_fast_transfer import start_server
from motionstudio.remote import fast_transfer
from motionstudio.remote.stream import copy_remote


def start_proxy(target_port, one_way_delay, rate_bytes):
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    async def pipe(reader, writer):
        queue = asyncio.Queue()
        async def feed():
            while data := await reader.read(65536):
                await queue.put((loop.time() + one_way_delay, data))
            await queue.put((None, None))
        asyncio.ensure_future(feed())
        free_at = loop.time()
        while True:
            due, data = await queue.get()
            if data is None:
                break
            if rate_bytes:
                free_at = max(free_at, due) + len(data) / rate_bytes
                due = free_at
            delay = due - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            writer.write(data)
            await writer.drain()
        writer.close()

    async def handle(reader, writer):
        up_reader, up_writer = await asyncio.open_connection('127.0.0.1', target_port)
        asyncio.ensure_future(pipe(reader, up_writer))
        asyncio.ensure_future(pipe(up_reader, writer))

    server = asyncio.run_coroutine_threadsafe(asyncio.start_server(handle, '127.0.0.1', 0), loop).result()
    return server.sockets[0].getsockname()[1]


def child(kind, *args):
    """Run the server or a proxy in its own process so the measured client has its own GIL."""
    import subprocess
    process = subprocess.Popen([sys.executable, __file__, kind, *map(str, args)], stdout=subprocess.PIPE, text=True)
    return process, int(process.stdout.readline())


def main():
    root = Path(tempfile.mkdtemp())
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 16 * 1024 * 1024
    (root / 'random.bin').write_bytes(os.urandom(size))
    (root / 'log.txt').write_bytes((b'2026-11-19 10:00:00 C153A1 Protective stop joint 3 current\n' * (size // 58 + 1))[:size])
    _, port = child('server', root)
    cases = [('loopback (no proxy)', 0, 0), ('20 ms RTT', .01, 0), ('100 ms RTT', .05, 0),
             ('100 ms RTT, 2 MB/s link', .05, 2_000_000)]
    print(f'file {size / 1048576:.0f} MiB; loopback asyncssh server; proxy adds delay/bandwidth limit')
    print(f'{"link":26} {"file":7} {"paramiko":>10} {"asyncssh":>10} {"asyncssh+zlib":>14}')
    for name, delay, rate in cases:
        proxy = child('proxy', port, delay, rate)[1] if delay > .001 else port
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect('127.0.0.1', proxy, 'root', 'robot-password', look_for_keys=False, allow_agent=False)
        key = client.get_transport().get_remote_server_key()
        sftp = client.open_sftp()
        for label in ('random.bin', 'log.txt'):
            row = []
            t = time.perf_counter()
            with sftp.open('/' + label, 'rb') as remote:
                copy_remote(remote, io.BytesIO(), size)
            row.append(time.perf_counter() - t)
            for compress in (False, True):
                session = fast_transfer.FastSFTP.connect('127.0.0.1', 'root', 'robot-password', key, port=proxy, compress=compress)
                t = time.perf_counter()
                with session.open('/' + label) as remote:
                    copy_remote(remote, io.BytesIO(), size)
                row.append(time.perf_counter() - t)
                session.close()
            print(f'{name:26} {label[:7]:7} ' + ' '.join(f'{size / s / 1048576:8.1f} MiB/s'[0:14].rjust(w)
                                                     for s, w in zip(row, (10, 10, 14))), flush=True)
        client.close()


if sys.argv[1:2] == ['server']:
    print(start_server(Path(sys.argv[2]))[2], flush=True); threading.Event().wait()
elif sys.argv[1:2] == ['proxy']:
    print(start_proxy(int(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])), flush=True); threading.Event().wait()
else:
    main()
