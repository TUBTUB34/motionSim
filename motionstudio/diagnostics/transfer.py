"""Bounded diagnostic snapshots using the verified robot transport.

Files the project already holds (a backup tar, the Flight reports library, an
earlier Robot Status dataset or the Logs folder) are copied locally instead of
downloaded again, after their size, time and content are checked against the robot.
When nothing local matches, one tar of the logs and recordings is made on the
robot and saved to the project's Backups. Successful source files survive failures
and cancellation. The manifest describes coverage and where every file came from;
robot files are never changed (a tar lives only in a private /tmp folder until it
is saved) and no credentials are persisted.
"""
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tarfile
import tempfile
import time
from uuid import uuid4
import zipfile

from motionstudio.backup.transfer import RobotArchive
from motionstudio.core.paths import PROJECT_FOLDERS
from motionstudio.flight.data import MAX_TELEMETRY_BYTES, TELEMETRY_MEMBER, load_flight
from motionstudio.flight.transfer import DEFAULT_FLIGHT_DIRECTORY, FlightTransfer, _local_path, _manifest
from motionstudio.logs.data import MAX_LOG_BYTES
from motionstudio.remote.stream import copy_remote

TAIL_BYTES = 64 * 1024  # a saved log is reused only if it ends with the robot's last bytes
MAX_ARCHIVES = 20  # newest backup tars searched for reusable files


def _tar_members(path):
    """Regular files of a robot backup tar by absolute robot path (tar -P keeps it)."""
    with tarfile.open(path, 'r:') as archive:
        return {'/' + member.name.lstrip('/'): member for member in archive.getmembers() if member.isfile()}


