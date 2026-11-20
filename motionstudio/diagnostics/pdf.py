"""Client Robot Status PDFs with an optional technical incident appendix."""
from pathlib import Path
import hashlib
import os
import tempfile

import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from motionstudio.core.rendering import RENDER_LOCK
from motionstudio.flight.data import load_flight
from motionstudio.core.kinematics import JOINTS
from motionstudio.reporting.pdf import Chip, ReportDocument, LEFT, RIGHT, PAGE_WIDTH, PAGE_HEIGHT, INK, MUTED
from motionstudio.programs.pdf import deployment_follow_up, line_ranges
from motionstudio.core.about import APP_NAME

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
        if numbered:
            document.bullet(value, f'{number}.', indent=22)
        else:
            document.paragraph(value)
        document.y += 4


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
    top = document.y + 30
    height = 122
    colors = ('#176b9c', '#ac4b16', '#3f7a42', '#8b4a8e', '#966d00', '#48596c')
    for row, (metric, label) in enumerate((('velocity', 'Actual velocity [rad/s]'),
                                          ('position', 'Actual − target position [rad]'),
                                          ('current', 'Actual − target current [A]'))):
        y = top + row * (height + 44)
        axes = document.figure.add_axes(((LEFT+40)/PAGE_WIDTH, 1-(y+height)/PAGE_HEIGHT,
                                         (RIGHT-LEFT-40)/PAGE_WIDTH, height/PAGE_HEIGHT))
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
        axes.set_title(label, fontsize=9.5, loc='left', color=INK, fontweight='bold', pad=6)
        axes.tick_params(labelsize=7.5, colors=MUTED, length=2.5)
        axes.grid(True, color='#e3e9ea', linewidth=.5)
        axes.set_axisbelow(True)
        for name, spine in axes.spines.items():
            spine.set_visible(name in ('left', 'bottom'))
            spine.set_color('#b9c5c8')
        end = max(evidence.window_end, evidence.window_start + .001)
        axes.set_xlim(evidence.window_start, end)
        if evidence.marker_elapsed is not None:
            axes.axvline(evidence.marker_elapsed, color='#af3939', linestyle='--', linewidth=1)
        if found:
            axes.legend(loc='lower right', bbox_to_anchor=(1, 1), borderaxespad=.25, fontsize=7,
                        ncol=6, frameon=False, labelcolor=INK, handlelength=1.6, columnspacing=1.1)
        else:
            axes.text(.5, .5, 'Required recorded channels are unavailable', ha='center', va='center',
                      transform=axes.transAxes, fontsize=9, color=MUTED)
        if row == 2:
            axes.set_xlabel('Elapsed recording time [s] · gaps remain unconnected', fontsize=8, color=INK, labelpad=3)


def _client_text(value, source):
    """Do not publish the operator's local workspace path in the client document."""
    value = str(value)
    if source:
        value = value.replace(str(source), 'Evidence folder')
    return value


def _source_inventory(sources, root):
    """One entry per file; its referenced log lines are listed together, not once per line."""
    files = {}
    for source in unique_text(sources):
        path, separator, line = source.rpartition(':')
        if separator and line.isdigit():
            files.setdefault(path, []).append(int(line))
        else:
            files.setdefault(source, [])
    return [_client_text(path, root) + (': lines ' + line_ranges(lines) if lines else '') for path, lines in files.items()]


APPENDIX_ALL_INCIDENTS = 25  # above this, only incidents with a flight recording get their own page


