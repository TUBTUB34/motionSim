# Saved robot addresses and appearance — November 2026

The desktop now remembers robot IP addresses and the selected dark/light appearance
between launches. These settings are local to the current OS user.

## Workflow

- **Simulator → Connection → Save IP** adds the entered address. The editable
  selector shares its choices with Logs, Backups, Program Checker and Robot Status.
  Validated connection and file-retrieval submissions also remember the address.
- **Forget** removes the selected address from the list without disconnecting a
  robot. IPv4/IPv6 addresses are normalized and deduplicated. Order is stable;
  startup displays the first saved address. The list holds up to 50 addresses and
  asks for an explicit removal when full.
- **Dark mode** in the navigation rail selects dark when checked and light when
  unchecked. The app repaints existing controls, charts and robot overlays in
  place. It retains selections, text edits, undo history, chart zoom, playback
  position, camera orientation, loaded reports and ongoing jobs.
- PDF exports retain their print palette. OS file dialogs retain platform-specific
  behavior. Chart image export uses the displayed chart appearance.

## Implementation

`app_preferences.py` stores only a schema version, the IP list and the theme. It
uses an atomic replacement in the user's configuration directory. Failed writes
preserve previous settings; malformed files fall back to defaults with a visible
explanation. Credentials, SSH host keys and report data are not stored in this file.
The README lists platform paths and the optional test-path override.

`address_book.py` provides the shared editable selector. Updating the address list
does not overwrite another page's current input. Main connection validation rejects
an empty field instead of silently substituting a default address.

`ui_theme.py` keeps two cached native themes and repaints Tk surfaces, text/tree
tags, dropdowns and Matplotlib toolbars. Chart and renderer callbacks update
existing surfaces without resetting their state. Theme changes do not change
Matplotlib's global PDF settings. A Home summary regression caused by Tk's inherited
`busy` method on Python 3.14 was also corrected.
Windows verification exposed an existing local-program naming bug: backslash paths
were displayed as full program names. Local pairing keys now use normalized path
separators, preserving exact folder pairing while displaying only the program name.

## Verification

- Linux full discovery: **477 tests, 355 passed, 122 graphical tests skipped**.
  Native preference, theme and chart tests were also run with the desktop display.
  After the Windows path correction, all **19 Program Checker tests** passed,
  including a new local-name/folder-pairing regression.
- Windows: **203 distinct checks have passing final results**, including native
  preferences, theme, charts and workspace checks plus the affected program tests.
  The per-test result map preserves which run supplied each result. The final
  Windows source smoke, exact `build_windows.bat` and packaged executable smoke
  also passed. Source hashes were rechecked before publishing the local artifact.
- New coverage exercises restart persistence, shared selectors, IPv4/IPv6
  normalization, invalid input, duplicate removal, corrupt files, save failures,
  credential exclusion, keyboard actions and retained work across theme changes.
- Visual review on Linux: **26 native captures across 13 surfaces in both modes** at
  1120 × 740. No Tk callback errors or old dark widget colors remained in light
  mode. Evidence is in `reports/preferences-theme-2026-11/visual-qa.json` and the
  adjacent screenshots.
- Source `main.py --smoke-test` passed. The smoke workflow uses a temporary
  preferences file, checks saved settings and both themes, and exercises offline
  reports, navigation and rendering without contacting a controller.

The Windows build receipt and logs in `reports/preferences-theme-2026-11/` record
native Windows tests, the source snapshot, bundled smoke result and the SHA-256 of
`dist/URMotionStudio-settings.exe`. Earlier executables and review evidence are
preserved separately. Robot operations in these checks are mocked; no physical
controller was contacted.

Delivered executable: **108,986,296 bytes**, SHA-256
`3969044d24211538a7f51d7bcf4411558c990d67c8ec26f9b345cbf35044e10e`.

The initial Windows run completed 184 tests with two failures: the program-name
bug described above and a checklist screen hit-test. The corrected Program Checker
and playback suites pass. The checklist hit-test passes when launched directly on
the Windows desktop but fails under the detached test supervisor, even in a fresh
process with valid visible geometry. This points to the desktop launch context;
it does not establish an application layout defect. The final checks retain the
original assertions and record the direct native run separately. The unsuccessful
supervised runs are preserved in the logs rather than described as a clean
full-suite run.
