"""Client-readable condition review from structured, local diagnostic evidence.

Status labels describe supplied history, not a physical inspection, a remaining
life prediction or permission to operate. Numeric observations never create an
invented model-independent acceleration, temperature or current limit.
"""
from dataclasses import dataclass, field
import re

from flight_data import JOINTS
from log_parser import CODES, HEALTH_CODES
from robot_diagnostics import DiagnosticReport, _date

JOINT_MOTION_REVIEW = ('Review {joints} at the affected motion stage: check physical clearance, '
                       'active payload and centre of gravity, then acceleration changes and move transitions.')


@dataclass
class JointStatus:
    joint: str = ''
    status: str = 'Not assessed'
    summary: str = ''
    evidence: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    incident_ids: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class ProgramArea:
    program: str = ''
    area: str = ''
    priority: str = 'Review'
    summary: str = ''
    evidence: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    script_lines: list[int] = field(default_factory=list)
    incident_ids: list[str] = field(default_factory=list)
    association: str = ''


@dataclass
class RobotStatusReport:
    source: str = ''
    overall_status: str = 'Insufficient evidence'
    assessment: list[str] = field(default_factory=list)
    joints: list[JointStatus] = field(default_factory=list)
    program_areas: list[ProgramArea] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    coverage: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    references: list[dict[str, str]] = field(default_factory=list)
    robot_details: list[tuple[str, str]] = field(default_factory=list)
    diagnostic_report: DiagnosticReport | None = None


def _unique(items):
    return list(dict.fromkeys(items))


def _family(code):
    match = re.match(r'C\d+', code)
    return match.group() if match else ''


def _known_joint_code(code):
    return _family(code) in CODES or code in HEALTH_CODES or _family(code) in HEALTH_CODES


def _fault_reason(codes):
    families = {_family(code) for code in codes}
    reasons = []
    if families & {'C153', 'C157', 'C158', 'C159'}:
        reasons.append('collision detection or path-following warnings')
    if families & {'C173', 'C174'}:
        reasons.append('abrupt torque-demand warnings')
    if 'C163' in families:
        reasons.append('repeated protective stops')
    for code in codes:
        if _family(code) in {'C163', 'C174'}:
            continue
        rule = HEALTH_CODES.get(code) or HEALTH_CODES.get(_family(code))
        if rule:
            reasons.append(rule[0].lower())
    return ', '.join(_unique(reasons)) or 'joint-specific controller warnings'


