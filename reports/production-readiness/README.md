# Integrated workspaces and Robot Status review

Verified on 2026-11-02. All report examples and screenshots in this folder use
synthetic data. No physical robot was contacted for this verification.

## Delivered behavior

- Persistent Simulator, Flight Reports, Logs, Backups and Reports pages retain
  state and active work during navigation.
- Reports separates Program Checker from the client-facing Robot Status review.
- Flight analysis and retrieval share a page. An explicit separate simulator
  shares the current recording/playhead or live connection for comparison.
- Backups has its own connection, destination, contents and activity controls.
  Navigation does not start robot work; existing local archives are preserved.
- Robot Status includes an overall assessment, all six joint summaries,
  program areas, a grouped action plan, coverage and references. Client, asset
  and preparer details are optional. Detailed incidents/charts are an opt-in
  appendix. Missing or provisional evidence is visible in the client assessment.

## Verification

- Full automated discovery: 405 tests, 312 passed and 93 native-display checks
  skipped in that run. Native suites were also executed separately.
- Final report model/PDF regressions: 28 passed, including the three final
  refinements for provisional identity, grouped recommendations and date labels.
- Native workspace and existing Program Checker/playback: 18 passed.
- Native Robot Status: 11 passed. Backup/layout: 12 passed; final backup shutdown
  recheck: 5 passed. Logs: 10 passed. Flight charts: 18 passed. Retrieval: 12 passed.
- Additional native suites passed: connection/UI 3, joint comparison 11, theme 9,
  model catalog 3 and camera 3. The final Linux offline desktop smoke test passed.
- Windows: 78 regression checks passed in the latest source snapshot. The exact
  `build_windows.bat` completed with exit 0, including the bundled executable's
  offline `--smoke-test`. Python 3.14.7 / PyInstaller 6.22.3, using the existing
  build environment with package downloads disabled.
- Current application source hashes were checked against the Windows snapshot.
  See `windows-verification.json`, `windows-tests.log` and `windows-build.log`.

The verified executable is `dist/URMotionStudio-production.exe` (gitignored).
The previous default executable was not replaced. Its SHA-256 is recorded in
`windows-verification.json`.

## Review artifacts

The November 2 follow-up adds shared simulator credentials and required
project/date backup filenames. Downloads resolve the active identity at action
time; duplicate credential controls are hidden while connected and return after
disconnecting. A missing simulator SSH password directs the user to the central
connection settings. Existing transfers retain their original connection.

Backups use `ProjectName-YYYY-MM-DD.tar` with the UTC date captured at start.
Names are made portable for Windows, and collisions receive a unique suffix.
Focused source checks passed (41 tests, 9 native-display skips); native backup,
connection and layout checks passed (20 tests), followed by the existing log,
flight retrieval and report UI suites. The latest Windows receipt and executable
include these changes. New UI captures:
[shared connection](project-backup-connected.png) /
[offline credentials](project-backup-offline.png).

- [Client Robot Status example](robot-status-example.pdf)
- [Robot Status page, compact size](robot-status-compact.png)
- [Robot Status page](robot-status.png)
- [Backups](backups.png)
- [Flight Reports](flight-reports.png)
- [Simulator](simulator.png)
- [Separate simulator](separate-simulator.png)
- [Logs](logs.png)
- [Program Checker](program-checker.png)

Robot communication was exercised with mocked providers and a local loopback
server. These checks do not constitute physical-robot acceptance testing. A
Robot Status report describes the supplied controller history and measurements;
it does not certify present mechanical condition or predict remaining joint life.
