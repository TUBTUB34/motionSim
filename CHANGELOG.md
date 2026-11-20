# Changelog

Versions follow `motionstudio/__init__.py` (`__version__`). Each release is tagged `v<version>` in git, and the
Windows build is `dist/ArmChair-v<version>.exe`.

## 2.0.2 (2026-11-20)

First numbered release.

- Settings page (Ctrl+,): working folder, projects, light/dark theme, text size (90–150 %), saved robots.
- Projects: everything saved (flight reports, robot programs, Robot Status, backups, logs, reports, checklists)
  goes into the active project in the working folder (default `Documents\UR Motion Studio`). Save and open dialogs
  start there. Files from earlier versions can be moved in from Settings; nothing is overwritten.
- The footer shows the active project; the theme switch moved to Settings.
- The version is shown in Settings and in the EXE's file properties.