def _technical_appendix(document, report, check):
    document.new_page('Technical evidence appendix')
    document.paragraph('Supporting records are included for technical review. Codes and script lines identify recorded context; they do not alone establish a physical root cause.')
    document.y += 12
    incidents = report.incidents
    if len(incidents) > APPENDIX_ALL_INCIDENTS:
        incidents = [incident for incident in incidents if incident.flight_names]
        document.paragraph(f'{len(report.incidents)} incidents were analysed; the {len(incidents)} with a flight recording are shown here. '
                           'The others are counted in the findings table.', size=9, color=MUTED)
        document.y += 8
    document.rows('Incident guide', [(item.id, f'{item.time or "Time unavailable"} · {item.title}') for item in incidents])
    plotted = set()
    for incident in incidents:
        check()
        document.new_page(f'{incident.id} · Incident review')
        document.heading(incident.title)
        document.rows('Evidence context', [('Recorded time', incident.time or 'Unavailable'),
                      ('Controller codes', ', '.join(incident.codes) or 'No associated log code'),
                      ('Evidence link', incident.confidence), ('Motion window', incident.window_reason)])
        for title, values in incident_sections(incident):
            check()
            _paragraphs(document, title, [_client_text(value, report.source) for value in values],
                        numbered=title == 'What to check next')
        document.y += 6
        document.rows('Source records', [(str(n), _client_text(value, report.source))
                                         for n, value in enumerate(unique_text(incident.provenance), 1)], label_width=35)
        for evidence in incident.charts:
            check()
            key = (evidence.flight_path, evidence.window_start, evidence.window_end, evidence.marker_elapsed)
            if key not in plotted:
                _chart(document, evidence, incident.id)
                plotted.add(key)
    document.new_page('Sources and method')
    _paragraphs(document, 'Source inventory', _source_inventory(report.sources, report.source))
    _paragraphs(document, 'Collection and analysis details',
                [_client_text(value, report.source) for value in report.coverage + report.warnings])


def clean_warnings(values):
    """Coverage warnings worth showing: unique, and not an empty 'Source: ' stub from an older collection."""
    return [value for value in unique_text(values) if not value.rstrip().endswith(':')]


def _joint_rows(status):
    health = status.health
    if health.joints:
        return [(row.joint, Chip(row.status), '\n'.join(row.metrics) or 'No recording', str(row.events) if row.events else '0',
                 row.trend, row.notes) for row in health.joints]
    return [(joint.joint, Chip(joint.status), 'No recording', '–', '–', joint.summary) for joint in status.joints]


def _class_details(document, findings):
    """One block per event class: cause, why, fix and basis once, then its findings by joint."""
    from motionstudio.diagnostics.health import SEVERITIES
    classes = {}
    for finding in findings:
        classes.setdefault(finding.category, []).append(finding)
    for name, group in sorted(classes.items(), key=lambda kv: min(SEVERITIES.index(f.severity) for f in kv[1])):
        info = group[0].info
        document.ensure(150)
        document.heading(f'{name} · {", ".join(f.id for f in group)}', 11.5)
        fix = ' '.join(text for text in (info[4], info[6], info[5]) if text)
        bases = unique_text(f.basis for f in group)
        document.table(('', ''), [(('Likely cause', MUTED, 'bold'), info[2]), (('Why it matters', MUTED, 'bold'), info[3]),
                                  (('Recommended fix', MUTED, 'bold'), fix),
                                  (('Basis', MUTED, 'bold'), f'{"; ".join(bases)}. Reference: {info[7]}.')],
                       (.18, .82), size=8.5, header=False)
        document.y -= 6
        document.table(('ID', 'Severity', 'Joint', 'Events', 'Period', 'Trend', 'Codes', 'Where'),
                       [(f.id, Chip(f.severity), f.joint or '–', str(f.count), f.period,
                         f'{f.trend} {f.trend_counts[0]}→{f.trend_counts[1]}' if f.trend in ('Rising', 'Falling', 'Stable') else f.trend,
                         ', '.join(f.codes), f.where) for f in group],
                       (.07, .12, .11, .07, .19, .13, .11, .2), size=7.5)
        recorded = unique_text(name for f in group for name in f.recordings)
        if recorded:
            document.paragraph('Flight recordings at these events: ' + ', '.join(recorded) + '.', size=8, color=MUTED)
            document.y += 4


