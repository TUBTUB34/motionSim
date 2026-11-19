"""Offline, operator-authored deployment and inspection review records.

The catalog is shared by the checklist and report generators. These are review
prompts, never an automated safety assessment or a complete inspection procedure.
"""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import csv
import io
import json
import os
from pathlib import Path
import tempfile


GUIDANCE_URL = ('https://www.universal-robots.com/download/manuals-e-seriesur-series/'
                'installation-guides/good-installation-practices/'
                'robot-good-practices-installation-guide-english/')
SERVICE_BASE = 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-serv-man/E-series/'
DEPLOYMENT_REFERENCES = (
    ('UR Installation Guide v1.3.3', 'docs/Installation Guide V1.3.3_ENGLISH.pdf'),
    ('UR Application Deployment Workshop, May 2024', 'docs/UR Application Deployment_Workshop_2024.pdf'),
)
RECORD_NOTICE = ('Operator-entered review record. Recorded items are not automatically verified. '
                 'Use the current manual for your robot and the application risk assessment; '
                 'this checklist does not certify safety or replace a complete inspection.')


@dataclass(frozen=True)
class DeploymentCheck:
    key: str
    title: str
    group: str
    guidance: str
    sources: tuple[str, ...]
    url: str = GUIDANCE_URL


DEPLOYMENT_CHECKS = (
    DeploymentCheck('mounting', 'Mounting and orientation', 'Installation',
        'Record the base/tool mounting assessment, fastening inspection and configured orientation. '
        'Include moving-base conditions where applicable.',
        ('Installation Guide v1.3.3, PDF p. 5', 'Deployment Workshop 2024, PDF pp. 5–10')),
    DeploymentCheck('environment', 'Environment and protection', 'Installation',
        'Record suitability for the arm, controller and pendant: contamination, moisture, '
        'temperature, ventilation and cable routing.',
        ('Installation Guide v1.3.3, PDF p. 5', 'Deployment Workshop 2024, PDF pp. 12–18')),
    DeploymentCheck('electrical', 'Power and I/O', 'Installation',
        'Record the electrical inspection reference, power quality and I/O load review. '
        'Use model-specific electrical limits.',
        ('Installation Guide v1.3.3, PDF p. 9', 'Deployment Workshop 2024, PDF p. 17')),
    DeploymentCheck('tool_load', 'TCP, payload and CoG', 'Configuration',
        'Record measured tool TCP, total load and centre of gravity, with the relevant '
        'load diagram and inertia assessment.',
        ('Installation Guide v1.3.3, PDF p. 6', 'Deployment Workshop 2024, PDF pp. 20–31')),
    DeploymentCheck('load_changes', 'Payload changes', 'Configuration',
        'Check tool-only and loaded states, including pick, release and tool changes. '
        'Record where payload and centre of gravity change.',
        ('Installation Guide v1.3.3, PDF p. 6', 'Deployment Workshop 2024, PDF pp. 20–29')),
    DeploymentCheck('motion', 'Motion and acceleration', 'Programming',
        'Review transitions, waits, blend overlap and acceleration against the process. '
        'Record exceptions that require a deliberate stop.',
        ('Installation Guide v1.3.3, PDF pp. 7–8', 'Deployment Workshop 2024, PDF pp. 33–39')),
    DeploymentCheck('singularities', 'Reach and singularities', 'Programming',
        'Review the actual path near workspace limits, singularities and joint limits. '
        'Record clearance and path-review evidence.',
        ('Deployment Workshop 2024, PDF pp. 41–48',)),
    DeploymentCheck('stops', 'Protective stops', 'Programming',
        'Record recurring stop causes, corrective actions and follow-up evidence. '
        'Do not treat a reset as resolution.',
        ('Installation Guide v1.3.3, PDF p. 11', 'Deployment Workshop 2024, PDF pp. 50–52')),
    DeploymentCheck('risk_assessment', 'Risk assessment', 'Safety review',
        'Reference the application risk assessment and approved safety configuration, '
        'including tooling, workpieces and connected machinery. Do not store passwords here.',
        ('Installation Guide v1.3.3, PDF p. 12',)),
    DeploymentCheck('safety_validation', 'Safety validation', 'Safety review',
        'Reference the qualified validation record for emergency stops, safeguards, modes '
        'and connected safety I/O after installation or modification.',
        ('UR e-Series Service Manual, Pre-Use Assessment (SW5.24)',),
        SERVICE_BASE + 'serv-man-pre-use-assessment.htm'),
    DeploymentCheck('interfaces', 'Interfaces and URCaps', 'Programming',
        'Review external communications, thread timing and installed URCaps. Record '
        'required versions and compatibility evidence.',
        ('Installation Guide v1.3.3, PDF pp. 10–11',)),
    DeploymentCheck('inspection', 'Inspection plan', 'Maintenance',
        'Record the model-specific inspection plan, responsible person and next review date. '
        'Use the manual and operating conditions to set intervals.',
        ('Deployment Workshop 2024, PDF pp. 54–56',
         'UR e-Series Service Manual, Inspection Plan (SW5.24)'),
        SERVICE_BASE + 'serv-man-inspection-plan-CB.htm'),
    DeploymentCheck('support', 'Backup and support files', 'Maintenance',
        'Record the saved backup and Support File locations, robot identity, software '
        'version and incident time. Collect recent error reports before they are replaced.',
        ('UR PolyScope 5 manual, Log Tab (SW5.22)',),
        'https://www.universal-robots.com/manuals/EN/HTML/SW5_22/Content/prod-usr-man/'
        'software/PolyScope/content/log_g5/log_tab_en.htm'),
    DeploymentCheck('handover', 'Handover and training', 'Maintenance',
        'Record the responsible owner, training, current program and installation '
        'versions, and how outstanding actions will be followed up.',
        ('UR Deployment Checklist Template v1, PDF pp. 1–3',),
        'https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/'
        'UR%20Deployment%20Checklist%20Template_v1.pdf'),
)
STATES = ('Not reviewed', 'Recorded', 'Action needed', 'Not applicable')
METADATA_FIELDS = ('project', 'robot', 'reviewer', 'review_date', 'next_review')
MAX_FILE_BYTES = 1_048_576