def _joint_status(joint, report, incidents):
    status = JointStatus(joint=joint)
    known, unknown, measurements, codes = [], [], [], []
    for incident in incidents:
        # Structured per-joint mapping prevents a mixed fault burst from assigning
        # every code to every joint. Older externally built Incident objects can
        # only use their codes when they name exactly one joint.
        joint_codes = incident.joint_codes.get(joint, [])
        if not incident.joint_codes and incident.joints == [joint]:
            joint_codes = incident.codes
        if joint_codes:
            codes.extend(joint_codes)
            (known if any(_known_joint_code(code) for code in joint_codes) else unknown).append(incident)
        for measured in incident.joint_evidence:
            if measured.joint == joint and measured.channels:
                measurements.append((incident, measured))
    codes = _unique(codes)
    relevant = _unique(incident.id for incident in [*known, *unknown] + [i for i, _ in measurements])
    status.incident_ids = relevant
    if codes:
        status.evidence.append('Controller records: ' + ', '.join(codes) + '.')
    excursions = [(i, m) for i, m in measurements if m.current_excursion_samples]
    channels = _unique(channel for _, measured in measurements for channel in measured.channels)
    if measurements:
        recordings = _unique(m.flight_name for _, m in measurements)
        status.evidence.append(f'Available recorded channels: {", ".join(channels)} across {len(recordings)} recording(s).')
        if excursions:
            status.evidence.append('Recorded current crossed an available controller bound in ' +
                                   ', '.join(_unique(i.id for i, _ in excursions)) + '. This is not a hardware current rating.')
        velocities = [m.metrics['peak_velocity'] for _, m in measurements if 'peak_velocity' in m.metrics]
        if velocities:
            status.evidence.append(f'Highest observed absolute joint speed in reviewed windows: {max(velocities):.4g} rad/s; no universal speed limit was assumed.')
        tracking = [m.metrics['position_tracking_error'] for _, m in measurements if 'position_tracking_error' in m.metrics]
        if tracking:
            status.evidence.append(f'Largest observed position difference between actual and target: {max(tracking):.4g} rad; this alone does not diagnose wear or contact.')
        temperatures = [m.metrics['peak_temperature'] for _, m in measurements if 'peak_temperature' in m.metrics]
        if temperatures:
            status.evidence.append(f'Highest recorded joint temperature in reviewed windows: {max(temperatures):.4g} °C; a model-specific thermal limit was not evaluated.')
    if known:
        status.status = 'Needs attention'
        associated = [m for i, m in measurements if any(i is case for case in known)]
        status.summary = f'The controller recorded {_fault_reason(codes)} for {joint}. '
        if any(not i.association_verified for i, _ in measurements if any(i is case for case in known)):
            status.summary += 'The recording link is provisional because source robot identity is unverified. '
        if any(m.current_excursion_samples for m in associated):
            status.summary += 'The associated recording also contains current outside recorded bounds; review the load and motion context. '
        elif not associated:
            status.summary += 'No matched joint telemetry was available to corroborate the warnings. '
        elif any(m.current_both_bound_samples for m in associated):
            status.summary += 'No current-bound excursion was found in the associated measurements; incomplete coverage can still conceal a transient. '
        else:
            status.summary += 'Associated motion readings provide context, but comparable current bounds are unavailable. '
        status.summary += 'These findings do not establish that the joint is damaged.'
        if len(known) > 1:
            status.summary += ' The concern recurs; confirm whether corrective work has addressed it.'
        families = {_family(code) for code in codes}
        if families & {'C153', 'C157', 'C158', 'C159', 'C163', 'C174'}:
            status.recommendations.append(JOINT_MOTION_REVIEW.format(joints=joint))
        if any((code in HEALTH_CODES or _family(code) in HEALTH_CODES) and _family(code) not in {'C163', 'C174'} for code in codes):
            status.recommendations.append(f'Have qualified service personnel review {joint} with the complete controller message, robot model/software and original support files; a fault code alone cannot establish the required repair.')
        if not measurements:
            status.limitations.append('No corresponding usable joint telemetry was available to examine the motion around these warnings.')
    elif excursions:
        status.status = 'Review motion evidence'
        status.summary = f'{joint} has a recorded current-bound excursion that merits a load and motion review. No validated joint-specific fault was associated with it; the trace is not a diagnosis of a failed joint.'
        status.recommendations.append(f'Check the tool/workpiece load, clearance and motion transition where {joint} current crosses the recorded bound; verify timing before connecting it to a logged stop.')
    elif unknown:
        status.status = 'Limited evidence'
        status.summary = f'{joint} appears in controller messages that this review cannot reliably classify. Confirm the full message and software-specific meaning before assigning a condition.'
        status.recommendations.append('Interpret the original controller message using the applicable UR manual or service support.')
    elif (report.log_source_count and measurements and all(
            {'position', 'velocity', 'current'}.issubset(m.channels)
            and m.current_both_bound_samples == m.sample_count and m.sample_count > 0
            and not m.has_gaps for _, m in measurements) and not report.warnings):
        status.status = 'No recorded concern'
        status.summary = f'No mapped joint-specific warning or recorded current-bound excursion was identified for {joint} in the supplied evidence. Motion and current channels were available; this does not establish present mechanical condition or future reliability.'
    elif measurements:
        status.status = 'Limited evidence'
        status.summary = f'Some {joint} measurements are available, but they do not provide enough coverage to characterize its condition. No mapped joint-specific warning was found in the supplied evidence.'
    elif report.log_source_count:
        status.status = 'Limited evidence'
        status.summary = f'No mapped warning naming {joint} was found in the supplied logs. Without usable joint telemetry or inspection evidence, its mechanical condition remains unassessed.'
    else:
        status.summary = f'No usable measurements or mapped controller records were available for {joint}. Its condition cannot be determined from this dataset.'
    if not measurements:
        status.limitations.append('Joint telemetry unavailable.')
    elif any(m.scope == 'Recording-level' for _, m in measurements):
        status.limitations.append('Some measurements describe an entire recording; an exact fault sample was not established.')
    if any(m.has_gaps for _, m in measurements):
        status.limitations.append('Reviewed measurements contain gaps; short transients may be missing.')
    if any(i.codes and i.flight_names and not i.association_verified for i, _ in measurements):
        status.limitations.append('A log-to-recording match is provisional because source robot identity could not be verified in both files.')
    if not any(m.current_comparable_samples for _, m in measurements):
        status.limitations.append('No comparable recorded current bounds were available; no current-limit check was made.')
    if not report.log_source_count:
        status.limitations.append('Controller log coverage is unavailable.')
    return status


