# Changelog

Versions follow `motionstudio/__init__.py` (`__version__`). Each release is tagged `v<version>` in git, and the
Windows build is `dist/ArmChair-v<version>.exe`.

## 2.1.0 (2026-11-20)

- Robot Status report rebuilt as an arm health report: overall status, headline numbers and top risks; a joint
  dashboard (Good / Monitor / Attention required / Critical with key metrics, event counts and trend); findings
  grouped by event type and joint with severity; where the program struggles, with Program Check cross-references;
  event timeline; recommendations split into program, maintenance, application and further data. Rating rules are
  printed in the report. Repeated text and incident-ID lists are gone.
- Program Check report follows the same layout: health score, severity-rated findings (P-01…) with a specific fix,
  observations to verify on the robot, and a prioritised action list.
- Robot Status runs Program Check on the program saved in each matched flight recording.
- All PDFs carry the APEX logo in the header.
- Flight recordings are now matched to log events on the controller clock (logs have no time zone), and recordings
  are no longer dropped by an over-strict memory limit.
- Collect & analyze reuses files the project already holds (backup tar, Flight reports, earlier Robot Status runs,
  Logs) and downloads only what is missing; with nothing saved it downloads one tar into Backups.
- Fixed the empty "Flight recordings:" collection warning. The footer and new live recordings show ArmChair.

## 2.0.2 (2026-11-20)

First numbered release.

- Settings page (Ctrl+,): working folder, projects, light/dark theme, text size (90–150 %), saved robots.
- Projects: everything saved (flight reports, robot programs, Robot Status, backups, logs, reports, checklists)
  goes into the active project in the working folder (default `Documents\UR Motion Studio`). Save and open dialogs
  start there. Files from earlier versions can be moved in from Settings; nothing is overwritten.
- The footer shows the active project; the theme switch moved to Settings.
- The app is now called ArmChair (window titles, dialogs, PDF author, recordings). Data folders keep their
  earlier names, so saved settings and projects are found as before.
- The version is shown in Settings and in the EXE's file properties.
