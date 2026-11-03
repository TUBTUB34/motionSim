# UR guidance and application review evidence

Current source review: 2026-11-02. [Change and coverage notes](../../docs/app-review-2026-11.md).
Screenshots and example PDFs use synthetic fixtures, except the explicitly named
App_DoughFactory PDF, which uses the supplied configuration archive.

- [Home](home-loaded-wide.png) · [compact Home](home-compact.png)
- [Checklist](checklist-compact.png) · [connection](connection-compact.png)
- [Program review](program-review-compact.png) · [Robot Status](robot-status-compact.png)
- [Logs](logs-compact.png) · [flights](flights-compact.png)
- [Backups](backups-compact.png) · [simulator](simulator-compact.png)
- [Program guidance example](program-guidance-example.pdf)
- [Robot Status example](robot-status-example.pdf)
- [Supplied App_DoughFactory review](App_DoughFactory-guidance.pdf)

`core-tests.log`: 448 discovered, 340 passed, 108 display-skipped; the final new
compact flight toolbar test was subsequently included in the native layout run.
`native-tests.json` and `test_*.log`: 129 passing native/module checks across
fifteen modules. Counts overlap some core checks. `supplied-program-verification.json`
records program/PDF checks.

Windows: 145 passed / one optional PDF-inspection skip, eight final layout checks
passed, and source smoke, exact batch build and bundled smoke passed. All 128
snapshot files match the final source. Results and source/artifact hashes are in
`windows-verification.json`; independent verification is in
`artifact-source-verification.json`.
The current artifact is `dist/URMotionStudio-reviewed.exe`; earlier default and
production executables are preserved. Read the receipt before using a packaged
artifact as evidence for a particular source revision.

No real robot or production controller was contacted. This is offline/software
verification, not physical robot acceptance or safety certification.