def _program_topics(incident):
    families = {_family(code) for code in incident.codes}
    topics = []
    if families & {'C153', 'C157', 'C158', 'C159'}:
        topics.append(('Approach path and clearance',
            'The controller reported collision detection or a path-following discrepancy. The approach, retract path and external cabling are relevant review areas, together with the active load model.',
            'Inspect the real cell at the recorded approach/retract stage and compare fixture, workpiece and cable clearance with the intended path.'))
    if families & {'C158', 'C159', 'C162'}:
        topics.append(('Payload changes at pickup and release',
            'The controller warning makes payload configuration relevant. Verify the configured tool and carried part rather than assuming a zero or changed payload is incorrect.',
            'Check payload mass and centre-of-gravity updates at pickup/release against the tool and workpiece actually attached at that stage.'))
    if families & {'C173', 'C174'}:
        topics.append(('Move transitions and blending',
            'The controller reported abrupt or excessive torque demand. Review the transition between moves and commanded acceleration before treating this as a hardware problem.',
            'Inspect acceleration changes, blend settings and required stops around the recorded motion. Validate any change against clearance and process requirements.'))
    if 'C171' in families:
        topics.append(('Blend configuration and waypoint continuity',
            'The controller reported a blend issue. Use the full subtype: some C171 messages report a blend that is too small, while others report skipped waypoints.',
            'Review the named waypoint, neighboring radii and executed branch using the complete C171 message. Adjust radius or spacing only after checking the required process path and clearance.'))
    if families & {'C150', 'C151', 'C152'}:
        topics.append(('Path margin to configured limits',
            'The controller reported proximity to a joint, tool-orientation or safety-plane limit. This is application context, not proof of a component defect.',
            'Compare the recorded pose and path against the validated installation limits and provide suitable margin. Preserve the limits established by the application risk assessment.'))
    if 'C154' in families:
        topics.append(('Path near singular configurations',
            'The controller reported a singularity. The path and tool orientation need review in the recorded configuration.',
            'Review the path near extended reach, the base axis and wrist alignment; assess whether the process permits a different tool orientation or trajectory.'))
    if 'C163' in families:
        topics.append(('Recurring stopping stage',
            'Repeated protective stops were reported. Identify whether the same process stage, load or physical interaction recurs before selecting a repair.',
            'Resolve the repeated-stop cause and retain original evidence; if application checks do not explain the recurrence, arrange a qualified service review.'))
    if not topics and any(m.current_excursion_samples for m in incident.joint_evidence):
        topics.append(('Recorded motion and load state',
            'A recorded current-bound excursion warrants checking the motion and load context. It does not by itself identify a faulty program command or a damaged component.',
            'Review the load state and physical clearance at the recorded excursion, using the matching captured program and installation.'))
    if not topics and incident.codes:
        topics.append(('Program context for controller review',
            'The controller message needs interpretation in its operating context. There is not enough evidence to attribute it to a particular program statement.',
            'Retain the program and installation used at the incident and review the complete controller message with the applicable UR guidance.'))
    return topics