def new_record():
    return {'version': 1, 'metadata': dict.fromkeys(METADATA_FIELDS, ''),
            'items': {item.key: {'status': STATES[0], 'note': ''} for item in DEPLOYMENT_CHECKS}}


def validate_record(value):
    """Return a normalized copy, rejecting incompatible or lossy imports."""
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('This is not a supported checklist file (version 1 required).')
    if set(value) - {'version', 'metadata', 'items'}:
        raise ValueError('The checklist contains unsupported fields.')
    result = new_record()
    metadata = value.get('metadata')
    items = value.get('items')
    if not isinstance(metadata, dict) or set(metadata) - set(METADATA_FIELDS):
        raise ValueError('The checklist metadata is invalid.')
    if not isinstance(items, dict) or set(items) - set(result['items']):
        raise ValueError('The checklist contains unknown review items.')
    for key, text in metadata.items():
        if not isinstance(text, str) or len(text) > 500:
            raise ValueError(f'Invalid {key.replace("_", " ")} field.')
        if key in ('review_date', 'next_review') and text:
            try:
                if date.fromisoformat(text).isoformat() != text:
                    raise ValueError()
            except ValueError:
                raise ValueError('Review dates must use YYYY-MM-DD or be blank.') from None
        result['metadata'][key] = text
    for key, item in items.items():
        if (not isinstance(item, dict) or set(item) != {'status', 'note'} or
                item['status'] not in STATES or not isinstance(item['note'], str) or len(item['note']) > 8000):
            raise ValueError(f'Invalid review entry: {key}.')
        result['items'][key] = dict(item)
    return result


def load_record(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_FILE_BYTES + 1)
    if len(data) > MAX_FILE_BYTES:
        raise ValueError('Checklist files must be smaller than 1 MiB.')
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        raise ValueError('The selected file is not valid checklist JSON.') from None
    return validate_record(value)


def _atomic_write(path, text):
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                dir=destination.parent, prefix='.' + destination.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def save_record(path, record):
    _atomic_write(path, json.dumps(validate_record(record), ensure_ascii=False, indent=2) + '\n')


def review_counts(record):
    counts = dict.fromkeys(STATES, 0)
    for item in record['items'].values():
        counts[item['status']] += 1
    return counts


def review_summary(record):
    counts = review_counts(record)
    return (f'{counts["Recorded"]} recorded · {counts["Action needed"]} action needed · '
            f'{counts["Not reviewed"]} not reviewed · {counts["Not applicable"]} not applicable')


def _csv_cell(value):
    # Quoting alone does not stop spreadsheets interpreting imported formulas.
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value


def export_record_csv(path, record):
    record = validate_record(record)
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(('Project', 'Robot / serial', 'Reviewer', 'Review date', 'Next review',
                     'Area', 'Review item', 'Status (operator entered)', 'Evidence / actions', 'Guidance', 'Sources', 'Reference URL', 'Scope'))
    metadata = [record['metadata'][key] for key in METADATA_FIELDS]
    for check in DEPLOYMENT_CHECKS:
        entry = record['items'][check.key]
        writer.writerow([_csv_cell(value) for value in metadata + [check.group, check.title, entry['status'],
            entry['note'], check.guidance, '; '.join(check.sources), check.url, RECORD_NOTICE]])
    _atomic_write(path, buffer.getvalue())


def today_utc():
    return datetime.now(timezone.utc).date().isoformat()