class DiagnosticSourceJob(RobotArchive, FlightTransfer):
    """One-shot worker; emits collected/cancelled/error with a result dictionary.

The result contains path, warnings, complete, downloaded_files, reused_files and
message. Host-key questions use the normal application host_key event. The
destination must exist; every run creates a separate dataset rather than replacing
old data. ``project`` (a project folder) enables reuse of files already saved there
and, when none match, the single-tar download saved as ``Backups/archive_name``.
"""
    def __init__(self, *, connection, destination,
                 flight_directory=DEFAULT_FLIGHT_DIRECTORY, max_files=500,
                 max_total_bytes=2 * 1024 ** 3, max_listing_entries=10000,
                 project=None, archive_name='robot-status.tar', **kwargs):
        self.destination = Path(destination)
        if not self.destination.is_dir():
            raise ValueError('Choose an existing folder for the diagnostic dataset.')
        if min(max_files, max_total_bytes, max_listing_entries) <= 0:
            raise ValueError('Diagnostic collection limits must be positive.')
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_listing_entries = max_listing_entries
        self.snapshot_path = None
        self.records = []
        self.warnings = []
        self.downloaded_files = 0
        self.downloaded_bytes = 0
        self.reused_files = 0
        self.project = Path(project) if project is not None else None
        self.archive_name = archive_name
        self.saved_archive = None
        self.remote_path = None
        self._archives = None
        super().__init__(connection, library=self.destination,
                         directory=flight_directory, **kwargs)

    def _warn(self, message):
        message = self._error(message)
        if message not in self.warnings:
            self.warnings.append(message)
        self.events.put(('status', message))

    def _save_diagnostic_manifest(self, status):
        if self.snapshot_path is None:
            return
        data = dict(schema_version=2, status=status,
                    collected_at=datetime.now(timezone.utc).isoformat(),
                    serial=self.serial, robot_id=self.robot_id, fingerprint=self.fingerprint,
                    warnings=self.warnings, files=self.records,
                    archive=str(self.saved_archive) if self.saved_archive else None,
                    limits=dict(files=self.max_files, total_bytes=self.max_total_bytes,
                                listing_entries=self.max_listing_entries))
        # This must also work after cancellation, without discarding good files.
        target = self.snapshot_path / 'diagnostic_manifest.json'
        temporary = target.with_suffix('.json.part')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def _progress(self, name):
        last = 0.
        def update(count):
            nonlocal last
            now = time.monotonic()
            if now - last >= .2:
                self.events.put(('status', f'Downloading {name} · {count / 1048576:.2f} MiB · {self.engine_label}'))
                last = now
        return update

    def _copy_source(self, remote_path, kind, local_name):
        self._ensure_connection()
        self.check_cancel()
        before = self.sftp.stat(remote_path)
        if not stat.S_ISREG(before.st_mode or 0):
            raise ValueError('Source is not a regular file.')
        size = int(before.st_size)
        ceiling = MAX_LOG_BYTES if kind == 'log' else MAX_TELEMETRY_BYTES
        if not 0 <= size <= ceiling:
            raise ValueError(f'Source exceeds the {ceiling // 1048576} MiB file limit.')
        remaining = self.max_total_bytes - self.downloaded_bytes
        if size > remaining:
            raise ValueError('Total diagnostic download size limit reached; coverage is incomplete.')
        directory = self.snapshot_path / ('logs' if kind == 'log' else 'flights')
        directory.mkdir(exist_ok=True)
        if shutil.disk_usage(directory).free < size + 1024 * 1024:
            raise OSError(errno.ENOSPC, 'Not enough free space for this source.')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, prefix='.source-',
                    suffix='.part' + Path(local_name).suffix, delete=False) as local:
                temporary = Path(local.name)
                with self.open_bulk(self.sftp, remote_path, compress=kind == 'log', size=size) as remote:
                    copied = copy_remote(remote, local, size, check_cancel=self.check_cancel,
                            progress=self._progress(PurePosixPath(remote_path).name),
                            allow_growth=kind == 'log', max_bytes=min(ceiling, remaining))
                local.flush()
                os.fsync(local.fileno())
            self.check_cancel()
            if kind == 'flight':
                after = self.sftp.stat(remote_path)
                if (after.st_size, after.st_mtime) != (before.st_size, before.st_mtime):
                    raise ValueError('Flight report changed during download; retrieve it again.')
                if temporary.suffix.lower() == '.zip':
                    self._validate(temporary)
                else:
                    load_flight(temporary)
                self.check_cancel()
            if copied == 0:
                raise ValueError('Source is empty and contains no diagnostic evidence.')
            digest = self._hash(temporary)
            target = directory / local_name
            self.check_cancel()
            os.replace(temporary, target)
            return dict(path=target.relative_to(self.snapshot_path).as_posix(),
                        size=copied, mtime=before.st_mtime, sha256=digest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _collect_source(self, remote_path, kind, local_name):
        self.check_cancel()
        record = dict(kind=kind, remote_path=remote_path, path=None, status='pending')
        self.records.append(record)
        if self.downloaded_files >= self.max_files:
            record.update(status='skipped', error='Diagnostic file limit reached.')
            self._warn('Diagnostic file limit reached; remaining sources were not retrieved.')
            return
        try:
            result = self._attempt(lambda: self._copy_source(remote_path, kind, local_name))
            record.update(result, status='downloaded')
            self.downloaded_files += 1
            self.downloaded_bytes += result['size']
        except InterruptedError:
            record.update(status='cancelled', error='Collection cancelled before this source completed.')
            raise
        except Exception as error:
            self.check_cancel()
            missing = isinstance(error, OSError) and error.errno == errno.ENOENT
            message = self._error(error)
            record.update(status='missing' if missing else 'failed', error=message)
            self._warn(f'{kind.title()} {PurePosixPath(remote_path).name}: {message}')
        finally:
            self._save_diagnostic_manifest('collecting')

    def _list_flights(self):
        """Recordings to collect as (remote path, local name, size, mtime).

        The whole listing is read before any download: interleaving downloads with
        paramiko's listdir_iter read-aheads mixes up their replies on one channel.
        """
        self._ensure_connection()
        self.events.put(('status', 'Finding flight recordings…'))
        entries = self.sftp.listdir_attr(self.directory)
        if len(entries) > self.max_listing_entries:
            self._warn('Flight directory scan limit reached; historical coverage is incomplete.')
            entries = entries[:self.max_listing_entries]
        found = []
        for attr in entries:
            self.check_cancel()
            name = attr.filename
            if (not isinstance(name, str) or name in ('.', '..') or
                    PurePosixPath(name).name != name or '\\' in name or
                    any(c in name for c in '\x00\r\n') or
                    not name.lower().endswith(('.zip', '.csv')) or
                    name.lower().startswith('robot_configuration') or
                    not stat.S_ISREG(attr.st_mode or 0)):
                continue
            remote_path = str(PurePosixPath(self.directory) / name)
            safe = re.sub(r'[^A-Za-z0-9_.-]', '_', Path(name).stem)[:100]
            suffix = hashlib.sha256(remote_path.encode()).hexdigest()[:12]
            found.append((remote_path, f'flight-{safe}-{suffix}{Path(name).suffix.lower()}',
                          int(attr.st_size or 0), int(attr.st_mtime or 0)))
        if not found:
            self._warn('No supported flight recordings were found; flight evidence is unavailable.')
        return found

    def _collect_flights(self, flights):
        for remote_path, local_name, _size, _mtime in flights:
            if self._reused(remote_path):
                continue
            self._collect_source(remote_path, 'flight', local_name)
            if self.downloaded_files + self.reused_files >= self.max_files:
                # Explicit even if the last listed item happened to be final:
                # the remainder has not been inspected, so coverage is unknown.
                self._warn('Diagnostic file limit reached; remaining flight coverage was not verified.')
                break

    # ---- Reuse of files the project already holds -------------------------------------

    def _reused(self, remote_path):
        return any(r['remote_path'] == remote_path and r['status'] == 'reused' for r in self.records)

    def _archives_in(self, folder):
        """Newest backup tars first, indexed lazily; unreadable files are skipped."""
        try:
            paths = sorted((p for p in folder.glob('*.tar') if p.is_file() and not p.is_symlink()),
                           key=lambda p: p.stat().st_mtime, reverse=True)[:MAX_ARCHIVES]
        except OSError:
            return []
        return paths

    def _archive_index(self):
        """Indexed tars, newest first, opened one at a time only as far as lookups need.

        Each tar is read (headers only) once; with OneDrive files-on-demand an older
        backup is never fetched while a newer one already holds every wanted file.
        """
        if self._archives is None:
            self._archives = []
            self._pending_archives = (self._archives_in(self.project / PROJECT_FOLDERS['backups'])
                                      if self.project is not None else [])
        yield from list(self._archives)
        while self._pending_archives:
            path = self._pending_archives.pop(0)
            self.check_cancel()
            try:
                entry = (path, _tar_members(path))
            except (OSError, tarfile.TarError, EOFError):
                continue
            self._archives.append(entry)
            yield entry

    def _earlier_datasets(self):
        """Manifests of earlier Robot Status runs for this robot (same key and serial)."""
        roots = {self.destination}
        if self.project is not None:
            folder = self.project / PROJECT_FOLDERS['status']
            roots.update(p for p in (folder.glob('*') if folder.is_dir() else []) if p.is_dir())
        found = []
        for root in roots:
            for manifest in root.glob('robot-diagnostics-*/diagnostic_manifest.json'):
                if manifest.parent == self.snapshot_path:
                    continue
                try:
                    data = json.loads(manifest.read_text(encoding='utf-8'))
                except (OSError, ValueError):
                    continue
                if (isinstance(data, dict) and data.get('fingerprint') == self.fingerprint
                        and data.get('serial', '') == self.serial):
                    found.append((manifest.parent, data.get('files') or []))
        return found

    def _remote_tail(self, remote_path, size):
        count = min(size, TAIL_BYTES)
        with self.sftp.open(remote_path, 'rb') as stream:
            stream.seek(size - count)
            return stream.read(count)

    def _candidates(self, remote_path, kind, size, mtime):
        """Local copies in the user's order: backup tars, then saved files.

        Yields (description, opener, expected sha256 or None); an opener returns a
        binary stream. A candidate is only offered if size and time (or, for a saved
        log without a recorded time, its final bytes) agree with the robot.
        """
        for path, members in self._archive_index():
            member = members.get(remote_path)
            if member is not None and member.size == size and int(member.mtime) == int(mtime):
                def opener(path=path, member=member):
                    archive = tarfile.open(path, 'r:')
                    stream = archive.extractfile(member)
                    stream.close_archive = archive.close
                    return stream
                yield f'backup {path.name}', opener, None
        if self.project is not None and kind == 'flight' and self.robot_id:
            library = self.project / PROJECT_FOLDERS['flights'] / self.robot_id
            record = _manifest(library)['files'].get(remote_path, {})
            local = _local_path(library, record)
            if (local is not None and record.get('fingerprint') == self.fingerprint
                    and record.get('size') == size and record.get('mtime') == mtime):
                yield 'Flight reports library', lambda local=local: local.open('rb'), record.get('sha256')
        for folder, records in self._earlier_datasets():
            for record in records:
                if (isinstance(record, dict) and record.get('remote_path') == remote_path
                        and record.get('status') in ('downloaded', 'reused') and record.get('size') == size
                        and record.get('mtime') == mtime and isinstance(record.get('path'), str)):
                    local = folder / record['path']
                    # Resolve both sides: OneDrive and mapped drives can change a path's spelling.
                    if local.is_file() and folder.resolve() in local.resolve().parents:
                        yield f'earlier Robot Status dataset {folder.name}', lambda local=local: local.open('rb'), record.get('sha256')
        if self.project is not None and kind == 'log':
            local = self.project / PROJECT_FOLDERS['logs'] / PurePosixPath(remote_path).name
            try:
                matches = (local.is_file() and local.stat().st_size == size and size > 0
                           and local.read_bytes()[-TAIL_BYTES:] == self._remote_tail(remote_path, size))
            except OSError:
                matches = False
            if matches:
                yield 'Logs folder', lambda local=local: local.open('rb'), None

    def _place(self, opener, kind, local_name, expected=None):
        """Copy one local candidate into the snapshot; None if its content does not check out."""
        directory = self.snapshot_path / ('logs' if kind == 'log' else 'flights')
        directory.mkdir(exist_ok=True)
        temporary = directory / f'.reuse-{uuid4().hex[:8]}.part{Path(local_name).suffix}'
        digest = hashlib.sha256()
        try:
            stream = opener()
            try:
                with temporary.open('wb') as local:
                    while block := stream.read(1024 * 1024):
                        self.check_cancel()
                        digest.update(block)
                        local.write(block)
            finally:
                stream.close()
                getattr(stream, 'close_archive', lambda: None)()
            if expected and digest.hexdigest() != expected:
                return None
            if kind == 'flight' and temporary.suffix.lower() == '.zip':
                with zipfile.ZipFile(temporary) as archive:
                    if TELEMETRY_MEMBER not in archive.namelist() or archive.testzip() is not None:
                        return None
            target = directory / local_name
            os.replace(temporary, target)
            return target, digest.hexdigest()
        except (OSError, zipfile.BadZipFile, tarfile.TarError, EOFError):
            return None
        finally:
            temporary.unlink(missing_ok=True)

    def _reuse(self, remote_path, kind, local_name, size, mtime):
        if self.downloaded_files + self.reused_files >= self.max_files:
            return False
        for origin, opener, expected in self._candidates(remote_path, kind, size, mtime):
            self.check_cancel()
            self.events.put(('status', f'Using {PurePosixPath(remote_path).name} from {origin}'))
            placed = self._place(opener, kind, local_name, expected)
            if placed is not None:
                target, digest = placed
                self.records.append(dict(kind=kind, remote_path=remote_path,
                                         path=target.relative_to(self.snapshot_path).as_posix(),
                                         status='reused', origin=origin, size=size, mtime=mtime, sha256=digest))
                self.reused_files += 1
                self._save_diagnostic_manifest('collecting')
                return True
        return False

    def _download_archive(self, logs, flights):
        """Nothing local matched: one tar of the logs and recordings, kept in Backups."""
        folder = self.project / PROJECT_FOLDERS['backups']
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / self.archive_name
        if destination.exists():
            destination = folder / f'{Path(self.archive_name).stem}-{uuid4().hex[:8]}.tar'
        sources = [path for path, _name, _size, _mtime in logs]
        if flights:
            sources.append(self.directory)
        remote_dir = f'/tmp/ur-status-{uuid4().hex}'
        self.remote_path = remote_dir + '/robot-status.tar'
        created = False
        try:
            self.sftp.mkdir(remote_dir, mode=0o700)
            created = True
            self.create_archive(self.client, sources)
            self.check_cancel()
            size = self.sftp.stat(self.remote_path).st_size
            if size <= 0:
                raise OSError('Robot produced an empty archive.')
            self.save_archive(self.sftp, destination, size)
        finally:
            if created:
                for action in (lambda: self.sftp.remove(self.remote_path), lambda: self.sftp.rmdir(remote_dir)):
                    try:
                        action()
                    except Exception:
                        pass  # best effort; the next connect does not depend on it
        self.saved_archive = destination
        members = _tar_members(destination)
        self._archives, self._pending_archives = [(destination, members)], []
        for kind, items in (('log', logs), ('flight', flights)):
            for remote_path, local_name, _size, _mtime in items:
                member = members.get(remote_path)
                if member is not None:  # otherwise left to the per-file download below
                    # Freshly made: the robot's current copy, even if a log grew since it was listed.
                    self._reuse(remote_path, kind, local_name, member.size, int(member.mtime))
        for record in self.records:
            if record['status'] == 'reused':
                record['origin'] = 'downloaded as ' + destination.name

    def _summary(self):
        origins = {}
        for record in self.records:
            if record['status'] == 'reused':
                origins[record['origin']] = origins.get(record['origin'], 0) + 1
        parts = [f'{count} from {origin}' for origin, count in origins.items()]
        if self.downloaded_files:
            parts.append(f'{self.downloaded_files} downloaded')
        return 'Diagnostic sources saved: ' + (', '.join(parts) or 'none') + '.'

    def _result(self, status, message):
        return dict(path=self.snapshot_path, warnings=self.warnings.copy(),
                    complete=status == 'complete', downloaded_files=self.downloaded_files,
                    reused_files=self.reused_files, archive=self.saved_archive, message=message)

    def _remote_logs(self):
        current = PurePosixPath(self.connection.path)
        if current.suffix.lower() == '.bak':
            current = current.with_suffix('.txt')
        logs = []
        for remote, name in dict.fromkeys(((str(current.with_suffix('.bak')), 'log_history.bak'),
                                           (str(current), 'log_history.txt'))):
            try:
                attr = self._attempt(lambda remote=remote: (self._ensure_connection(), self.sftp.stat(remote))[1])
            except InterruptedError:
                raise
            except Exception:
                logs.append((remote, name, None, None))  # the per-file collection records why
                continue
            logs.append((remote, name, int(attr.st_size or 0), int(attr.st_mtime or 0)))
        return logs

    def run(self):
        status, event, message = 'failed', 'error', 'No usable diagnostic sources were retrieved.'
        try:
            self.check_cancel()
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            self.snapshot_path = Path(tempfile.mkdtemp(prefix=f'robot-diagnostics-{stamp}-',
                                                      dir=self.destination))
            self.library = self.snapshot_path / 'flights'
            self._save_diagnostic_manifest('collecting')
            self._attempt(self._ensure_connection)
            logs = self._remote_logs()
            try:
                flights = self._attempt(self._list_flights)
            except InterruptedError:
                raise
            except Exception as error:
                self.check_cancel()
                message = self._error(error)
                self.records.append(dict(kind='flight', remote_path=self.directory, path=None,
                        status='missing' if isinstance(error, OSError) and error.errno == errno.ENOENT else 'failed',
                        error=message))
                self._warn(f'Flight recordings: {message}')
                flights = []
            if self.project is not None:
                self.events.put(('status', 'Checking the project for files already saved…'))
                wanted = [*((r, 'log', n, s, m) for r, n, s, m in logs if s is not None),
                          *((r, 'flight', n, s, m) for r, n, s, m in flights)]
                for remote_path, kind, local_name, size, mtime in wanted:
                    self._reuse(remote_path, kind, local_name, size, mtime)
                listed = sum(size for *_, size, _mtime in wanted)
                if wanted and not self.reused_files and listed > self.max_total_bytes:
                    self._warn('The logs and recordings exceed the download size limit; downloading files one by one up to the limit.')
                elif wanted and not self.reused_files:
                    try:
                        self._attempt(lambda: (self._ensure_connection(), self._download_archive(
                            [item for item in logs if item[2] is not None], flights)))
                    except InterruptedError:
                        raise
                    except Exception as error:
                        self.check_cancel()
                        self._warn(f'Single-archive download failed ({self._error(error)}); downloading files one by one.')
            for remote, name, _size, _mtime in logs:
                if not self._reused(remote):
                    self._collect_source(remote, 'log', name)
            self._collect_flights(flights)
            self.check_cancel()
            if self.downloaded_files or self.reused_files:
                status = 'partial' if self.warnings else 'complete'
                event, message = 'collected', self._summary()
        except Exception as error:
            if self.stopped.is_set() or self.closing.is_set() or isinstance(error, InterruptedError):
                status, event = 'cancelled', 'cancelled'
                message = 'Collection cancelled. Completed source files were preserved.'
                for record in self.records:
                    if record['status'] == 'pending':
                        record.update(status='cancelled', error='Collection cancelled.')
            else:
                message = self._error(error)
            self._warn(message)
        finally:
            self._disconnect()
            try:
                self._save_diagnostic_manifest(status)
            except OSError as error:
                event, status = 'error', 'failed'
                message = f'Could not save diagnostic coverage manifest: {self._error(error)}'
                self._warn(message)
            self.events.put((event, self._result(status, message)))
            self.connection = None