def _program_areas(incidents):
    areas = {}
    associations = {}
    for incident in incidents:
        contexts = incident.program_evidence or [None]
        for area, summary, action in _program_topics(incident):
            for context in contexts:
                program = context.program if context and context.program else 'Program not identified'
                key = (program, area)
                item = areas.setdefault(key, ProgramArea(program=program, area=area, priority='Investigate', summary=summary))
                item.incident_ids.append(incident.id)
                item.recommendations.append(action)
                if context:
                    item.script_lines.extend(context.script_lines)
                    if context.marker_line:
                        item.evidence.append(f'{incident.id}: recorded controlling script line {context.marker_line} near the event marker.' + (f' Captured command: {context.command}' if context.command else ''))
                    elif context.script_lines:
                        shown = ', '.join(map(str, context.script_lines[:12]))
                        remainder = f' and {len(context.script_lines)-12} more' if len(context.script_lines) > 12 else ''
                        item.evidence.append(f'{incident.id}: controlling script lines {shown}{remainder} occur in the recording; no fault-causing statement was established.')
                    if context.installation:
                        item.evidence.append(f'{incident.id}: recorded installation {context.installation}.')
                    association = 'Matched incident recording' if incident.codes and incident.flight_names else 'Recording context without a unique log association'
                    if incident.codes and incident.flight_names and not incident.association_verified:
                        association += ' (source identity unconfirmed)'
                    if context.scope == 'Recording-level':
                        association += '; exact fault sample unavailable'
                    else:
                        association += '; marker locates recorded event, not proven root cause'
                else:
                    association = 'No matching captured program context'
                    item.recommendations.append('Obtain the matching flight recording, captured script and installation before assigning this concern to a specific program area or line.')
                item.evidence.append(f'{incident.id}: ' + (', '.join(incident.codes) if incident.codes else 'recorded motion evidence') + '.')
                associations.setdefault(key, []).append(association)
    for key, item in areas.items():
        item.association = '; '.join(_unique(associations[key]))
        item.incident_ids = _unique(item.incident_ids)
        item.evidence = _unique(item.evidence)
        item.recommendations = _unique(item.recommendations)
        item.script_lines = sorted(set(item.script_lines))
    return list(areas.values())