def _monthly_chart(document, health):
    """Classified events per month, stacked by class; the largest four classes keep their own colour."""
    months = sorted(month for month in health.monthly if month != 'Unknown')
    totals = {}
    for month in months:
        for name, count in health.monthly[month].items():
            if name != 'Uninterpreted':
                totals[name] = totals.get(name, 0) + count
    if len(months) < 2 or not totals:
        return
    order = sorted(totals, key=totals.get, reverse=True)
    shown, other = order[:4], order[4:]
    series = [(name, [health.monthly[m].get(name, 0) for m in months]) for name in shown]
    if other:
        series.append(('Other classified', [sum(health.monthly[m].get(name, 0) for name in other) for m in months]))
    colors = ('#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#8a9aa0')  # dataviz categorical slots 1-4, then neutral
    height = 130
    document.ensure(height+60)
    document.paragraph('Classified events per month', size=10, weight='bold')
    document.paragraph('Uninterpreted controller codes are left out; one event can carry more than one class.', size=8, color=MUTED)
    top = document.y + 26
    axes = document.figure.add_axes(((LEFT+28)/PAGE_WIDTH, 1-(top+height)/PAGE_HEIGHT, (RIGHT-LEFT-28)/PAGE_WIDTH, height/PAGE_HEIGHT))
    base = np.zeros(len(months))
    positions = np.arange(len(months))
    for (name, values), color in zip(series, colors):
        axes.bar(positions, values, .55, bottom=base, color=color, label=name, edgecolor='white', linewidth=1)
        base += values
    for x, total in zip(positions, base):
        axes.text(x, total, f'{int(total)}', ha='center', va='bottom', fontsize=7, color=INK)
    axes.set_xticks(positions, [__import__('datetime').date(int(m[:4]), int(m[5:]), 1).strftime('%b %Y') for m in months])
    axes.tick_params(labelsize=7.5, colors=MUTED, length=0)
    axes.grid(True, axis='y', color='#e3e9ea', linewidth=.5)
    axes.set_axisbelow(True)
    axes.set_ylim(0, max(base)*1.15)
    for name, spine in axes.spines.items():
        spine.set_visible(name == 'bottom')
        spine.set_color('#b9c5c8')
    axes.legend(loc='lower left', bbox_to_anchor=(0, 1.02), ncol=len(series), fontsize=7, frameon=False,
                labelcolor=INK, handlelength=1, handleheight=1, columnspacing=1.2, borderaxespad=0)
    document.y = top + height + 26


