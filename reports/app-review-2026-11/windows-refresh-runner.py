"""Refresh the two final layout files after the first guarded build completes."""
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import traceback
import uuid

helpers = runpy.run_path(str(Path(__file__).with_name('motionsim-review-windows-build.py')), run_name='build_helpers')
SOURCE, REPORTS, MARKER = (helpers[name] for name in ('SOURCE', 'REPORTS', 'MARKER'))
digest, run, now = (helpers[name] for name in ('digest', 'run', 'now'))


def main():
    receipt = REPORTS / 'windows-verification.json'
    initial = json.loads(receipt.read_text(encoding='utf-8'))
    if any(initial.get(key) != 0 for key in ('unit_test_exit_code', 'source_smoke_exit_code', 'build_exit_code')):
        raise RuntimeError('Initial tests, source smoke and batch build must have passed before a focused refresh')
    expected = {'flight_view.py', 'tests/test_design_ui.py'}
    manifest = initial['source_manifest']
    changed = {entry['name'] for entry in manifest if digest(SOURCE / entry['name']) != entry['sha256']}
    if changed != expected:
        raise RuntimeError(f'Unexpected source changes; review before refreshing: {sorted(changed)}')
    build_root = Path(initial['build_root'])
    if str(build_root) != MARKER.read_text(encoding='utf-8').strip():
        raise RuntimeError('Build directory marker mismatch')
    archive = REPORTS / 'initial-layout-snapshot'
    archive.mkdir(exist_ok=False)
    for name in ('windows-verification.json', 'windows-source-snapshot.json', 'windows-tests.log',
                 'windows-source-smoke.log', 'windows-build.log', 'windows-build-runner.py'):
        shutil.copyfile(REPORTS / name, archive / name)
    details = dict(initial)
    details.update(started=now(), success=False, workspace_source_hashes_verified=False,
                   previous_attempt='initial-layout-snapshot/windows-verification.json',
                   revalidation_modules=['tests.test_design_ui'], refreshed_files=sorted(expected),
                   revalidation_reason='Final compact embedded-flight toolbar fix after the complete initial Windows suite')
    details.pop('error', None)
    details.pop('source_changes_after_snapshot', None)
    details.pop('artifact', None)
    environment = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                       PIP_NO_CACHE_DIR='1', MOTIONSIM_UI_TESTS='1', PYTHONIOENCODING='utf-8')
    python = str(build_root / '.venv-build' / 'Scripts' / 'python.exe')
    try:
        for entry in manifest:
            if entry['name'] not in expected:
                continue
            path = SOURCE / entry['name']
            before = digest(path)
            shutil.copyfile(path, build_root / entry['name'])
            if digest(build_root / entry['name']) != before or digest(path) != before:
                raise RuntimeError('Source changed during refresh: ' + entry['name'])
            entry['sha256'] = before
        (REPORTS / 'windows-source-snapshot.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        code = run([python, '-m', 'unittest', '-v', 'tests.test_design_ui'],
                   build_root / 'windows-refresh-tests.log', 300, environment, build_root)
        details['revalidation_exit_code'] = code
        if code:
            raise RuntimeError('Updated Windows layout tests failed')
        code = run([python, 'main.py', '--smoke-test'], build_root / 'windows-source-smoke.log',
                   300, environment, build_root)
        details['source_smoke_exit_code'] = code
        if code:
            raise RuntimeError('Updated Windows source smoke failed')
        code = run(['cmd.exe', '/d', '/c', 'build_windows.bat'], build_root / 'windows-build.log',
                   1200, environment, build_root)
        details['build_exit_code'] = code
        if code:
            raise RuntimeError('Updated exact batch build or bundled smoke failed')
        differences = [entry['name'] for entry in manifest if digest(SOURCE / entry['name']) != entry['sha256']]
        if differences:
            details['source_changes_after_snapshot'] = differences
            raise RuntimeError('Source changed again during build; executable not published')
        details['workspace_source_hashes_verified'] = True
        executable = build_root / 'dist' / 'URMotionStudio.exe'
        target = SOURCE / 'dist' / 'URMotionStudio-reviewed.exe'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.URMotionStudio-reviewed-' + uuid.uuid4().hex + '.tmp')
        try:
            with executable.open('rb') as incoming, temporary.open('wb') as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            expected_digest = digest(executable)
            if digest(temporary) != expected_digest:
                raise RuntimeError('Executable copy checksum mismatch')
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        details['artifact'] = {'path': 'dist/URMotionStudio-reviewed.exe', 'bytes': target.stat().st_size,
                               'sha256': expected_digest, 'windows_build_path': str(executable)}
        details['success'] = True
        print('ARTIFACT', target, flush=True)
        return 0
    except Exception as error:
        details['error'] = str(error)
        traceback.print_exc()
        return 1
    finally:
        details['finished'] = now()
        for name in ('windows-refresh-tests.log', 'windows-source-smoke.log', 'windows-build.log'):
            path = build_root / name
            if path.is_file():
                shutil.copyfile(path, REPORTS / name)
        shutil.copyfile(Path(__file__), REPORTS / 'windows-refresh-runner.py')
        receipt.write_text(json.dumps(details, indent=2), encoding='utf-8')
        print('RECEIPT', receipt, flush=True)


if __name__ == '__main__':
    sys.exit(main())
