# Completion checkpoint — paused at user request

Claude's mounting correction, regression test, README and in-app help changes
were already present when Codex resumed from ECC checkpoint 4. No application
source changes have been made by Codex in this continuation yet.

Completed checks:

- All 16 local recordings match their saved program's `set_gravity()` direction:
  [mounting-check.json](mounting-check.json).
- Simulator setup, Logs, both Reports tabs, Checklist and retrieval were captured
  and reviewed in light/dark themes; the real 180°/45° playback was also reviewed.
  See [screenshots](../../docs/screenshots-2026-11-12/README.md).
- Linux source smoke exited 0.
- Windows: 38 focused core/recorder/live UI tests passed, source smoke exited 0,
  and the exact `build_windows.bat` completed including the bundled EXE smoke.
  [Receipt](windows-verification.json) includes source hashes, all verified.
- `dist/URMotionStudio.exe`: 109,059,439 bytes; SHA-256
  `dfb2e0ca90e13c37b438a17854de3a5012bfaa4eba537c3d654fa135566c8576`.

The Linux full UI run executed 499 tests with 4 optional backup/oracle skips and
one keyboard-focus failure while screenshot windows were also being opened.
That exact test passed alone unchanged. A final full run with exclusive display
access remains to be done. Initial sandbox failures were display-access errors.
Temporary logs: `/tmp/motionsim-completion-tests-native.log` and
`/tmp/motionsim-completion-tests.log`.

## New request to finish on resume

The user reports that the native Windows UI looks larger than WSL and wants the
same sizing. Investigation only so far:

- WSL: Tk scaling 1.33399, approximately 96 DPI, DejaVu Sans 10 pt,
  17 px line spacing, Home tab 73 × 46 px.
- Windows source: Tk scaling 1.33363, approximately 96 DPI, Segoe UI 10 pt,
  17 px line spacing, Home tab 68 × 46 logical pixels. `GetDpiForSystem()` returns
  96 in this process. See `linux-scale-before.json` / `win32-scale-before.json`.
- Hypothesis still to verify: Windows is bitmap-scaling a DPI-unaware process.
  Neither app startup nor the inspected default PyInstaller manifest sets DPI
  awareness. Matching logical metrics alone does not prove equal physical size.
- No DPI/font/scaling fix has been applied. Capture the app window itself on
  Windows (Pillow supports `ImageGrab.grab(window=...)`); the first bbox capture
  captured the editor behind the app and was removed.

Helpers in `/tmp`: `motionsim_scale_probe.py`, `motionsim_windows_verify.py`.
Windows staged source/environment:
`C:\Users\apex\AppData\Local\Temp\motionsim-upgrade-20261112-vfrq8o52`.
The source environment in this checkout is `.venv`; native Windows Python is
`C:\Program Files\Python314\python.exe`.

After the sizing fix, rerun relevant native UI checks, the full Linux suite and
rebuild/smoke-test the EXE. Update the upgrade document's older verification
section (it still says 498 tests and Windows unverified). No physical robot has
been contacted. All work remains uncommitted; nothing has been pushed.
