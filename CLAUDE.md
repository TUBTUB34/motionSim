# ArmChair (motionSim, formerly UR Motion Studio)

Read-only Tkinter + matplotlib + numpy desktop app for Universal Robots: live RTDE viewer, live monitor/recorder, flight reports, logs, Program check and Robot Status PDFs, backups. It never sends motion or scripts to a robot.

## Run / test (verified 2026-11-19)
- App: `.venv/bin/python main.py`. Packaging check: `.venv/bin/python main.py --smoke-test`.
- Tests (WSL, no windows opened): `.venv/bin/python -m unittest discover -s tests`. UI tests are skipped unless `MOTIONSIM_UI_TESTS=1`. When staging a copy for Windows, delete its old `*.py`/`__pycache__` first so stale modules cannot load.
- **Never open app windows on the user's visible desktop.** WSL has no Xvfb. Run UI tests and the EXE smoke on the hidden Windows desktop: `scripts/run_hidden.py` from the staged copy (ECC: "never run UI tests…").
- Windows EXE: `build_windows.bat` (PyInstaller, `MotionStudio.spec`; the spec collects every `motionstudio` module) builds `dist\ArmChair-v<version>.exe`.
- Version: `motionstudio/__init__.py` `__version__`; release = bump it, add a `CHANGELOG.md` entry, tag `v<version>`. The app is shown as ArmChair (`core/about.APP_NAME`: titles, dialogs, PDF author, recordings); data folders keep the old names (`URMotionStudio` preferences, `Documents\UR Motion Studio` working folder) so saved settings stay found.
- Benchmarks (simulated, never a robot): `scripts/benchmark_transfer.py`, `scripts/benchmark_fast_transfer.py`.

## Rules
- Tests must never write to real user files. Pass `trust_path=` (temp) to SSH jobs, and `preferences_path=` to `SimulatorApp` (its projects then live beside that file). Hidden Windows runs also set `MOTIONSIM_WORKSPACE`, `MOTIONSIM_PREFERENCES_PATH`, `USERPROFILE`, `LOCALAPPDATA` to an isolated folder (Documents is looked up via the shell, not `USERPROFILE`).
- Saves go to the active project (`core/paths.project_folder(app, kind)`; dialogs use `start_folder`). Create folders only on save, never when a page opens. Pages that hold a destination implement `project_changed()`.
- Fonts: `theme.font(size, weight, mono)` named fonts follow Settings → Text size; never pass `('TkDefaultFont', N)` tuples.
- Credentials stay in memory only. Approved SSH host keys are saved to `~/.ssh/known_hosts` after a successful connect. A changed key always asks again.
- Recordings hold real streamed channels only. Derived values (e.g. TCP acceleration) stay in the view.
- Live charts refresh through `LiveCharts.paint` (cached per-panel strips, one panel per idle turn); a full `canvas.draw()` only on layout/size/window change. `tests/test_live_view` pixel test guards it; measure UI changes with a hidden-desktop probe, not by feel.
- Mounting adjustments are per robot (`robot_view.LIVE` / `recording_key(report)`); a recording never takes the connected robot's angles.

## Code lookup order
1. ECC memory: `ecc memory search "motionsim <topic>" --scope user` (tag `project-motionsim`).
2. Code graph: codebase-memory-mcp project `motionSim` (`search_graph`, `get_code_snippet`, `get_file_outline`).
3. Targeted `Read` with offset/limit, or grep. Use the map below to pick the file.

## Structure and dependency rules
`main.py` only launches `motionstudio.app.main_window.main()`. `tests/test_architecture.py` enforces:
shared packages (`core` < `ui`/`remote`/`reporting` < `viewer`) never import features or `app`; a feature
imports another feature only via `FEATURE_DEPENDENCIES` there, never its `view`; only `app` wires pages.
Put new shared code in the lowest package that fits instead of importing across features.

## File map (`motionstudio/`)
- `app/`: `main_window.py` (SimulatorApp: connection form, poll loop, fps/reconnect status, `set_project`/`busy_pages`), `shell.py` (nav + side simulator), `home.py`, `settings_view.py` (Settings page), `preferences.py`, `help_content.py`/`help_view.py`, `model_catalog.py`, `status_view.py`, `smoke.py` (`--smoke-test`).
- `core/`: `about.py` (APP_NAME), `kinematics.py` (MODELS, JOINTS), `telemetry.py`, `installation.py`, `robot_assets.py`, `tooling.py`, `playback.py` (recorded-motion timeline), `paths.py` (working folder, projects, `project_folder`, earlier-data move), `rendering.py` (RENDER_LOCK).
- `ui/`: `theme.py` (tokens, `flow()`, `status_color`), `dialogs.py`, `workspace_panel.py`, `address_book.py`, `charts.py` (joint colours, palette, `StackedPanels`), `chart_canvas.py` (ChartCanvas).
- `remote/`: `connection.py` (settings, `active_robot_settings`, `transfer_connection`, `robot_download_folder`), `ssh_trust.py` (prompt, HostKeyQuestion, ConfirmHostKey, saving keys), `ssh_job.py` (SshConnection, SshJob: verified connect, `open_bulk`), `fast_transfer.py` (AsyncSSH), `stream.py` (`copy_remote`, SSH_TIMEOUT), `installation_transfer.py` (SCP).
- `reporting/pdf.py`: ReportDocument (A4 frame for every PDF).
- `viewer/`: `robot_view.py` (RobotView), `rasterizer.py`, `mounting.py`, `playback.py`.
- `live/`: `rtde_client.py` (CONNECT_TIMEOUT / DATA_TIMEOUT / STALE_AFTER), `session.py` (RobotSession, auto-reconnect), `recorder.py` (LiveCapture), `view.py` (Live monitor).
- `flight/`: `data.py`, `stats.py`, `charts.py`, `pdf.py`, `transfer.py`, `view.py`, `retrieval_view.py`. `logs/`: `parser.py`, `data.py`, `transfer.py` (LogJob), `view.py`.
- `programs/`: `check.py`, `motion.py`, `pdf.py`, `transfer.py`, `view.py`. `diagnostics/` (Robot Status): `analysis.py`, `status.py`, `guidance.py`, `pdf.py`, `transfer.py`, `view.py`.
- `backup/`: `transfer.py`, `view.py`. `commissioning/` (Checklist): `records.py`, `view.py`.
- Elsewhere: `samples/` (example installation and log), `docs/upgrade-2026-11-12.md` (historical code map, pre-package names).
