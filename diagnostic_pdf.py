"""Client Robot Status PDFs with an optional technical incident appendix."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import tempfile

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from flight_charts import RENDER_LOCK
from flight_data import JOINTS, load_flight
from flight_pdf import LEFT, RIGHT, PAGE_WIDTH, PAGE_HEIGHT, INK, MUTED
from program_check_pdf import _ProgramReport, deployment_follow_up

NOTE = ('Local evidence-based review; hypotheses require verification. Controller detections do not alone prove '
        'physical contact or component failure. Not a safety validation.')


def unique_text(values):
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def incident_sections(incident):
    """One consistent narrative for both preview and exported document."""
    seen = set()
    sections = []
    for heading, values in (
        ('What the evidence shows', incident.observations),
        ('Possible contributors', incident.hypotheses),
        ('Alternative explanations and limits', incident.counterevidence),
        ('What to check next', incident.actions),
        ('Evidence still needed', incident.missing_evidence),
    ):
        items = [value for value in unique_text(values) if value not in seen]
        seen.update(items)
        if items:
            sections.append((heading, items))
    return sections


def _paragraphs(document, title, values, numbered=False):
    values = unique_text(values)
    if not values:
        return
    document.heading(title)
    for number, value in enumerate(values, 1):
        document.paragraph(f'{number}. {value}' if numbered else value)
        document.y += 8


def _chart(document, evidence, incident_id):
    """Plot only recorded intervals, never bridge missing samples or invent a stop."""
    document.new_page(f'{incident_id} · Motion evidence')
    document.paragraph(evidence.flight_name, size=11, weight='bold')
    document.paragraph(f'{evidence.scope}: elapsed {evidence.window_start:.3f}–{evidence.window_end:.3f} s.', color=MUTED)
    document.paragraph('Dashed line: explicit incident marker.' if evidence.marker_elapsed is not None
                       else 'No exact fault sample is established. These traces describe the recording, not the instant of the stop.', color=MUTED)
    try:
        signature = getattr(evidence, 'source_sha256', '')
        if signature:
            digest = hashlib.sha256()
            with Path(evidence.flight_path).open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
            if digest.hexdigest() != signature:
                raise ValueError('source file changed after analysis; analyze the folder again to refresh the traces')
        flight = load_flight(evidence.flight_path)
    except Exception as error:
        document.paragraph(f'Traces unavailable: {error}. The analyzed observations remain above.')
        return
    top = max(document.y + 22, 225)
    height = 122
    colors = ('#176b9c', '#ac4b16', '#3f7a42', '#8b4a8e', '#966d00', '#48596c')
    for row, (metric, label) in enumerate((('velocity', 'Actual velocity [rad/s]'),
                                          ('position', 'Actual − target position [rad]'),
                                          ('current', 'Actual − target current [A]'))):
        y = top + row * (height + 40)
        axes = document.figure.add_axes((LEFT/PAGE_WIDTH, 1-(y+height)/PAGE_HEIGHT,
                                         (RIGHT-LEFT)/PAGE_WIDTH, height/PAGE_HEIGHT))
        found = False
        for joint, name in enumerate(JOINTS):
            values = flight.series(metric, joint)
            if metric != 'velocity':
                values = values - flight.series(metric, joint, target=True)
            values = np.where((flight.time >= evidence.window_start) & (flight.time <= evidence.window_end), values, np.nan)
            labelled = False
            for segment in flight.valid_segments(values):
                axes.plot(flight.time[segment], values[segment], color=colors[joint], linewidth=.85,
                          marker='.' if len(flight.time[segment]) == 1 else None,
                          label=name if not labelled else None)
                found = labelled = True
        axes.set_title(label, fontsize=9, loc='left', color=INK)
        axes.tick_params(labelsize=7, colors=INK)
        axes.grid(True, alpha=.2)
        end = max(evidence.window_end, evidence.window_start + .001)
        axes.set_xlim(evidence.window_start, end)
        if evidence.marker_elapsed is not None:
            axes.axvline(evidence.marker_elapsed, color='#af3939', linestyle='--', linewidth=1)
        if found:
            axes.legend(loc='upper right', fontsize=6, ncol=3, framealpha=.9)
        else:
            axes.text(.5, .5, 'Required recorded channels are unavailable', ha='center', va='center',
                      transform=axes.transAxes, fontsize=9, color=MUTED)
        if row == 2:
            axes.set_xlabel('Elapsed recording time [s] · gaps remain unconnected', fontsize=8)


def _client_text(value, source):
    """Do not publish the operator's local workspace path in the client document."""
    value = str(value)
    if source:
        value = value.replace(str(source), 'Evidence folder')
    return value


def _technical_appendix(document, report, check):
    document.new_page('Technical evidence appendix')
    document.paragraph('Supporting records are included for technical review. Codes and script lines identify recorded context; they do not alone establish a physical root cause.')
    document.y += 12
    document.rows('Incident guide', [(item.id, f'{item.time or "Time unavailable"} · {item.title}') for item in report.incidents])
    plotted = set()
    for incident in report.incidents:
        check()
        document.new_page(f'{incident.id} · Incident review')
        document.heading(incident.title)
        document.rows('Evidence context', [('Recorded time', incident.time or 'Unavailable'),
                      ('Controller codes', ', '.join(incident.codes) or 'No associated log code'),
                      ('Association', incident.confidence), ('Motion window', incident.window_reason)])
        for title, values in incident_sections(incident):
            check()
            _paragraphs(document, title, values, numbered=title == 'What to check next')
        document.rows('Traceability', [(str(n), value) for n, value in enumerate(unique_text(incident.provenance), 1)], label_width=35)
        for evidence in incident.charts:
            check()
            key = (evidence.flight_path, evidence.window_start, evidence.window_end, evidence.marker_elapsed)
            if key not in plotted:
                _chart(document, evidence, incident.id)
                plotted.add(key)
    document.new_page('Sources and method')
    _paragraphs(document, 'Source inventory', report.sources)
    _paragraphs(document, 'Collection and analysis details', report.coverage + report.warnings)


