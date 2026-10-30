# App_DoughFactory report verification

The reviewed input is `tests/full-backup.tar`, read directly without extraction
or executing URScript. The generated report is
[`App_DoughFactory_verified_program_check.pdf`](App_DoughFactory_verified_program_check.pdf).
The original supplied and previously generated PDFs are preserved.

## Verified results

The independent oracle and final generator agree on all **213 velocity,
acceleration and radius values across 71 motion calls**. They also agree on the
17 acceleration findings above the CB/e-Series linear starting recommendation.
Explicit velocity-limit overrides were checked separately for all 61 classic
motion calls. OptiMove fractions are assessed separately from physical speeds.

The matching installation is `/programs/default.installation`, as declared by the
URP. Numeric struct fields now expose the later 4.7/6.3 kg payload definitions and
TCP changes. Initializer substitutions are labelled source estimates: they do not
prove initialization on a particular execution path or actual robot speeds.

Only one move's translation can be established from these sources: the rigid
offset at lines 826–828 is **46.9574 mm**, with 5 mm combined blend radius. The
remaining distances are explicitly unresolved. Branch alternatives, runtime
targets, repetitions and the initial robot pose prevent a valid total executed
travel distance. No active safety-mode velocity limit is inferred from the
installation serialization.

UR describes [800°/s² joint and 2.5 m/s² linear acceleration recommendations](https://www.universal-robots.com/articles/ur/robot-care-maintenance/important-deployment-points/)
as deployment starting values, not specifications or safety limits. The checks
also follow the documented [MoveJ parameter units and duration priority](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/movej_qa14v105t0r.htm)
and [blend geometry](https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-usr-man/software/PolyScope/content/BasicProgNodes/commandtab_way_blend_para.htm).

## Reference PDF comparison

The output retains the supplied PDF's A4 summary, checklist, detailed findings,
teal section headers and footer format. Identical parameter findings are grouped
while preserving every source line. The generated report is 20 pages; the
reference is 10. The extra findings include calculated variables and explicit
coverage and uncertainty. Text bounds, collisions and footer overlap checks pass.

The reference's numerical findings are not reproduced blindly: it double-counts
quoted PolyScope labels and reports false zero-distance/branch blend overlaps.
See [`independent-audit.md`](independent-audit.md) for specific discrepancies.

## Reproduce the checks

Run from the repository root with project dependencies installed:

```sh
python -m unittest tests.test_program_check tests.test_program_motion tests.test_program_archive tests.test_program_backup
```

The supplied-backup tests run only when the private fixture and independent
oracle are present. The final local run passed 35 tests. Three native desktop UI
tests additionally verified archive selection, installation matching, automatic
review, option validation and PDF export.

`expected-values.json` contains the independent oracle;
`generated-findings.json` contains the final report evidence;
`report-verification.json` records test and PDF validation results.
Native Windows results are recorded separately in `windows-verification.json`.

The actual `build_windows.bat` passed on Windows Python 3.14.7 x64 with
PyInstaller 6.22.3 and Tcl/Tk 9.0.4, including the frozen executable's GUI and PDF
smoke test. Native Windows report tests ran 31 tests with one expected skip for
an omitted private configuration fixture. All packaged source hashes match the
workspace. The verified executable is `dist/URMotionStudio.exe`; the previous
build is preserved as `dist/URMotionStudio.previous-b9eb9dba5100.exe`.