def build_robot_status(diagnostic_report):
    """Synthesize evidence into a client report without files, network or robot calls."""
    report = diagnostic_report
    status = RobotStatusReport(source=report.source, diagnostic_report=report,
                               references=[dict(reference) for reference in report.references])
    serials = _unique(context['serial'] for context in report.robot_context if context.get('serial'))
    robot_ids = _unique(context['robot_id'] for context in report.robot_context if context.get('robot_id'))
    models = _unique(context['model'] for context in report.robot_context if context.get('model'))
    software = _unique(context['software_version'] for context in report.robot_context if context.get('software_version'))
    dates = sorted({stamp.date().isoformat() for incident in report.incidents if (stamp := _date(incident.time)) is not None})
    status.robot_details = [('Robot serial', ', '.join(serials) or 'Not available in supplied evidence'),
                            ('Robot model', ', '.join(models) or 'Not established'),
                            ('Controller software', ', '.join(software) or 'Not established'),
                            ('Incident dates', (' to '.join((dates[0], dates[-1])) if len(dates) > 1 else dates[0]) if dates else 'Not established')]
    status.coverage = [f'Reviewed {report.log_source_count} controller log dataset(s) and {report.recording_count} flight recording(s).',
                       'The findings describe the supplied history and recorded windows, not continuous monitoring or a physical inspection.']
    if dates:
        status.coverage.append('Available incident dates: ' + (' to '.join((dates[0], dates[-1])) if len(dates) > 1 else dates[0]) + '.')
    if report.warnings:
        status.coverage.append('Source warnings were reported. Check the technical coverage details before treating this as a complete review.')
    status.limitations = ['This review does not predict remaining joint life or certify that the robot is safe to operate. Findings must be considered with the real application and any maintenance completed after these records.']
    if not serials:
        status.limitations.append('Robot identity was not established from source serial numbers. Confirm all supplied files belong to the robot named on this report.')
    if len(serials) > 1 or len(robot_ids) > 1:
        status.overall_status = 'Separate robot datasets'
        status.assessment = ['The supplied files identify more than one robot. A single robot condition assessment would combine unrelated evidence, so no combined joint assessment has been made.']
        status.joints = [JointStatus(joint=joint, summary='Not assessed: source files identify multiple robot serial numbers.') for joint in JOINTS]
        status.actions = ['Separate the source files by robot serial number and generate one Robot Status report per robot.']
        return status
    # Repeated references to the same object/case do not create repeated issues.
    incidents = []
    seen = set()
    for incident in report.incidents:
        key = (incident.id, incident.time, tuple(incident.codes), tuple(incident.provenance)) if incident.id else ('object', id(incident))
        if key not in seen:
            incidents.append(incident)
            seen.add(key)
    status.joints = [_joint_status(joint, report, incidents) for joint in JOINTS]
    status.program_areas = _program_areas(incidents)
    attention = [joint.joint for joint in status.joints if joint.status == 'Needs attention']
    review_motion = [joint.joint for joint in status.joints if joint.status == 'Review motion evidence']
    unassessed = [joint.joint for joint in status.joints if joint.status in {'Not assessed', 'Limited evidence'}]
    if attention:
        status.overall_status = 'Attention required'
        status.assessment.append('Controller evidence identifies concerns involving ' + ', '.join(attention) + '. These are the first areas to investigate; the evidence does not establish that the joints need replacement.')
        status.assessment.append('Start with the recorded process stage, physical clearance and active load settings. Use the joint summaries to distinguish application-related warnings from controller messages that require service interpretation.')
    elif review_motion:
        status.overall_status = 'Motion review recommended'
        status.assessment.append('Recorded motion needs review for ' + ', '.join(review_motion) + '. Current crossed available recorded bounds, but the supplied evidence does not confirm physical contact or a component defect.')
    elif any(incident.codes for incident in incidents):
        status.overall_status = 'Controller review required'
        status.assessment.append('Controller messages require review, but the available evidence does not reliably assign them to a specific joint condition. Use the program context and complete messages to decide the next investigation.')
    elif report.log_source_count or any(incident.joint_evidence for incident in incidents):
        status.overall_status = 'Limited condition assessment' if unassessed or report.warnings else 'No recorded concern'
        status.assessment.append('No mapped joint-specific warning or recorded current-bound excursion was identified in the reviewed evidence. This is a statement about these records, not confirmation of present mechanical health.')
    else:
        status.assessment.append('The available files do not contain enough usable robot evidence to assess condition. Collect controller logs and flight recordings before issuing a condition conclusion.')
    if unassessed:
        status.assessment.append('Evidence is insufficient to characterize ' + ', '.join(unassessed) + '. These joints remain unassessed or only partly observed; absence of a recorded fault is not a clean bill of health.')
    if report.warnings:
        status.assessment.append('Source coverage has qualifications. Resolve missing or omitted evidence before drawing conclusions about fault-free operation.')
    shared_motion_joints = [joint.joint for joint in status.joints
                           if JOINT_MOTION_REVIEW.format(joints=joint.joint) in joint.recommendations]
    status.actions = ([JOINT_MOTION_REVIEW.format(joints=', '.join(shared_motion_joints))]
                      if shared_motion_joints else [])
    status.actions.extend(action for joint in status.joints for action in joint.recommendations
                          if action != JOINT_MOTION_REVIEW.format(joints=joint.joint)
                          and action not in status.actions)
    status.actions.extend(action for area in status.program_areas for action in area.recommendations if action not in status.actions)
    if unassessed:
        status.actions.append('Collect representative controller logs and joint motion recordings for the insufficiently covered joints, and confirm current condition with the maintenance history and physical inspection.')
    if not status.actions:
        status.actions.append('Retain this review as a baseline and compare it with future records and the normal maintenance inspection; no corrective repair is justified by these records alone.')
    status.actions = _unique(status.actions)
    return status
