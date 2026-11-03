# Robot incident report: research and analysis contract

Reviewed 2026-10-30. This document separates published guidance from engineering
decisions for MotionSim's offline diagnostic report. The report should explain
what the available records support, what remains uncertain, and which inspection
or measurement would distinguish the remaining explanations. It must not pretend
that a technician inspected the physical cell.

## Source-backed interpretation

| Controller code | Interpretation and useful next investigation | Source / applicability |
| --- | --- | --- |
| C153 | Path tracking discrepancy. Check contact, load settings and motion demand; this is not proof of a mechanical collision. | [UR C153, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_153.html) |
| C157 | Controller collision detection. UR also identifies incorrect settings as a possible explanation. Inspect the physical path, payload, CoG and acceleration. | [UR C157, PolyScope 5.25](https://www.universal-robots.com/manuals/EN/HTML/SW5_25/Content/prod-err-codes/topics/CODE_157.html) |
| C158 | Collision detection with zero configured payload. Establish whether a tool/part was actually attached before calling the setting wrong. | [UR C158, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_158.html) |
| C159 | Path discrepancy with zero configured payload. Preserve both facts rather than reducing this to a generic payload warning. | [UR C159, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_159.html) |
| C162 | Controller identifies payload/CoG configuration as a likely contributor, not a measured mass. | [UR C162, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_162.html) |
| C163 | Repeated protective stops require resolution. Application problems and joint problems are possible; a count does not diagnose a failed joint. | [UR C163, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_163.html) |
| C174 | Abrupt torque demand; examine target acceleration changes and motion transitions. Appropriate blends or reduced acceleration are investigation options. | [UR C174, PolyScope 5.19](https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-err-codes/topics/CODE_174.html) |
| C154 | Controller singularity report. Review configuration and whether Cartesian path requirements can be changed. Do not infer this code merely from a velocity peak. | [UR C154, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_154.html) |

C153/C157/C158/C159/C163 subtypes A0–A5 identify Base through Wrist 3.
C174 A0–A5 use that order; A6–A11 use the same joints with a program-line
argument. Other code families use different subtype meanings: never apply a
global `A modulo 6` rule. Keep the original code, source, subtype and message.
[UR C174, PolyScope 5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/CODE_174.html)

The current [UR Error Codes Directory](https://www.universal-robots.com/manuals/latest/en/pdf/shared/error-codes/)
resolved to edition 5.26 on the research date. Its section structure separates
earlier and later models, and some current pages omit explanatory text present
in earlier editions. Retain the recording's software version and link explicit
versions for rules; do not silently replace the historical meaning with a
different firmware's message. Unknown codes remain visible for manual review.

UR's [protective-stop guidance](https://www.universal-robots.com/articles/ur/robot-care-maintenance/understanding-protective-stops/)
(modified October 6, 2026) treats recurring stops as something to investigate.
It names installation/load configuration, contact, cabling and demanding motion
among possible causes. Validate TCP, mounting, payload and CoG against the real
cell. If the application checks do not explain recurrence, arrange qualified
service review and retain logs, programs, installation and recordings. The
report should never recommend ignoring stops or automatically resetting them.

UR's [singularity explanation](https://www.universal-robots.com/articles/ur/application-installation/what-is-a-singularity/)
(July 17, 2018) distinguishes extended reach, the central base-axis region and
wrist alignment. A slow Cartesian move can require rapid joint motion near the
base. A path/tool-orientation change or a joint-space move can be an option when
the process permits it. Offline pose evidence supports an investigation, not a
clearance or safety validation.

## Specifications are conditional, not one universal table

Use the model, generation, actual firmware, installed configuration and source
units together. Record which limits are available and what was not checked.
Do not infer a current limit from a hardware torque table or invent a fixed
current-to-torque conversion. [UR's maximum torque article](https://www.universal-robots.com/articles/ur/robot-care-maintenance/max-joint-torques-cb3-and-e-series/)
explicitly distinguishes joint capability from usable application torque and
the configured safety-force constraints. Its table covers named CB3/e-Series
models; do not extrapolate it to every newer arm.

[Important Deployment Points](https://www.universal-robots.com/articles/ur/robot-care-maintenance/important-deployment-points/)
labels its suggested installation values as starting points, not specifications
or limits. Consequently, the commonly cited joint acceleration 800 degrees/s²
and linear acceleration 2.5 m/s² must not become automatic failure thresholds.
They can inform a clearly labelled review recommendation only.

Even product-sheet wording changes across editions. The UR10e
[5.19 specification](https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-usr-man/complianceUR10e/H_g5_sections/appendix_g5/tech_spec_sheet.htm)
lists an approximate 1 m/s tool speed, while the
[5.25.1 manual](https://www.universal-robots.com/manuals/EN/PDF/SW5_25_1/user-manual-UR10e-PDF_online/711-039-00_UR10e_User_Manual_en_Global.pdf)
lists approximately 4 m/s maximum TCP speed. Neither is a substitute for the
application's safety configuration. The useful deliverable is conditional,
traceable comparison, not a claim to have checked every UR specification.

## Correlation and numerical rules (MotionSim engineering decisions)

1. Preserve source identity and coverage. Inventory accepted/rejected inputs,
   serial/model/software metadata when available, date coverage, missing
   recordings and unknown code families. Identical copied files are not new
   events. Deduplicate overlapping rotated-log records using their identity and
   full message, while retaining provenance. A repeated real event stays real.
2. Build incidents from nearby, compatible messages. Keep the initiating warning
   and consequences together without converting each joint message into a new
   physical collision. Group only within the same known robot/session; do not
   combine distinct program executions solely because they share a line number.
3. Distinguish association from precise alignment. Summary incident wall time
   may associate a flight with a log event. It does not locate that event in the
   recording's uptime axis. Filename timestamps and file modification times are
   retrieval/creation hints, never a sufficient sample anchor. Require an
   explicit controller timestamp or recording event marker for exact placement.
   Keep competing candidate flights ambiguous, and do not guess across reboots,
   differing serials, unknown zones or clock changes.
   UR summary timestamps in the weekday/month/time/zone/year format are parsed
   with explicit recognized offsets. Advanced settings optionally supply a log
   UTC offset for otherwise naive log timestamps; it is recorded in coverage
   and never replaces an already explicit timezone. The operator must verify
   the offset for the incident date, including daylight-saving changes.
   A uniquely associated log's explicit uptime can locate its event only when
   both sources carry the same nonempty robot serial, the burst agrees on one
   uptime/session, and the timestamp lies in the recording without a data gap.
4. A sample alignment must state its anchor, tolerance and temporal precision.
   If alignment is absent, describe full-recording observations as such; never
   label the largest current sample as the collision moment. Flag a stop already
   active at recording start because its onset is outside available coverage.
5. Compute only on finite, increasing timestamps in contiguous sample segments.
   Do not differentiate/interpolate across gaps, duplicate times or controller
   resets. Report derivative provenance: recorded target acceleration differs
   from acceleration estimated from measured velocity. Peak deceleration after
   a stop may be its consequence; it is not automatically a preceding cause.
6. At an aligned event, describe actual/target position tracking, measured and
   commanded velocity, acceleration provenance, and current relative to explicit
   recorded bounds, if present. Bound comparison requires finite actual and
   lower/upper values with lower ≤ upper. Say “outside recorded current window,”
   not “above hardware rating.” Preserve polarity and units. A missing channel
   is unknown, never zero or a passed check.
7. Show a short lead-up/event/recovery sequence and source measurements. Compare
   with earlier samples only if they are continuous and represent a relevant
   baseline; no arbitrary percent increase should be a machine-failure rule.
   A normal trace with insufficient sample rate does not rule out brief contact.
8. Preserve script context but avoid false causal statements. A sampled controlling
   script line provides execution context; it need not be a PolyScope tree line
   or the statement that caused the fault. Use only the matching captured script.
   PDF traces recheck the analyzed file's SHA-256; changed files require a new
   analysis instead of silently supplying different chart evidence.
9. Record each conclusion as observed, plausible, or unresolved. Give the evidence,
   alternative explanations and a discriminating next check. Reserve “confirmed”
   for recorded controller facts, not unseen mechanical damage or contact.

The clock/channel foundation is UR's [RTDE Guide](https://docs.universal-robots.com/tutorials/communication-protocol-tutorials/rtde-guide.html):
timestamp is time since controller start; actual and target quantities are
different fields, field availability varies with software, and output packages
can be skipped. Its controlling-script-line definition also differs from the
PolyScope program tree. These facts motivate, but do not themselves prescribe,
the conservative association policies above.

External research supports that caution. The original 2021 Mechatronics paper
[A nonlinear momentum observer for sensorless robot collision detection under model uncertainties](https://doi.org/10.1016/j.mechatronics.2021.102603)
describes identification and an observer rather than diagnosing collisions from
one current peak. Its accessible publisher abstract discusses measurement noise
and uncertain dynamics; full text was not available in this review. MotionSim
does not implement or claim validation of that observer. This is methodological
support for distinguishing signal evidence from physical root cause.

## Document structure and reusable tools

Lead with a short assessment: the most important observed incident pattern, the
strongest supported interpretation, and the most useful next checks. Then give
data coverage and correlation confidence. Each incident should read as a small
case study: context, sequence, evidence/chart, plausible contributors, competing
explanations, and next actions. Finish with prioritized actions, unresolved
questions, source references and an evidence appendix. Counts and line numbers
belong in supporting detail, not in place of the assessment. Collapse duplicate
recommendations, but retain each distinct occurrence and its measurements.

Public-source/repository search found these relevant existing components:

| Component | Scope and decision |
| --- | --- |
| Existing `log_parser.py`, `flight_data.py`, transfers and PDF helpers | Extend these local, bounded readers. They already preserve useful channels and avoid new packaging dependencies. |
| [UR Log Viewer manual](https://www.universal-robots.com/articles/ur/robot-care-maintenance/ur-log-viewer-manual/) | Reference workflow/tool: log history, support files, flight records and joint graphs. Manual describes v1.3.5.0; it is a separate Windows/.NET application, not an embedded Python analysis API. |
| [UR Log Viewer release notes](https://www.universal-robots.com/articles/ur/release-notes/ur-log-viewer-release-notes/) | August 19, 2026 update describes v1.4.1.0 support for newer flight formats. Recorded script-line support starts at PolyScope 5.9/3.14; absence in older captures is expected. |
| [UR RTDE Python client](https://github.com/UniversalRobots/RTDE_Python_Client_Library) | Manufacturer BSD-3-Clause realtime client with recording/CSV examples. Useful for future controlled captures, not an incident interpretation engine; no new dependency needed here. |
| [UR ROS2 Dashboard client](https://github.com/UniversalRobots/Universal_Robots_ROS2_Driver/blob/main/ur_robot_driver/doc/dashboard_client.rst) | Manufacturer collection interface reference, but introducing ROS solely for an existing desktop SSH downloader is unnecessary. |
| [UR support-file instructions](https://www.universal-robots.com/articles/support-file-for-technical-support/) | Supports escalation with original context. Export contents/version availability differ; a log-only collection must not be described as a complete support file. |

The review used repository search, official web documentation, public GitHub
and an original research-paper publisher abstract. It was not an exhaustive
package-registry audit. No suitable reusable local narrative diagnostic engine
was established. Build the thin deterministic interpretation layer over existing
parsers; keep source files and report computation local. No external language
model, uploaded logs, generated robot motion, safety-setting modification or
automatic stop acknowledgement is required.
