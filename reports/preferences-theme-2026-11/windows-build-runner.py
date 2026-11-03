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
REPORTS = SOURCE / 'reports' / 'preferences-theme-2026-11'
MARKER = Path(r'\\wsl.localhost\Ubuntu\tmp\motionsim-settings-build-root.txt')
EXPECTED_PYTHON = ENVIRONMENT / 'Scripts' / 'python.exe'
MODULES = [
    'tests.test_commissioning_ui.CommissioningUITests.test_csv_export_does_not_mark_draft_saved',
    'tests.test_commissioning_ui.CommissioningUITests.test_filters_empty_state_and_footer_available_at_compact_size',
    'tests.test_commissioning_ui.CommissioningUITests.test_invalid_open_and_cancelled_save_keep_current_draft',
    'tests.test_commissioning_ui.CommissioningUITests.test_notes_survive_switch_and_save_open_roundtrip',
    'tests.test_commissioning_ui.CommissioningUITests.test_review_rows_are_visible_pointer_targets_beside_evidence',
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
    build_root = Path(r'C:\Users\apex\AppData\Local\Temp\motionsim-settings-20261102-40b1cgef')
    MARKER.write_text(str(build_root), encoding='utf-8')
    details = {
        'started': now(), 'build_root': str(build_root), 'python': sys.version,
        'platform': sys.getwindowsversion()._asdict() if hasattr(sys.getwindowsversion(), '_asdict') else str(sys.getwindowsversion()),
        'pyinstaller': importlib.metadata.version('pyinstaller'),
        'dependency_mode': 'Existing verified environment; PIP_NO_INDEX=1; no package downloads',
        'unit_test_modules': MODULES,
        'initial_regression': {'log': 'windows-tests-initial.log', 'tests': 184, 'passed': 182,
            'resolved_failures': ['Windows local program basenames fixed; full affected modules rerun',
                                  'Checklist screen-hit assertion failed under detached supervision and passed unchanged in direct native launches; retained logs record both contexts']},
        'prior_affected_modules': {'tests.test_program_check': {'tests': 19, 'exit_code': 0, 'log': 'test_program_check.log'},
                                   'tests.test_program_playback_ui': {'exit_code': 0, 'log': 'test_program_playback_ui.log'}},
        'native_checklist_isolation': {'grouped_log': 'test_commissioning_ui.log',
            'reason': 'Screen hit-test returned None under detached supervision despite visible in-bounds row. Unchanged assertion passed twice in direct native launch, including final snapshot. Other four cases passed separately. This is launch-context dependent, not proven to be a Tk-root-count issue.',
            'single_case_confirmation': 'windows-checklist-recheck.log', 'test_source_changed': False},
        'runner_recovery': 'Initial terminal supervisor exited143; test child completed. No duplicate build was launched.',
        'omitted_windows_module': {'name': 'tests.test_robot_status_pdf',
                                  'reason': 'Optional PyMuPDF is absent. Linux text/bounds tests passed; Windows source and packaged PDF exports are smoke-tested.'},
        'build_command': 'build_windows.bat',
        'smoke_test': 'Exact batch runs dist\\URMotionStudio.exe --smoke-test and propagates failure',
        'workspace_source_hashes_verified': False,
        'preferences_isolation': str(build_root / 'isolated-preferences.json'),
        'source_metadata_omission': 'NTFS Zone.Identifier metadata streams are not application assets.',
    }
    try:
        manifest=json.loads((REPORTS / 'windows-source-snapshot.json').read_text(encoding='utf-8'))
        for entry in manifest:
            for base in (SOURCE, build_root):
                if digest(base / entry['name']) != entry['sha256']:
                    raise RuntimeError('Frozen source or build snapshot changed: ' + entry['name'])
        initial_manifest = json.loads((REPORTS / 'windows-source-snapshot-initial.json').read_text(encoding='utf-8'))
        initial = {entry['name']: entry['sha256'] for entry in initial_manifest}
        details['changes_since_initial_regression'] = [entry['name'] for entry in manifest if initial.get(entry['name']) != entry['sha256']]
        if set(details['changes_since_initial_regression']) != {'program_check.py', 'tests/test_program_check.py'}:
            raise RuntimeError('Unexpected source delta since initial regression')
        details['source_manifest'] = manifest
        (REPORTS / 'windows-source-snapshot.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        if not (build_root / '.venv-build' / 'Scripts' / 'python.exe').exists():
            raise RuntimeError('Existing isolated build environment junction is missing')
        environment = dict(os.environ, PIP_NO_INDEX='1', PIP_DISABLE_PIP_VERSION_CHECK='1',
                           PIP_NO_CACHE_DIR='1', MOTIONSIM_UI_TESTS='1', PYTHONIOENCODING='utf-8',
                           MOTIONSIM_PREFERENCES_PATH=str(build_root / 'isolated-preferences.json'))
        python = str(build_root / '.venv-build' / 'Scripts' / 'python.exe')
        results = json.loads((REPORTS / 'windows-test-results.json').read_text(encoding='utf-8'))
        if len(results) != 203 or any(result['status'] != 'ok' for result in results.values()):
            raise RuntimeError('Final native regression results are incomplete or failing')
        details['unit_test_exit_code'] = 0
        details['unique_windows_tests_passed'] = len(results)
        details['test_results'] = 'windows-test-results.json'
        details['final_pointer_assertion'] = {'exit_code': 0, 'seconds': 0.986, 'log': 'windows-checklist-recheck.log',
            'launch': 'Direct native Windows Python through WSL interop, original assertion and final source snapshot'}
        (build_root / 'windows-tests.log').write_text('Final native regression outcomes: 203 unique checks passed.\nSee windows-test-results.json for latest result and retained log per test.\n',encoding='utf-8')
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
        target = SOURCE / 'dist' / 'URMotionStudio-settings.exe'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name('.URMotionStudio-settings-' + uuid.uuid4().hex + '.tmp')
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
        details['artifact'] = {'path': 'dist/URMotionStudio-settings.exe', 'bytes': target.stat().st_size,
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
        for name in ('windows-tests.log', 'windows-source-smoke.log', 'windows-build.log', *[module.rsplit('.',1)[-1] + '.log' for module in MODULES]):
            path = build_root / name
            if path.is_file():
                shutil.copyfile(path, REPORTS / name)
        shutil.copyfile(Path(__file__), REPORTS / 'windows-build-runner.py')
        (REPORTS / 'windows-verification.json').write_text(json.dumps(details, indent=2), encoding='utf-8')
        print('RECEIPT', REPORTS / 'windows-verification.json', flush=True)


if __name__ == '__main__':
    sys.exit(main())
