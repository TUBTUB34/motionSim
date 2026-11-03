# UR Motion Studio application review — 2026-11-02

This pass extends the existing desktop application and preserves the earlier
uncommitted workspace/report work. Its purpose is a practical UR engineering and
service workspace: inspect evidence, review programs, document commissioning,
preserve robot data and prepare reports.

## What changed

- **Home** provides direct task entry points and live summaries of loaded logs,
  recordings, program/status reports, flight downloads, PDF exports and backups.
  Opening Home or switching tasks never starts a robot connection or transfer.
- **Checklist** adds fourteen sourced commissioning/inspection topics. A review
  keeps project/robot/reviewer identity, manual review states, dates, evidence and
  follow-up notes. JSON save/open is validated and atomic; CSV includes all topics
  and citations. Drafts survive navigation; unsaved work can cancel app shutdown
  before workers or connections are stopped.
- **Program Checker** incorporates the two new UR PDFs, including payload
  transitions/inertia, contextual small blends, process-motion waits, controlled
  stops, processing load and interfaces. Its on-screen filter separates
  recommendations and manual review; exports always retain the complete report.
- **Program and Robot Status PDFs** include a sourced physical-installation
  follow-up page. Source/telemetry cannot establish physical inspection results,
  so those items remain explicitly unassessed. Flight PDFs continue to report
  measurements. See [guidance mapping](ur-report-guidance-2026-11.md).
- **Logs** includes C150–152 limit-proximity and subtype-specific C171 blend
  warnings as controller signals. Controller messages do not increase the number
  of affected joints or acquire invented joint-health ratings.
- **Navigation/layout** retains existing theme and native controls. The connection
  form scrolls; the checklist list and notes sit side by side; compact Flight
  Reports uses its existing retrieval tab without a clipped duplicate shortcut.
  The rail Connection button navigates to the live view without disconnecting it.
  Stale telemetry is labeled in the rail and Home.
- **Reliability** fixes remote `.bak` selection to retrieve both log histories,
  rolls back incomplete local two-file log publication, preserves source
  collection compatibility with the documented Python minimum, and allows an
  explicit SSH trust prompt when diagnostic collection continues on a hidden
  page. Cancel still rejects a pending trust request.

Shortcuts: Ctrl+0 Home; Ctrl+1 Simulator; Ctrl+2 Logs; Ctrl+3 Flight Reports;
Ctrl+4 Program Checker; Ctrl+5 Backups; Ctrl+6 Checklist; F1 Help. Modal dialogs
retain control of these shortcuts.

## Whole-program review coverage

| Area | Review and verification |
| --- | --- |
| RTDE and session lifecycle | Read-only recipe/telemetry, connection cancellation, stale samples, installation retrieval and local mock connection checks. |
| Simulator and playback | Nominal model rendering, recorded/live source separation, playhead/pose interpolation, missing channels, camera and separate-view state. |
| Logs and incidents | Parser/deduplication, controller-vs-joint mapping, filters/counts, rotated-file pairing and failed local-save recovery. |
| Flight analysis/retrieval | Recorded units, missing samples, CRC/size validation, comparisons, cancellation, transfer state and compact navigation. |
| Backups and source collection | Existing-file preservation, partial failure/cancellation, shared credentials, project/date filenames, Windows filename constraints and explicit archive scope. |
| Reports | Source uncertainty, guide applicability, warning subtypes, evidence references, PDF pagination, filtering without losing export contents, background exports. |
| App shell/checklist/help | Persistent pages, keyboard/modal behavior, draft-close guard, status summaries, compact control reachability and current help. |
| Packaging | New modules/catalog included in the module guard and offline smoke workflow; Windows build verification recorded alongside the artifact. |

## Research and scope

[Official UR research](ur-workflow-research-2026-11.md) explains the feature choice
and links the manufacturer's good-installation page, deployment template,
inspection plan, pre-use assessment and Support File workflow. The checklist
closes a gap between automated evidence review and documented physical follow-up.

The backup page now states its actual scope: programs/apps/logs/flights in a TAR
data archive. It is not a controller image or the official UR Support File. The
app does not restore controller images, flash firmware, change safety settings,
reset protective stops automatically or command robot motion. These are separate
workflows requiring controller-specific requirements and physical validation.

Potential next work: safe Support File/archive inventory, a model/software-aware
reference library, and comparison of saved inspection histories. They are
proposals, not shipped features or reminders.

## Verification

- Final core discovery run: **448 tests**, **340 passed**, **108 display checks
  skipped**. The additional final compact-flight test was then verified natively.
- Native desktop modules: **129 checks passed** across fifteen suites, including
  the final eight-test layout suite. These runs overlap some core checks.
- Ten current workspace captures at compact/wide sizes and two synthetic report
  exports completed without Tk callback errors. Independent checklist/Home
  captures additionally verified actual pointer selection and visible notes.
- The supplied App_DoughFactory configuration archive was reviewed again; the
  generated PDF and its page-bound checks are retained in the evidence folder.
- `git diff --check` passed. The UI skill detector returned no findings; this is
  supplemental to native visual inspection, not proof of accessibility.
- Windows: 145 tests passed with one optional PyMuPDF-inspection skip, followed
  by eight passing final layout checks. Source smoke, exact `build_windows.bat`
  and bundled executable smoke all passed. All 128 snapshot files match the
  final source. `dist/URMotionStudio-reviewed.exe` is the verified artifact;
  receipt and hashes: `reports/app-review-2026-11/windows-verification.json`.

See [verification artifacts](../reports/app-review-2026-11/README.md). No physical
robot was contacted. Tests used local fixtures, synthetic telemetry, mocked
transports and a loopback server. Hardware timing, actual installations and
site-specific safety/maintenance acceptance remain unverified. No commits,
pushes or skill/configuration changes were made by this pass.
