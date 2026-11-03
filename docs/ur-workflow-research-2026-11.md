# UR workflow research and checklist

Research checked 2026-11-02 against official Universal Robots sources and the two PDFs supplied in `docs/`. The app remains an offline analysis and read-only viewing tool; the new checklist performs no robot operations.

## Added: Commissioning & inspection

The missing workflow was a place to retain physical/application review evidence alongside the existing program, flight, log and status analysis. UR's [good installation practices page](https://www.universal-robots.com/download/manuals-e-seriesur-series/installation-guides/good-installation-practices/robot-good-practices-installation-guide-english/) publishes both supplied PDFs and a separate deployment checklist. It also recommends a documented inspection plan suited to the application.

`commissioning.py` provides fourteen review prompts covering installation, configuration, programming, safety review and maintenance. Each prompt has a source and exact PDF page references where applicable. The same catalog is available to the report generators so manual follow-up is consistent with the working checklist.

The native **Checklist** page stores project/robot/reviewer information, review dates, item status and notes. **Not reviewed** is the initial state. **Recorded**, **Action needed** and **Not applicable** are operator-entered states, not automatic pass results. Notes can identify inspection records, measurements, action owners, backup locations or reasons an item is not applicable. The page intentionally does not collect passwords.

Save/open uses a versioned JSON file selected by the user, with bounded input validation and atomic replacement. An incompatible import or failed write retains the existing review. Changes survive page navigation; closing the app, opening another review or starting a new review checks for unsaved edits. CSV export includes identity, every item, notes, guidance, citations and the scope notice. Spreadsheet formula prefixes are escaped. Exporting a CSV does not mark the editable JSON review as saved.

The footer keeps file actions visible in compact windows. The item list and evidence editor sit beside each other; the content scrolls, notes remain attached to their item when selection changes, and filters expose open actions or unreviewed items. The app does not infer inspection intervals or claim that a completed checklist establishes safe operation. Its next-review date is user chosen, not a scheduled notification.

## Sources and application

| Official source | Use in the app |
| --- | --- |
| Supplied **Installation Guide v1.3.3**, PDF pp. 5–12 | Mounting, environment, payload/CoG changes, motion, electrical review, processing/interfaces and application risk-assessment prompts. The guide identifies its parameter values as starting points, not specifications or limits (PDF p. 4). |
| Supplied **UR Application Deployment Workshop, May 2024**, PDF pp. 5–56 | Extends the physical, payload/inertia, motion, singularity and inspection prompts. Page ranges in the code refer to PDF pages, not repeated/misnumbered slide footers. |
| [UR Deployment Checklist Template v1](https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/UR%20Deployment%20Checklist%20Template_v1.pdf), pp. 1–3 | Motivates a durable project/robot record and handover notes. The app uses its own concise prompts, not a reproduced manufacturer form. |
| [e-Series Service Manual: Pre-Use Assessment, SW5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-serv-man/E-series/serv-man-pre-use-assessment.htm) | Links the safety-validation item to the official assessment after installation/modification; users reference qualified validation evidence. |
| [e-Series Service Manual: Inspection Plan, SW5.24](https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-serv-man/E-series/serv-man-inspection-plan-CB.htm) | Provides a reference for the inspection-plan item. Actual schedules and procedures must match the model, controller and conditions. |
| [PolyScope 5 Manual: Log Tab, SW5.22](https://www.universal-robots.com/manuals/EN/HTML/SW5_22/Content/prod-usr-man/software/PolyScope/content/log_g5/log_tab_en.htm) | Supports collecting a Support File and recent incident reports. UR retains five recent error reports; the technical Support File contains configuration, programs, installations and prior failures. |

## Other useful directions

1. **Support bundle inventory:** a future archive viewer could enumerate robot identity, calibration/configuration, program/installation, logs and incidents without extracting unsafe paths or assuming all components exist. Current backup archives and analysis reports are not substitutes for a controller image or the official Support File.
2. **Model/version-aware reference library:** useful when selecting instructions for CB, e-Series, UR Series or PolyScope X. The current checklist explicitly requires the correct manual rather than applying historical numerical limits to every robot.
3. **Multi-robot inspection history:** a future library could link successive user-saved reviews and actions. The current version keeps one portable review file per robot/application and makes no unattended reminder promises.

Robot control, automated safety configuration, firmware flashing and automatic protective-stop reset were not added: these would need separate physical-robot requirements and validation beyond this offline workflow.

## Verification

- Seven domain tests cover source catalog integrity, default unknown state, Unicode evidence/date roundtrips, missing-item normalization, unsupported/lossy imports, malformed/oversized files, write-failure preservation, and CSV evidence/formula handling.
- Five native Tk tests cover notes across selection and file roundtrips, invalid import/canceled save preservation, unsaved discard protection, empty filters, fixed footer visibility at 920×650, actual pointer access to review rows, side-by-side notes, and export retaining dirty state.
- Integrated native screenshots at 1120×740 and 1440×900 are saved in `reports/ur-guidance-2026-11/`. The navigation Connection/Help controls remained visible after layout settled at both sizes. Visual review found and corrected a list stacking issue and moved the notes beside the list.
- Command: `.venv/bin/python -m unittest tests.test_commissioning`; native command: `MOTIONSIM_UI_TESTS=1 MPLCONFIGDIR=/tmp/motionsim-mpl DISPLAY=:0 .venv/bin/python -m unittest tests.test_commissioning_ui`.
- No robot connection or hardware acceptance was performed.
