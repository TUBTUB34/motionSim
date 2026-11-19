# UR Motion Studio (motionSim)

Read-only Tkinter + matplotlib + numpy desktop app for Universal Robots: live RTDE viewer, live monitor/recorder, flight reports, logs, Program check and Robot Status PDFs, backups. It never sends motion or scripts to a robot.

## Run / test (verified 2026-11-19)
- App: `.venv/bin/python main.py`. Packaging check: `.venv/bin/python main.py --smoke-test`.
- Tests (WSL, no windows opened): `.venv/bin/python -m unittest discover -s tests`. UI tests are skipped unless `MOTIONSIM_UI_TESTS=1`.
- **Never open app windows on the user's visible desktop.** WSL has no Xvfb. Run UI tests and the EXE smoke on the hidden Windows desktop: `scripts/run_hidden.py` from the staged copy (ECC: "never run UI tests…").
- Windows EXE: `build_windows.bat` (PyInstaller, `MotionStudio.spec`). Add every new module to the spec's module guard.
- Benchmarks (simulated, never a robot): `scripts/benchmark_transfer.py`, `scripts/benchmark_fast_transfer.py`.

## Rules
- Tests must never write to real user files. Pass `trust_path=` (temp) to SSH jobs, and `preferences_path=` to `SimulatorApp`.
- Credentials stay in memory only. Approved SSH host keys are saved to `~/.ssh/known_hosts` after a successful connect. A changed key always asks again.
- Recordings hold real streamed channels only. Derived values (e.g. TCP acceleration) stay in the view.

## Code lookup order
1. ECC memory: `ecc memory search "motionsim <topic>" --scope user` (tag `project-motionsim`).
2. Code graph: codebase-memory-mcp project `motionSim` (`search_graph`, `get_code_snippet`, `get_file_outline`).
3. Targeted `Read` with offset/limit, or grep. Use the map below to pick the file.

## File map
- Entry/shell: `main.py` (SimulatorApp, connection form, poll loop, fps status), `app_shell.py` (nav + side simulator), `home_view.py`, `workspace_panel.py`, `ui_theme.py` (tokens, `flow()`, `status_color`), `ui_dialogs.py`, `app_preferences.py`, `address_book.py`, `help_content.py`/`help_view.py`.
- Live robot: `rtde_client.py` (RTDE; CONNECT_TIMEOUT / DATA_TIMEOUT / STALE_AFTER), `session.py` (RobotSession threads), `telemetry.py`, `status_view.py`, `live_recorder.py` (LiveCapture ring + recordings), `live_view.py` (Live monitor charts).
- 3D: `view.py` (RobotView), `rasterizer.py`, `robot_assets.py`, `kinematics.py`, `tooling.py`, `mounting_view.py`, `playback_view.py`, `model_catalog.py`, `installation.py`.
- SSH/transfer: `connection.py` (settings, `active_robot_settings`, `transfer_connection`), `ssh_trust.py` (prompt + saving keys), `transfer.py` (ConfirmHostKey, installation SCP), `log_transfer.py` (LogJob: shared verified connect, `open_bulk`), `fast_transfer.py` (AsyncSSH bulk reads), `transfer_stream.py` (`copy_remote`, SSH_TIMEOUT), `flight_transfer.py`, `backup_transfer.py`, `program_transfer.py`, `diagnostic_transfer.py`.
- Flight reports: `flight_data.py`, `flight_stats.py`, `flight_charts.py`, `flight_motion.py`, `flight_view.py`, `flight_retrieval_view.py`, `flight_pdf.py`.
- Logs: `log_parser.py`, `log_data.py`, `log_view.py`.
- Reports: `program_check.py`, `program_motion.py`, `program_check_view.py`, `program_check_pdf.py`. Robot Status: `robot_diagnostics.py`, `robot_status.py`, `diagnostic_guidance.py`, `diagnostic_view.py`, `diagnostic_pdf.py`.
- Other: `backup_view.py`, `commissioning.py`/`commissioning_view.py` (Checklist), `desktop_smoke.py`.
- Docs: `docs/upgrade-2026-11-12.md` (earlier upgrade code map).
