# PDF report audit, 2026-11-12

All three customer-facing PDFs were generated from real data, every page was
rendered to PNG and read, then fixed. `before-*.png` are the previous output,
`after-*.png` the current output (same inputs).

Inputs: `assets/flightreports/recording20261013_19_12_20.zip` (UR10e, 30 s at
500 Hz, protective stop at 24.42 s); a live recording made through
`live_recorder.LiveCapture` from the same data; a two-sample CSV; the
App_DoughFactory program from `robot_configuration20261014_19_16_56.zip`; and
`log_history.txt` with the supplied recordings for Robot Status.

## Flight PDF (`flight_pdf.py`)

**Before:** titled "Program Check Report", with "Overall result: Not assessed", a
"Compliance Status" table and a boilerplate checklist, left over from the Program
Check template. Rows of "Not recorded" were printed for missing data. Angles were
in radians. The x axis was controller uptime, and the 5.4 pt footer was hard to read.

**After:** a **Flight Recording Report** in 9 pages:

1. Recording identity, then **What the recording shows**: stops, safety/robot/program
   state changes, the highest speed, current and tracking error, and the lowest
   speed scaling. Each item comes straight from a recorded channel.
2. **Joint statistics**: a table for all six joints, each peak with its time.
3. **One chart page per joint**: position, speed, acceleration, current and torque,
   actual solid and target dashed, protective stops shaded. Angles are in degrees
   and time in seconds from the first sample.
4. **Data-quality notes**: derived quantities, gaps, missing channels.

Empty sections are omitted, so a live recording or a bare CSV reads cleanly. The
report gives no verdict and infers no limits. Derived values are labelled, and
missing channels are named as unavailable, never shown as zero.

## Program Check and Robot Status PDFs

All three reports now share one page frame:

- Header: the report name and the subject (file or robot).
- Footer: a readable disclaimer, the generation date and "Page N of M".

Report-specific changes:

- **Program Check:** the first page is clearer ("Program at a glance", "Start
  here"). The local folder of a reviewed file is no longer printed.
- **Robot Status:** the appendix lists each source file once, with line ranges,
  relative to the evidence folder instead of the operator's local path.

## Verification

- `tests.test_flight_pdf`, `tests.test_program_check_pdf`,
  `tests.test_diagnostic_pdf` and `tests.test_robot_status_pdf` all pass.
- New tests cover:
  - no verdict wording;
  - degree conversion;
  - omitted empty sections;
  - findings that never name a cause;
  - page numbering;
  - no local paths.
- The full suite passes: 498 tests with `MOTIONSIM_UI_TESTS=1`.

## Not verified

- The PDFs were not printed on paper.
- They were not opened in Adobe Reader or other viewers; rendering was checked
  with PyMuPDF only.
- They were not reviewed by a customer.
