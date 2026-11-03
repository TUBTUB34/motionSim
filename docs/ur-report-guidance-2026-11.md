# UR document coverage in reports

Reviewed 2026-11-02. The two supplied PDFs are now explicit report references,
and their physical review topics share the catalog used by the deployment
checklist. Page numbers below are **PDF page numbers**, not printed slide or
page labels. The Installation Guide's printed pages are one less than the PDF
pages; several Workshop slides also have inconsistent printed numbers.

The Installation Guide v1.3.3 covers the CB/e-Series models listed on its cover.
The May 2024 workshop describes e-Series and newer robots available at that
time. Neither document is treated as the latest specification for every model.
Model-specific manuals and the application risk assessment take precedence.

| UR recommendation | Source | App implementation and evidence boundary |
| --- | --- | --- |
| Confirm payload, CoG and TCP; update the load when a part changes | Guide p. 6; Workshop pp. 20–28 | Existing Program Check source/installation values and pickup/release review prompts retained. Call counts never establish that every executed pickup changes the payload. |
| Review payload transition time and inertia | Workshop pp. 25, 29–30 | New **Payload transition and inertia** category. Displays transition seconds, flags negative literal values, and requests physical verification for default/zero inertia. Nonzero transition does not wait for the change to finish. Zero inertia does not automatically mean a fault. |
| Prefer smooth paths and suitable blend sizes before increasing acceleration | Guide pp. 7–8; Workshop pp. 33–39 | Existing acceleration and geometric overlap checks retained. Literal nonzero MoveJ/MoveL radii below 15 mm produce a contextual manual prompt in a confirmed guidance profile. Deliberate zero-radius stops, MoveP process radii, unresolved values and unconfirmed families do not receive this prompt. |
| Avoid interrupted process motion and blends | Guide p. 7; Workshop pp. 34–36 | New **Process motion and waits** category. MoveP prompts cover constant-speed need, blocking I/O/gripper calls and a controlled stop before a required wait. Source order does not establish runtime control flow. |
| Account for motion version | Workshop p. 35; current UR motion-version manual | Existing Motion Version 2 caveat retained: static overlap does not prove a skipped waypoint because planning can change radii. |
| Review thread load, yielding, connection churn and unnecessary URCaps | Guide pp. 10–11 | New **Diagnostics and program efficiency** category. Socket open/close source evidence prompts review of connection lifetime; no runtime frequency is inferred. The report asks for loop/thread review without treating a sync elsewhere in the file as proof that every loop yields. |
| Use diagnostic instrumentation during supervised troubleshooting | Workshop pp. 58–61 | Program Check records `position_deviation_warning` and `pause_on_error_code` occurrences or suggests optional investigation. The workshop's 0.4 threshold is identified as a fraction of controller stop deviation, not millimetres or a safety threshold. Commands are never sent to a robot. |
| Interpret the complete C171 blend subtype | Workshop pp. 35, 61; UR C171 manual | Diagnostic guidance and Robot Status identify blend review. C171A1/A10 prompts concern a small radius; documented skipped-waypoint subtypes concern overlap/radius/spacing. A family-only code cannot establish overlap. |
| Maintain path margin from joint, orientation and safety-plane limits | Workshop p. 48; UR C150–152 manual entries | Diagnostic guidance and Robot Status identify a targeted program review area. They preserve risk-assessed limits and do not infer a damaged joint. |
| Inspect mounting, environment, electrical setup, safety validation and maintenance | Guide pp. 5, 9, 12; Workshop pp. 5–18, 54–56; applicable service manual | Program Check and Robot Status PDFs include a compact **Physical installation follow-up** page from the shared commissioning catalog. Every item is explicitly unassessed by these reports; users record on-site evidence in the full deployment checklist. |
| Investigate protective stops and retain evidence | Guide p. 11; Workshop pp. 50–52, 58–63 | Existing physical-clearance, load, motion and qualified-service recommendations retained; targeted diagnostic suggestions added. Neither a reset nor an absence of supplied faults establishes resolution. |

The acceleration comparisons remain **guidance starting values**, not safety or
product limits: 800 degrees/s² is converted to radians/s² for joint commands;
2500 mm/s² becomes 2.5 m/s² for linear commands. Existing time-priority,
OptiMove-fraction, unresolved-runtime-value and supplied-specification handling
remains in place. No new model-independent current, temperature, lifetime or
payload-rating limit was invented.

Program Check appends three categories to its existing nine, preserving existing
category positions and grouped line evidence. Its source lexer now recognizes
the relevant diagnostic, stop, wait and socket functions while continuing to
ignore comments, quoted strings, PolyScope labels and function declarations.
The full short manual follow-up page is also included when only a URP is
provided, without pretending that the generated script was reviewed.

Robot Status remains an assessment of supplied records, with uncertain identity
and incomplete evidence visible. Physical checklist prompts do not affect joint
ratings. Flight PDF exports remain recorded measurements and metadata; they do
not score installation practices or convert a workshop example into a robot
limit.

## Sources checked

- [Supplied Installation Guide v1.3.3](Installation%20Guide%20V1.3.3_ENGLISH.pdf).
- [Supplied May 2024 Deployment Workshop](UR%20Application%20Deployment_Workshop_2024.pdf).
- [UR payload API](https://www.universal-robots.com/manuals/EN/HTML/SW10_12/Content/prod-scriptmanual/all_scripts/set_target_payload.htm): transition and inertia semantics; use the matching controller version when applying a change.
- [UR position-deviation logging](https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-scriptmanual/G5/position_deviation_warning.htm): threshold units and runtime logging.
- [UR pause-on-error API](https://www.universal-robots.com/manuals/EN/HTML/SW5_26/Content/prod-scriptmanual/all_scripts/pause_on_error_code%28code%2Cargument%29.htm): code/argument semantics and reset after program stop.
- [UR C171](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_171.html): distinction between small and overlapping blends.
- [UR C150](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_150.html): joint-limit proximity; its official error-directory navigation also identifies C151 tool orientation and C152 safety-plane proximity.

## Verification

Regression coverage includes small-blend exceptions, unknown-family behavior,
payload transition values, default-inertia uncertainty, comment/string masking,
diagnostic source attribution, C171 subtype separation, configured-limit
preservation, program-review areas without invented joint faults, and PDF
content/pagination/bounds. The seven report/motion/guidance/status suites pass
71 tests in the repository Python environment. A synthetic Robot Status report
remains five pages, including the new single-page physical follow-up, which was
also visually inspected. The supplied App_DoughFactory archive produces 315
findings grouped into 79 entries across 19 pages; all text remains within the
page bounds and outside the footer. No robot connection, script execution or
safety setting change is part of this verification.