def export_diagnostic_pdf(report, destination, check_cancel=None, *, metadata=None, include_evidence=False):
    """Save a client Robot Status report; technical incident detail is opt-in."""
    check = check_cancel or (lambda: None)
    check()
    from motionstudio.diagnostics.status import RobotStatusReport, build_robot_status
    from motionstudio.diagnostics.health import METHOD
    status = report if isinstance(report, RobotStatusReport) else build_robot_status(report)
    report = status.diagnostic_report
    health = status.health
    metadata = metadata or {}
    destination = Path(destination)
    source = status.source
    handle, temporary = tempfile.mkstemp(prefix='.diagnostic-', suffix='.pdf', dir=destination.parent)
    os.close(handle)
    try:
        with RENDER_LOCK, PdfPages(temporary, metadata={
            'Title': 'Robot Status Report', 'Author': metadata.get('prepared_by') or APP_NAME,
            'Subject': 'Arm health: joint condition, findings and program areas to review',
        }) as pdf:
            label = metadata.get('robot_label', '').strip() or metadata.get('client', '').strip()
            document = ReportDocument(pdf, label, subtitle='ROBOT STATUS REPORT', note=NOTE)

            # 1. Executive summary
            document.new_page('Robot Status Report')
            details = dict(status.robot_details)
            robot = ' · '.join(value for key in ('Robot model', 'Robot serial') if (value := details.get(key, ''))
                               and not value.startswith(('Not ', 'Not available')))
            document.paragraph('Arm health review' + (f' · {robot}' if robot else '') + (f' · {health.period}' if health.start else ''),
                               size=10.5, color=MUTED)
            document.y += 10
            context = [(label, metadata[key].strip()) for label, key in (('Client', 'client'), ('Robot / asset', 'robot_label'),
                       ('Prepared by', 'prepared_by')) if metadata.get(key, '').strip()]
            context += [(key, value) for key, value in status.robot_details if key != 'Incident dates']
            if health.start:
                context.append(('Period analysed', health.period))
            if report is not None:
                context.append(('Evidence', f'{report.log_source_count} controller log set(s), {report.recording_count} flight recording(s)'
                                + (f', {health.bursts} logged events' if health.bursts else '')))
            document.rows('Review details', context)
            document.banner(status.overall_status, 'Overall arm health: ' + status.overall_status,
                            ' '.join(_client_text(value, source) for value in unique_text(status.assessment)[:2]))
            if health.findings:
                serious = sum(count for name, count in health.counts() if name in ('Critical', 'High'))
                concern = [row.joint for row in health.joints if row.status in ('Critical', 'Attention required')]
                document.tiles([(f'{health.bursts:,}', 'logged events analysed'),
                                (f'{len(concern)} of 6', 'joints needing attention'),
                                (str(serious), 'critical or high findings'),
                                (f'{health.matched_recordings} of {report.recording_count}' if report else '0', 'recordings matched to events')])
                document.heading('Top risks', 11)
                document.table(('ID', 'Severity', 'Finding', 'Events', 'Trend'),
                               [(f.id, Chip(f.severity), f.title, str(f.count), f.trend) for f in health.findings[:3]],
                               (.09, .16, .5, .11, .14))
                document.paragraph('Findings by severity: ' + ' · '.join(f'{name} {count}' for name, count in health.counts()),
                                   size=8.5, color=MUTED)

            document.y += 6
            document.paragraph('This assessment describes the available records. It is not an inspection of the robot’s present mechanical '
                               'condition, a prediction of remaining joint life or a safety approval.', size=8.5, color=MUTED)

            # 2. Joint health
            check()
            document.new_page('Joint health')
            document.paragraph('Status follows each joint\'s most severe finding (see Data coverage and method). Key metrics are the largest values in the '
                               'matched flight recordings: tracking is actual − target position, Current Δ is actual − target current.', size=9, color=MUTED)
            document.y += 8
            document.table(('Joint', 'Status', 'Key metrics', 'Events', 'Trend', 'Notes'), _joint_rows(status),
                           (.11, .18, .21, .08, .1, .32), size=7.5, bold_first=True)
            _monthly_chart(document, health)

            # 3. Findings and program hot spots
            check()
            document.start_section('Findings')
            if health.findings:
                document.paragraph('One finding per event class and joint; the fixes are under Finding details and Recommendations.', size=9, color=MUTED)
                document.y += 6
                document.table(('ID', 'Severity', 'Category', 'Joint', 'Events', 'Trend', 'Program location'),
                               [(f.id, Chip(f.severity), f.category, f.joint or '–', str(f.count), f.trend, f.where)
                                for f in health.findings], (.07, .13, .21, .12, .08, .1, .29), size=8)
            else:
                document.paragraph('No classified controller warnings were found in the supplied records.')
            document.heading('Where the program struggles')
            if health.hot_spots:
                document.table(('Rank', 'Program location', 'Events', 'Event types', 'Likely cause', 'Findings', 'Program Check'),
                               [(str(h.rank), h.location, str(h.count), h.types, h.cause, ', '.join(h.findings), ', '.join(h.program_refs) or '–')
                                for h in health.hot_spots], (.06, .18, .07, .22, .27, .1, .1), size=7.5)
                for program_check in health.program_checks:
                    document.heading(f'Program Check of {program_check["program"]} · score {program_check["score"]} / 100', 11)
                    document.paragraph(f'The program saved in {program_check["recording"]}, the program that was running at that stop. '
                                       'P-IDs match a Program Check report of the same program.', size=8.5, color=MUTED)
                    document.y += 4
                    if program_check['findings']:
                        document.table(('ID', 'Severity', 'Category', 'Location', 'Issue'),
                                       [(f['id'], Chip(f['severity']), f['category'], f['location'], f['issue']) for f in program_check['findings']],
                                       (.08, .13, .22, .15, .42), size=7.5)
                    else:
                        document.paragraph('No source value departs from UR guidance.', size=8.5)
                missing = [p for p in health.programs if p not in {c['program'] for c in health.program_checks}]
                if missing:
                    document.paragraph('Cross-check: run Program Check on ' + ', '.join(missing) +
                                       ' and compare its payload, blend and motion findings with these locations.', size=8.5, color=MUTED)
                document.paragraph('“Not identified”: no flight recording exists at those times, so the program location is unknown.', size=8, color=MUTED)
            identified = [area for area in status.program_areas if area.program and area.program != 'Program not identified']
            if not health.hot_spots:  # without graded findings, show the program review areas themselves
                for area in identified:
                    lines = f' · recorded script lines {line_ranges(area.script_lines)}' if area.script_lines else ''
                    document.bullet(f'{area.program} · {area.area}{lines}. {area.summary}', size=8.5)
            if not health.hot_spots and not identified:
                document.paragraph('The records do not place the events in a program location. A flight recording at the time of an event is needed to do so.')

            # 4. Finding details
            detailed = [f for f in health.findings if f.info]
            if detailed:
                check()
                document.start_section('Finding details')
                document.paragraph('Grouped by event class: cause, impact and fix are stated once, then each joint\'s finding. '
                                   'Trend gives the events in the first → second half of the period.', size=9, color=MUTED)
                _class_details(document, detailed)
                rest = [f for f in health.findings if f not in detailed]
                if rest:
                    document.heading('Not classified', 11.5)
                    for finding in rest:
                        document.paragraph(f'{finding.id} · {finding.what} Their meaning is not interpreted here; look them up in the UR error-code directory for this software version.', size=8.5)

            # 5. Timeline
            if health.timeline:
                check()
                document.start_section('Event timeline')
                document.paragraph('Recorded flight reports, and the first and latest occurrence of each critical or high finding.', size=9, color=MUTED)
                document.y += 6
                document.table(('Time', 'Event', 'Codes · joint · program', 'Finding'), health.timeline, (.2, .3, .38, .12), size=8)

            # 6. Recommendations
            check()
            document.start_section('Recommendations')
            groups = [('Program changes', health.program), ('Maintenance and hardware', health.maintenance),
                      ('Application and environment', health.application), ('Further data needed', health.data_needed),
                      ('Monitoring', health.monitoring)]
            if not any(values for _, values in groups):
                groups = [('Recommended next steps', status.actions)]
            for title, values in groups:
                _paragraphs(document, title, [_client_text(v, source) for v in values], numbered=True)
                document.y += 4
            document.paragraph('Change motion or payload settings only after checking the real tool, part and cell. Keep the original records for follow-up.', size=8.5, color=MUTED)

            # 7. Coverage and method
            check()
            document.start_section('Data coverage and method')
            _paragraphs(document, 'Coverage', [_client_text(v, source) for v in status.coverage])
            warnings = clean_warnings(report.warnings if report is not None else [])
            if warnings:
                _paragraphs(document, 'Gaps in the data', [_client_text(v, source) for v in warnings[:6]])
                if len(warnings) > 6:
                    document.paragraph(f'{len(warnings)-6} further source notes are listed in the app\'s Coverage & sources view.', size=8.5, color=MUTED)
            _paragraphs(document, 'How findings are rated', METHOD)
            _paragraphs(document, 'Limits', [_client_text(v, source) for v in status.limitations] +
                        ['This review does not replace a risk assessment (ISO 10218 / ISO/TS 15066) or a physical inspection.'])
            references = {}
            for finding in health.findings:
                if finding.info:
                    references.setdefault(finding.info[8], finding.info[7])
            for reference in status.references:
                url = reference.get('url', '')
                references.setdefault(url, reference.get('title', '') if reference.get('title') not in ('', 'Diagnostic guidance source')
                                      else 'UR ' + url.rstrip('/').rsplit('/', 1)[-1].replace('.html', '').replace('CODE_', 'error code C').replace('-', ' '))
            if references:
                document.heading('References')
                for url, title in references.items():
                    document.link(title, url)
            deployment_follow_up(document)
            if include_evidence and report is not None:
                check()
                _technical_appendix(document, report, check)
            document.close()
        check()
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination
