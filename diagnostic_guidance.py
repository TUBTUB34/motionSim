"""Versioned, source-backed troubleshooting prompts for offline reports.

These are investigation paths, not a diagnosis or a controller limit model.
Verified 2026-11-02; applicability and analysis rules are documented in
docs/robot-diagnostic-research.md and docs/ur-report-guidance-2026-11.md.
No robot commands or network calls occur here.
"""

import re
from collections.abc import Iterable


ERROR_BASE = "https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/"
PROTECTIVE_STOP = "https://www.universal-robots.com/articles/ur/robot-care-maintenance/understanding-protective-stops/"
DEPLOYMENT = "https://www.universal-robots.com/articles/ur/robot-care-maintenance/important-deployment-points/"
SINGULARITY = "https://www.universal-robots.com/articles/ur/application-installation/what-is-a-singularity/"
ERROR_DIRECTORY = "https://www.universal-robots.com/manuals/latest/en/pdf/shared/error-codes/"


def guidance_for_codes(codes: Iterable[str]) -> dict:
    """Return deduplicated investigation guidance for Cxxx or CxxxAyy codes.

    A mixed incident retains each relevant investigation path. Unknown codes
    receive an explicit manual-lookup action rather than an invented diagnosis.
    The caller adds measured evidence and decides whether a hypothesis is
    supported; code presence alone must not be described as physical proof.
    """
    numbers = set()
    blend_subtypes = set()
    for code in codes:
        match = re.fullmatch(r"C(\d+)(?:A(\d+))?(?::\d+)?", str(code).strip(), re.I)
        if match:
            numbers.add(int(match.group(1)))
            if int(match.group(1)) == 171 and match.group(2) is not None:
                blend_subtypes.add(int(match.group(2)))
    titles, hypotheses, actions, counterevidence, references = [], [], [], [], []

    if numbers & {153, 157, 158, 159}:
        titles.append("Investigate contact, load settings and motion demand")
        hypotheses.append(
            "Contact with the workpiece, fixture or external cabling, an inaccurate load model, "
            "or demanding motion could explain the controller's path-following or collision warning."
        )
        actions.extend((
            "Inspect the recorded robot pose and the surrounding fixtures, workpiece and cable routing for contact; "
            "compare the approach and retract waypoints with the real setup.",
            "Verify payload mass, center of gravity, TCP and mounting against the actual tool and carried part; "
            "check that payload changes occur at pickup and release.",
            "Review the motion immediately before the stop, including acceleration changes and blends. "
            "Validate any revised path under the application's approved operating procedure.",
        ))
        counterevidence.append(
            "A controller collision warning does not establish physical contact. Current and tracking deviations "
            "alone cannot distinguish a collision from incorrect settings or prove joint damage."
        )
        references.append(PROTECTIVE_STOP)

    if numbers & {158, 159}:
        hypotheses.append(
            "This code records a configured payload of zero; if a tool or part was attached, the load model may be incomplete."
        )
        actions.append("Confirm whether zero payload was appropriate at this incident, including the end-effector mass and center of gravity.")
        counterevidence.append("Zero configured payload is not itself proof of an error; confirm the physical load at the time.")

    if 162 in numbers:
        titles.append("Verify the load model")
        hypotheses.append("The controller identified payload mass or center of gravity as a likely contributor.")
        actions.append("Compare the incident's active payload and center of gravity with measured tool and workpiece values, including pickup/release changes.")
        counterevidence.append("The controller's payload assessment is a likely cause, not an independent measurement of the attached load.")

    if 163 in numbers:
        titles.append("Resolve recurring protective stops")
        hypotheses.append("Repeated stops indicate an unresolved application issue or a joint problem requiring investigation.")
        actions.append(
            "Address recurring stops before returning to routine operation. Check contact, payload, center of gravity and motion settings; "
            "if these are verified and stops persist, provide the support files to qualified UR service personnel."
        )
        counterevidence.append("The repeated-stop code does not identify a failed component, and a partial log cannot reproduce the controller's full stop counter.")

    if numbers & {173, 174}:
        titles.append("Review trajectory torque demand")
        hypotheses.append(
            "The controller reported excessive torque demand or an abrupt torque change; changes in commanded acceleration "
            "and transitions between moves are relevant investigation points."
        )
        actions.append(
            "Examine the preceding and following moves, target acceleration and blend transitions. "
            "Review lower acceleration or a suitable blend while preserving required path clearance and process stops."
        )
        counterevidence.append("A torque-demand warning does not by itself demonstrate a damaged joint or an external collision.")
        references.append("https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-err-codes/topics/CODE_174.html")

    if 171 in numbers:
        titles.append("Review the reported blend configuration")
        hypotheses.append("The controller reported a blend issue. The exact subtype distinguishes an undersized blend from a waypoint skipped by an overlapping blend.")
        if blend_subtypes & {1, 10}:
            actions.append("For the C171A1 or C171A10 small-blend warning, review the process path and an appropriate larger radius while preserving clearance and required accuracy.")
        if blend_subtypes & {0, 3, 4, 5, 6, 7, 9, 11, 12, 13, 14}:
            actions.append("For the C171 skipped-waypoint subtype, inspect the named move and adjacent waypoints; review a smaller radius or more waypoint separation, then validate the resulting path.")
        actions.append("Retain the complete C171 subtype and captured program; compare the controller's warning with the actual motion version and executed branch.")
        counterevidence.append("C171 alone does not establish an overlapping blend, and static geometry alone cannot prove a skipped waypoint on every motion version.")

    if numbers & {150, 151, 152}:
        titles.append("Review motion margin to configured limits")
        hypotheses.append("The controller reported proximity to a joint, tool-orientation or safety-plane limit; review the exact code and active configuration.")
        actions.append("Compare the recorded pose and approach path with the validated joint, tool-orientation and safety-plane limits. Preserve the risk-assessed limits and establish suitable path margin.")
        counterevidence.append("These codes do not establish a mechanical joint defect or justify relaxing a safety limit.")

    if numbers & {153, 157, 158, 159, 171, 173, 174}:
        actions.append("For supervised troubleshooting, the 2024 UR deployment workshop (PDF pp. 58–61) describes position-deviation logging and pause_on_error_code. Confirm controller support and have the responsible programmer enable diagnostics; this report does not change the robot.")

    if 154 in numbers:
        titles.append("Review the path near a singular configuration")
        hypotheses.append("The controller reported a singularity; the requested Cartesian motion may require excessive joint motion in this configuration.")
        actions.append(
            "Review the pose near the base axis, extended reach and wrist alignment. "
            "Consider changing tool orientation or the path; a joint-space move may be suitable where a straight TCP path is not required."
        )
        counterevidence.append("A joint-speed spike alone is insufficient to diagnose a singularity without pose and kinematic context.")
        references.append(SINGULARITY)

    known = {150, 151, 152, 153, 154, 157, 158, 159, 162, 163, 171, 173, 174}
    if not numbers or numbers - known:
        actions.append("Review the complete error message and subtype in the manual for this robot model and software version; retain the original support files for service review.")
        references.append(ERROR_DIRECTORY)
    if not titles:
        titles.append("Review the recorded controller fault")
        counterevidence.append("This report has no validated cause-specific rule for the recorded code; no physical root cause is inferred.")
    references.extend(f"{ERROR_BASE}CODE_{number}.html" for number in sorted(numbers & known))

    def unique(values):
        return tuple(dict.fromkeys(values))

    return {
        "title": "; ".join(unique(titles)),
        "hypotheses": unique(hypotheses),
        "actions": unique(actions),
        "counterevidence": unique(counterevidence),
        "references": unique(references),
    }
