"""Isolated Windows validation; run with the existing verified Python environment."""
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import uuid


SOURCE = Path(r'\\wsl.localhost\Ubuntu\home\apex\workspace\motionSim')
ENVIRONMENT = Path(r'C:\Users\apex\AppData\Local\Temp\motionsim-audit-20261030-094744\.venv-build')
REPORTS = SOURCE / 'reports' / 'app-review-2026-11'
MARKER = Path(r'\\wsl.localhost\Ubuntu\tmp\motionsim-review-build-root.txt')
EXPECTED_PYTHON = ENVIRONMENT / 'Scripts' / 'python.exe'
MODULES = [
    'tests.test_home', 'tests.test_commissioning', 'tests.test_commissioning_ui',
    'tests.test_workspace_ui', 'tests.test_design_ui', 'tests.test_connection_sharing',
    'tests.test_backup_page', 'tests.test_program_check', 'tests.test_program_motion',
    'tests.test_program_check_pdf', 'tests.test_robot_status', 'tests.test_robot_diagnostics',
    'tests.test_diagnostic_guidance', 'tests.test_diagnostic_ui', 'tests.test_log_parser',
]


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(command, log, timeout, env, cwd):
    print('START', Path(log).name, flush=True)
    with Path(log).open('w', encoding='utf-8') as stream:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=stream,
                                   stderr=subprocess.STDOUT)
        try:
            result = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                           stdout=stream, stderr=subprocess.STDOUT, check=False)
            raise RuntimeError(f'{Path(log).name} timed out; stopped its process tree') from None
    print('FINISH', Path(log).name, 'exit', result, flush=True)
    print('\n'.join(Path(log).read_text(encoding='utf-8', errors='replace').splitlines()[-12:]), flush=True)
    return result


def main():
    if sys.platform != 'win32' or not EXPECTED_PYTHON.exists():
        raise RuntimeError('The existing Windows Python environment is required')
    REPORTS.mkdir(parents=True, exist_ok=True)
    build_root = Path(tempfile.mkdtemp(prefix='motionsim-review-20261102-'))
    MARKER.write_text(str(build_root), encoding='utf-8')
    details = {
        'started': now(), 'build_root': str(build_root), 'python': sys.version,
        'platform': sys.getwindowsversion()._asdict() if hasattr(sys.getwindowsversion(), '_asdict') else str(sys.getwindowsversion()),
        'pyinstaller': importlib.metadata.version('pyinstaller'),
        'dependency_mode': 'Existing verified environment; PIP_NO_INDEX=1; no package downloads',
        'unit_test_modules': MODULES,
        'omitted_windows_module': {'name': 'tests.test_robot_status_pdf',
                                  'reason': 'Optional PyMuPDF is absent. Linux text/bounds tests passed; Windows source and packaged PDF exports are smoke-tested.'},
        'build_command': 'build_windows.bat',
        'smoke_test': 'Exact batch runs dist\\URMotionStudio.exe --smoke-test and propagates failure',
        'workspace_source_hashes_verified': False,
    }
    try:
        special = {'build_windows.bat', 'MotionStudio.spec', 'requirements.txt', 'requirements-build.txt'}
        paths = sorted(path for path in SOURCE.iterdir() if path.is_file() and (path.suffix == '.py' or path.name in special))
        paths.extend(sorted((SOURCE / 'tests').glob('*.py')))
        for folder in ('assets/robots', 'assets/flightreports'):
            paths.extend(sorted(path for path in (SOURCE / folder).iterdir() if path.is_file()))
        paths.extend(sorted((SOURCE / 'docs').glob('*.pdf')))
        manifest = []
        for path in paths:
            relative = path.relative_to(SOURCE)
            original_digest = digest(path)
            target = build_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            if digest(target) != original_digest or digest(path) != original_digest:
                raise RuntimeError(f'Source changed during snapshot: {relative}')
            manifest.append({'name': relative.as_posix(), 'sha256': original_digest})
        details['source_manifest'] = manifest
        (REPORTS / 'windows-source-snapshot.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        junction = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(build_root / '.venv-build'), str(ENVIRONMENT)],
                                  capture_output=True, text=True)
        if junction.returncode:
            raise RuntimeError('Could not link the existing build environment: ' + junction.stderr)
        environment = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                           PIP_NO_CACHE_DIR='1', MOTIONSIM_UI_TESTS='1', PYTHONIOENCODING='utf-8')
        python = str(build_root / '.venv-build' / 'Scripts' / 'python.exe')
        code = run([python, '-m', 'unittest', '-v', *MODULES], build_root / 'windows-tests.log',
                   timeout=600, env=environment, cwd=build_root)
        details['unit_test_exit_code'] = code
        if code:
            raise RuntimeError('Windows regression tests failed')
        source_smoke = run([python, 'main.py', '--smoke-test'], build_root / 'windows-source-smoke.log',
                           timeout=300, env=environment, cwd=build_root)
        details['source_smoke_exit_code'] = source_smoke
        if source_smoke:
            raise RuntimeError('Windows source smoke failed')
        code = run(['cmd.exe', '/d', '/c', 'build_windows.bat'], build_root / 'windows-build.log',
                   timeout=1200, env=environment, cwd=build_root)
        details['build_exit_code'] = code
        if code:
            raise RuntimeError('Windows batch build or bundled smoke failed')
        differences = [entry['name'] for entry in manifest
                       if not (SOURCE / entry['name']).is_file() or digest(SOURCE / entry['name']) != entry['sha256']]
        if differences:
            details['source_changes_after_snapshot'] = differences
            raise RuntimeError('Workspace files changed during build; executable not published')
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
        details['success'] = False
        details['error'] = str(error)
        traceback.print_exc()
        return 1
    finally:
        details['finished'] = now()
        for name in ('windows-tests.log', 'windows-source-smoke.log', 'windows-build.log'):
            path = build_root / name
            if path.is_file():
                shutil.copyfile(path, REPORTS / name)
        shutil.copyfile(Path(__file__), REPORTS / 'windows-build-runner.py')
        (REPORTS / 'windows-verification.json').write_text(json.dumps(details, indent=2), encoding='utf-8')
        print('RECEIPT', REPORTS / 'windows-verification.json', flush=True)


if __name__ == '__main__':
    sys.exit(main())