def export_diagnostic_pdf(report, destination, check_cancel=None, *, metadata=None, include_evidence=False):
    """Save a client Robot Status report; technical incident detail is opt-in."""
    check = check_cancel or (lambda: None)
    check()
    from robot_status import RobotStatusReport, build_robot_status
    status = report if isinstance(report, RobotStatusReport) else build_robot_status(report)
    report = status.diagnostic_report
    metadata = metadata or {}
    destination = Path(destination)
    handle, temporary = tempfile.mkstemp(prefix='.diagnostic-', suffix='.pdf', dir=destination.parent)
    os.close(handle)
    try:
        with RENDER_LOCK, PdfPages(temporary, metadata={
            'Title': 'Robot Status', 'Author': metadata.get('prepared_by') or 'UR Motion Studio',
            'Subject': 'Recorded robot condition, joint assessments and program review priorities',
        }) as pdf:
            document = _ProgramReport(pdf, '', subtitle='ROBOT STATUS REPORT', note=NOTE)
            document.new_page('Robot Status')
            document.paragraph('Recorded condition and recommended next steps', size=14, color=MUTED)
            document.y += 18
            context = []
            for label, key in (('Client', 'client'), ('Robot / asset', 'robot_label'), ('Prepared by', 'prepared_by')):
                if metadata.get(key, '').strip():
                    context.append((label, metadata[key].strip()))
            context.extend(status.robot_details)
            context.append(('Report generated', datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')))
            document.rows('Review details', context)
            document.heading(status.overall_status)
            for value in unique_text(status.assessment):
                document.paragraph(_client_text(value, status.source))
                document.y += 9
            document.paragraph('This assessment describes the available records. It is not an inspection of the robot’s present mechanical condition or a safety approval.', size=9, color=MUTED)

            check()
            document.new_page('Joint condition')
            document.paragraph('Each joint is assessed separately. “Not assessed” and “Limited evidence” are gaps in the available records, not a pass or a prediction of remaining life.')
            document.y += 15
            document.rows('Assessment by joint', [(joint.joint, f'{joint.status}\n{joint.summary}') for joint in status.joints], label_width=82)
            document.paragraph('Recommendations below connect the observed concerns to practical checks. Full incident measurements can be included in the optional technical evidence appendix.', size=9, color=MUTED)

            check()
            document.new_page('Program areas to review')
            if not status.program_areas:
                document.paragraph('The available evidence does not identify a specific program area to change. Obtain matching recorded program context before attributing a fault to a command or line.')
            for area in status.program_areas:
                check()
                document.heading(area.area)
                document.rows('Program context', [('Program', area.program or 'Not identified'),
                              ('Review priority', area.priority), ('Evidence association', area.association)], label_width=110)
                document.paragraph(area.summary)
                document.y += 8
                if area.script_lines:
                    from program_check_pdf import line_ranges
                    document.paragraph('Recorded script context: lines ' + line_ranges(area.script_lines) + '. These are execution references, not proof that the line caused the issue.', size=9, color=MUTED)
                    document.y += 7
                if area.incident_ids:
                    document.paragraph('Supporting cases: ' + ', '.join(unique_text(area.incident_ids)), size=9, color=MUTED)
                    document.y += 7

            check()
            document.new_page('Recommended action plan')
            _paragraphs(document, 'Recommended next steps', status.actions or ['Collect sufficient matching controller logs and flight recordings to assess the robot and identify targeted checks.'], numbered=True)
            document.paragraph('Verify the application and physical setup before changing motion or load settings. Retain the original evidence for follow-up and service review.', size=9, color=MUTED)
            document.y += 12
            _paragraphs(document, 'Scope of this assessment', [_client_text(value, status.source) for value in status.coverage])
            if report is not None and report.warnings:
                warnings = unique_text(report.warnings)
                _paragraphs(document, 'Coverage notes', [_client_text(value, status.source) for value in warnings[:5]])
                if len(warnings) > 5:
                    document.paragraph(f'{len(warnings)-5} additional source warnings are retained in the application’s Coverage & sources view and optional technical appendix.', size=9, color=MUTED)
                    document.y += 8
            _paragraphs(document, 'Evidence still needed', [_client_text(value, status.source) for value in status.limitations])
            if status.references:
                document.heading('Technical references')
                for number, reference in enumerate(status.references, 1):
                    title = reference.get('title', 'UR technical guidance')
                    if title == 'Diagnostic guidance source':
                        title = 'UR technical guidance'
                    document.paragraph(f'{number}. {title}', size=9, weight='bold')
                    document.paragraph(reference.get('url', ''), size=8)
                    document.y += 8
            deployment_follow_up(document)
            if include_evidence:
                check()
                _technical_appendix(document, report, check)
            document.finish_page()
        check()
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
