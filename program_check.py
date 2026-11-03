"""Read-only, source-based UR program review. Never executes or compiles URScript."""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from collections import Counter
import ast
import gzip
import hashlib
import io
import math
import re
import zipfile
import tarfile
import posixpath
from defusedxml import ElementTree
from program_motion import analyze_motion, source_definitions, definition_estimate, Pose
from installation import decode_installation

MAX_BYTES = 16 * 1024 * 1024


def _member_name(name):
    """Logical archive path; backup TARs legitimately contain absolute paths."""
    return str(PurePosixPath(name.lstrip('/')))

REFERENCES = (
    ('UR Important Deployment Points (CB-Series/e-Series)', 'https://www.universal-robots.com/articles/ur/robot-care-maintenance/important-deployment-points/'),
    ('UR PolyScope 5.24 Script Manual: MoveJ', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/movej_qa14v105t0r.htm'),
    ('UR Motion Version (PolyScope 5.22+)', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_22/Content/prod-scriptmanual/all_scripts/motion_version_desc.htm'),
    ('UR Blend Parameters', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_19/Content/prod-usr-man/software/PolyScope/content/BasicProgNodes/commandtab_way_blend_para.htm'),
    ('UR Payload configuration (PolyScope 5.24)', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/set_target_payload.htm'),
    ('UR OptiMove parameter ranges', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/movej_optimove.htm'),
    ('UR MoveC geometry and units', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/movec_pose_via_pose_toa1.htm'),
    ('UR SpeedL velocity and acceleration', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/speedl_xd_a_t_aRot_a.htm'),
    ('UR ServoJ parameters', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-scriptmanual/all_scripts/servoj_qavt0-008lookahead_time.htm'),
    ('UR configurable joint and tool speed limits', 'https://www.universal-robots.com/manuals/EN/HTML/SW5_25/Content/prod-usr-man/complianceUR7e/H_g5_sections/safetyFunctionsAndinterfaces/configurable_functions.htm'),
    ('UR Application Deployment Workshop (May 2024), PDF pp. 25, 29–30: payload transitions and inertia', 'https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/UR%20Application%20Deployment_Workshop_2024.pdf'),
    ('UR Application Deployment Workshop (May 2024), PDF pp. 33–36: blends and process motion', 'https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/UR%20Application%20Deployment_Workshop_2024.pdf'),
    ('UR Installation Guide v1.3.3, PDF pp. 7, 10–11 (printed pp. 6, 9–10): continuity, processing and interfaces', 'https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/Installation%20Guide%20V1.3.3_ENGLISH.pdf'),
    ('UR Application Deployment Workshop (May 2024), PDF pp. 58–61: diagnostic instrumentation', 'https://s3-eu-west-1.amazonaws.com/ur-support-site/100103/UR%20Application%20Deployment_Workshop_2024.pdf'),
)
CATEGORIES = ('Initial payload / CoG / inertia', 'Dynamic payload changes', 'Initial TCP',
              'Dynamic TCP changes', 'Acceleration recommendations', 'Blend radius overlap',
              'Blended motion continuation', 'Velocity specifications', 'Move distances',
              'Payload transition and inertia', 'Process motion and waits',
              'Diagnostics and program efficiency')

@dataclass(frozen=True)
class ProgramSource:
    path: Path
    member: str = ''

    @property
    def name(self):
        return self.member or self.path.name

    @property
    def identity(self):
        return f'{self.path} :: {self.member}' if self.member else str(self.path)

    def read(self):
        if self.member:
            if self.path.suffix.lower() == '.zip':
                with zipfile.ZipFile(self.path) as archive:
                    info = archive.getinfo(self.member)
                    if info.file_size > MAX_BYTES:
                        raise ValueError('Program source exceeds 16 MiB.')
                    with archive.open(info) as stream:
                        data = stream.read(MAX_BYTES + 1)
            else:
                with tarfile.open(self.path, 'r:*') as archive:
                    info = archive.getmember(self.member)
                    if not info.isfile():
                        raise ValueError('Program source must be a regular archive file.')
                    if info.size > MAX_BYTES:
                        raise ValueError('Program source exceeds 16 MiB.')
                    with archive.extractfile(info) as stream:
                        data = stream.read(MAX_BYTES + 1)
        else:
            with self.path.open('rb') as stream:
                data = stream.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Program source exceeds 16 MiB.')
        return data

@dataclass(frozen=True)
class ProgramPair:
    name: str
    urp: ProgramSource | None = None
    script: ProgramSource | None = None
    installations: tuple = ()


def discover_programs(path):
    """Pair sources only by exact parent and stem. No archive extraction."""
    path = Path(path).resolve()
    sources = []
    if path.is_dir():
        for candidate in path.rglob('*'):
            if len(sources) >= 4000:
                raise ValueError('Too many program files; choose a smaller folder.')
            if candidate.suffix.lower() in ('.urp', '.script', '.installation') and candidate.is_file() and not candidate.is_symlink():
                if path not in candidate.resolve().parents:
                    continue
                sources.append(ProgramSource(candidate))
    elif path.suffix.lower() == '.zip':
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                member = PurePosixPath(info.filename)
                if not info.is_dir() and member.suffix.lower() in ('.urp', '.script', '.installation'):
                    if member.is_absolute() or '..' in member.parts or '\\' in info.filename:
                        continue
                    sources.append(ProgramSource(path, info.filename))
                    if len(sources) > 4000:
                        raise ValueError('Too many program files; choose a smaller archive.')
    elif path.name.lower().endswith(('.tar', '.tar.gz', '.tgz')):
        with tarfile.open(path, 'r:*') as archive:
            for index, info in enumerate(archive):
                if index >= 100_000:
                    raise ValueError('Too many archive entries; choose a smaller archive.')
                member = PurePosixPath(info.name)
                if info.isfile() and member.suffix.lower() in ('.urp', '.script', '.installation'):
                    if '..' in member.parts or '\\' in info.name:
                        continue
                    sources.append(ProgramSource(path, info.name))
                    if len(sources) > 4000:
                        raise ValueError('Too many program files; choose a smaller archive.')
    elif path.suffix.lower() in ('.urp', '.script'):
        sources.append(ProgramSource(path))
        other = path.with_suffix('.script' if path.suffix.lower() == '.urp' else '.urp')
        if other.is_file():
            sources.append(ProgramSource(other))
        sources.extend(ProgramSource(p) for p in path.parent.glob('*.installation') if p.is_file() and not p.is_symlink())
    else:
        raise ValueError('Choose a programs folder, ZIP / TAR backup, .urp or .script file.')
    seen_members = set()
    for source in sources:
        if source.member:
            name = _member_name(source.member)
            if name in seen_members:
                raise ValueError(f'Ambiguous duplicate program source: {source.name}')
            seen_members.add(name)
    installations = tuple(s for s in sources if PurePosixPath(s.name).suffix.lower() == '.installation')
    pairs = {}
    for source in (s for s in sources if PurePosixPath(s.name).suffix.lower() != '.installation'):
        key = str(PurePosixPath(_member_name(source.member)).with_suffix('')) if source.member else source.path.with_suffix('').as_posix()
        entry = pairs.setdefault(key, {})
        suffix = PurePosixPath(source.name).suffix.lower()[1:]
        if suffix in entry:
            raise ValueError(f'Ambiguous duplicate program source: {source.name}')
        entry[suffix] = source
    return [ProgramPair(PurePosixPath(key).name, **entry, installations=installations) for key, entry in sorted(pairs.items())]


def installation_context(pair, metadata):
    """Match only supplied files; never load an arbitrary path from URP metadata."""
    origin = pair.urp or pair.script
    parent = PurePosixPath(_member_name(origin.member)).parent if origin.member else origin.path.parent
    reference = metadata.get('installationRelativePath') or metadata.get('installation')
    candidates = pair.installations
    if reference:
        relative = str(reference).replace('\\', '/')
        if not relative.lower().endswith('.installation'):
            relative += '.installation'
        if relative.startswith('/') and metadata.get('directory', '').startswith('/'):
            relative = posixpath.relpath(relative, metadata['directory'])
        if origin.member:
            wanted = posixpath.normpath(str(parent / relative))
            selected = [s for s in candidates if s.member and _member_name(s.member) == wanted]
        else:
            wanted = (parent / relative).resolve()
            selected = [s for s in candidates if not s.member and s.path.resolve() == wanted]
        reason = f'URP references {reference}'
    else:
        selected = [s for s in candidates if (PurePosixPath(_member_name(s.member)).parent if s.member else s.path.parent) == parent]
        reason = 'Only installation in the program folder; no installation reference in URP'
    if len(selected) != 1:
        return None, ('Referenced installation not found in the selected sources.' if reference and not selected else
                      'Installation missing or ambiguous; no installation was guessed.'), None, None, ''
    source = selected[0]
    data = source.read()
    digest = hashlib.sha256(data).hexdigest()
    decoded = decode_installation(data)
    xml = data
    if xml.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(xml)) as stream:
            xml = stream.read(MAX_BYTES + 1)
    root = ElementTree.fromstring(xml)
    version = next((e for e in root if e.tag == 'LastSavedInVersion'), None)
    saved = '.'.join(version.get(k, '') for k in ('major', 'minor', 'bugfix')) if version is not None else ''
    return source, reason, decoded, digest, saved

@dataclass(frozen=True)
class Call:
    name: str
    line: int
    arguments: str

    @property
    def excerpt(self):
        text = ' '.join(self.arguments.split())
        return f'{self.name}({text})'


def calls_in(script):
    """Lex calls while masking strings/comments/PolyScope labels, preserving lines."""
    masked = list(script)
    quote = None
    escape = False
    comment = False
    for i, char in enumerate(script):
        if char == '\n':
            comment = False
        if comment:
            masked[i] = ' ' if char != '\n' else char
        elif quote:
            masked[i] = ' ' if char != '\n' else char
            if escape:
                escape = False
            elif char == '\\':
                escape = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            masked[i] = ' '
        elif char in ('#', '$'):
            comment = True
            masked[i] = ' '
    clean = ''.join(masked)
    pattern = r'\b(movej|movel|movep|movec|optimovej|optimovel|servoj|speedj|speedl|set_payload|set_payload_mass|set_payload_cog|set_target_payload|set_tcp|motion_version_set|sleep|sync|stopj|stopl|socket_open|socket_close|position_deviation_warning|pause_on_error_code)\s*\('
    result = []
    for match in re.finditer(pattern, clean):
        prefix = clean[clean.rfind('\n', 0, match.start()) + 1:match.start()]
        if re.search(r'\b(def|thread)\s*$', prefix):
            continue
        start, depth, end = match.end(), 1, match.end()
        while end < len(clean) and depth:
            depth += (clean[end] == '(') - (clean[end] == ')')
            end += 1
        if depth:
            raise ValueError(f'Unclosed {match[1]} call at line {script.count(chr(10), 0, match.start()) + 1}.')
        # Use original strings for evidence, masked comments for argument parsing.
        args = script[start:end-1]
        result.append(Call(match[1], script.count('\n', 0, match.start()) + 1, args))
    return result


def split_args(text):
    parts, start, depth, quote, escaped = [], 0, 0, None, False
    # Comments outside strings are removed by a small lexical pass.
    cleaned = []
    comment = False
    for char in text:
        if comment:
            if char == '\n':
                comment = False
                cleaned.append(' ')
            continue
        if quote:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            cleaned.append(char)
        elif char == '#':
            comment = True
        else:
            cleaned.append(char)
    text = ''.join(cleaned)
    quote = None
    for index, char in enumerate(text):
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in '([{':
            depth += 1
        elif char in ')]}':
            depth -= 1
        elif char == ',' and depth == 0:
            parts.append(text[start:index].strip())
            start = index+1
    parts.append(text[start:].strip())
    return parts


def arguments(call):
    positional, named = [], {}
    for part in split_args(call.arguments):
        match = re.match(r'^([a-zA-Z_]\w*)\s*=(?!=)(.*)$', part, re.S)
        if match:
            named[match[1]] = match[2].strip()
        else:
            positional.append(part)
    return positional, named


def literal(text):
    try:
        value = ast.literal_eval(text.strip())
        if isinstance(value, bool):
            return None
        if isinstance(value, (float, int)):
            return float(value) if math.isfinite(value) else None
        if isinstance(value, (list, tuple)) and all(type(x) in (float, int) and math.isfinite(x) for x in value):
            return list(value)
    except (ValueError, SyntaxError, TypeError, OverflowError, RecursionError):
        pass
    return None


def argument(call, name, index, default=None):
    positional, named = arguments(call)
    return named.get(name, positional[index] if len(positional) > index else default)

def configuration_values(call, definitions=None):
    """Describe source parameters; substituted initializers are explicitly qualified."""
    parts = []
    notes = []
    invalid = False
    def read(text):
        value = literal(text[1:] if text.startswith('p[') else text)
        if value is None and definitions:
            estimate, note = definition_estimate(text, definitions)
            if note:
                value = estimate
                notes.append(note.strip())
        if isinstance(value, Pose):
            value = list(value.values)
        elif isinstance(value, tuple):
            value = list(value)
        return value
    def vector(name, index, length, unit, default=''):
        nonlocal invalid
        text = argument(call, name, index, default)
        value = read(text)
        if value is None:
            parts.append(f'{name}: dynamic or not supplied')
        elif not isinstance(value, list) or len(value) != length:
            invalid = True
            parts.append(f'{name}: expected {length} numeric elements')
        else:
            parts.append(f'{name}: {value} {unit}')
            if name == 'inertia' and (any(v < 0 for v in value[:3]) or any(abs(v) > 133 for v in value)):
                invalid = True
                parts.append('Inertia is outside documented component constraints')
    if call.name == 'set_tcp':
        vector('pose', 0, 6, '(XYZ in m; rotation vector in rad)')
    else:
        if call.name != 'set_payload_cog':
            mass = read(argument(call, 'm', 0, ''))
            if mass is None:
                parts.append('Mass: dynamic or not supplied')
            elif not isinstance(mass, (int, float)) or mass < 0:
                invalid = True
                parts.append('Mass must be a non-negative number')
            else:
                parts.append(f'Mass: {mass:g} kg')
        if call.name != 'set_payload_mass':
            vector('CoG' if call.name == 'set_payload_cog' else 'cog', 0 if call.name == 'set_payload_cog' else 1, 3, 'm from tool mount')
        if call.name == 'set_target_payload':
            vector('inertia', 2, 6, 'kg·m²', '[0,0,0,0,0,0]')
            transition = read(argument(call, 'transition_time', 3, '0'))
            if transition is None:
                parts.append('transition_time: dynamic or unresolved')
            elif not isinstance(transition, (int, float)) or transition < 0:
                invalid = True
                parts.append('transition_time must be a non-negative number of seconds')
            else:
                parts.append(f'transition_time: {transition:g} s')
    return '; '.join(parts) + '.' + (' ' + ' '.join(dict.fromkeys(notes)) if notes else ''), invalid


@dataclass(frozen=True)
class Finding:
    category: str
    status: str
    detail: str
    line: int = 0
    evidence: str = ''
    reference: int = 0

@dataclass
class ProgramReview:
    pair: ProgramPair
    metadata: dict
    hashes: dict
    script_lines: int
    calls: list
    findings: list
    profile: str

    @property
    def counts(self):
        return Counter(call.name for call in self.calls)

    @property
    def checklist(self):
        priority = {'Review recommended': 3, 'Manual review': 2, 'Source observation': 1, 'Within guideline': 0}
        return [(category, max((f.status for f in self.findings if f.category == category), key=lambda s: priority[s], default='Manual review')) for category in CATEGORIES]


def _deployment_findings(calls, supported, add):
    """Apply the supplied UR workshop as review prompts, never execution proof."""
    payloads = [call for call in calls if call.name == 'set_target_payload']
    for call in payloads:
        transition = literal(argument(call, 'transition_time', 3, '0'))
        inertia = literal(argument(call, 'inertia', 2, '[0,0,0,0,0,0]'))
        detail = ('Review the payload transition against the actual pickup/release. A nonzero transition can help with heavy parts or vacuum gripping; '
                  'the program continues during the transition, so motion timing still needs validation. ')
        if transition == 0:
            detail += 'This call uses an instantaneous transition (0 s). '
        elif isinstance(transition, (int, float)) and transition > 0:
            detail += f'This call specifies {transition:g} s. '
        else:
            detail += 'Transition time is unresolved or invalid; review the source value. '
        if inertia == [0, 0, 0, 0, 0, 0]:
            detail += 'Zero/default inertia is present. Check whether a measured or CAD-derived inertia is needed for a long or widely distributed load; zero inertia is not automatically a fault.'
        else:
            detail += 'Verify inertia at the combined load CoG in kg·m², with axes aligned to the tool flange; source values do not establish the physical load.'
        add(CATEGORIES[9], 'Manual review', detail, call, 10)
    if not payloads:
        add(CATEGORIES[9], 'Manual review', 'No set_target_payload call was found. Confirm the controller supports the selected payload API, and review inertia and pickup/release transition settings in the installation or external code. A missing call is not proof of an incorrect load model.', reference=10)

    # Literal MoveJ/MoveL radii only: zero means a deliberate stop, while MoveP
    # process paths can need smaller radii. Do not infer runtime branch order.
    if supported:
        for call in calls:
            if call.name not in ('movej', 'movel'):
                continue
            radius = literal(argument(call, 'r', 4, '0'))
            if isinstance(radius, (int, float)) and 0 < radius < .015:
                add(CATEGORIES[5], 'Manual review', f'Configured blend radius={radius:g} m ({radius*1000:g} mm). '
                    'The 2024 workshop suggests about 15 mm or more for general paths. Smaller radii can be intentional for clearance or process accuracy; '
                    'review the actual path before increasing this value. This is not a safety limit or a proven motion fault.', call, 11)
    process = [call for call in calls if call.name == 'movep']
    if process:
        add(CATEGORIES[10], 'Manual review', f'{len(process)} MoveP source call(s). Confirm constant TCP speed is required by the process. '
            'Review waits, blocking I/O/gripper calls and operator actions along the executed path; plan a controlled stopl before a required wait. '
            'Source order across definitions and branches does not prove an interrupted blend.', process[0], 12)
    else:
        add(CATEGORIES[10], 'Manual review', 'No MoveP call was found. Verify that waits, branches and continuously checked conditions do not interrupt blended motion. '
            'Use appropriate controlled stops where interruption is required; external helpers and executed paths are not resolved by this review.', reference=12)
    connections = [call for call in calls if call.name in ('socket_open', 'socket_close')]
    if connections:
        add(CATEGORIES[11], 'Manual review', f'{len(connections)} socket open/close source call(s). Review connection lifetime and reuse where practical. '
            'Repeated connection setup can burden the controller; source counts do not establish how often the calls execute.', connections[0], 12)
    add(CATEGORIES[11], 'Manual review', 'Review thread/event load and busy loops for appropriate yielding (such as sync or a wait). '
        'A sync or sleep elsewhere in the file does not prove that every executed loop yields. Review unnecessary URCaps and interface activity if PolyScope is slow.', reference=12)
    diagnostics = [call for call in calls if call.name in ('position_deviation_warning', 'pause_on_error_code')]
    if diagnostics:
        for call in diagnostics:
            add(CATEGORIES[11], 'Source observation', 'Diagnostic command is present in source; execution and runtime settings are not proven. '
                'Position-deviation thresholds are fractions of the controller stop deviation, not millimetres. pause_on_error_code must be re-enabled after a program stop.', call, 13)
    else:
        add(CATEGORIES[11], 'Manual review', 'For supervised troubleshooting, consider position_deviation_warning(True, 0.4), the workshop starting point, '
            'or pause_on_error_code for the specific warning. Confirm support in the applicable controller manual. These optional diagnostics are not installed or executed by this report.', reference=13)


def review_program(pair, profile='Auto (URP version)', limits=None):
    limits = dict(limits or {})
    allowed = {'joint_velocity', 'tcp_velocity', 'joint_acceleration', 'tcp_acceleration'}
    for key, value in limits.items():
        if key not in allowed or type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Motion specifications must be finite positive numbers in the displayed units.')
    metadata, hashes, findings = {}, {}, []
    if pair.urp:
        data = pair.urp.read()
        hashes[pair.urp.identity] = hashlib.sha256(data).hexdigest()
        if data.startswith(b'\x1f\x8b'):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                data = stream.read(MAX_BYTES+1)
            if len(data) > MAX_BYTES:
                raise ValueError('Decompressed URP exceeds 16 MiB.')
        root = ElementTree.fromstring(data)
        if root.tag != 'URProgram':
            raise ValueError('Selected URP is not a URProgram document.')
        metadata = dict(root.attrib)
        # These fields are derived evidence, never trusted claims in the URP.
        metadata.pop('reviewInstallation', None)
        metadata.pop('reviewInstallationNote', None)
    script = ''
    if pair.script:
        data = pair.script.read()
        hashes[pair.script.identity] = hashlib.sha256(data).hexdigest()
        script = data.decode('utf-8-sig', errors='strict')
    calls = calls_in(script)
    installation = None
    installation_note = ''
    installation_version = ''
    if pair.installations or metadata.get('installation') or metadata.get('installationRelativePath'):
        try:
            source, installation_note, installation, digest, installation_version = installation_context(pair, metadata)
            if source:
                hashes[source.identity] = digest
                metadata['reviewInstallation'] = source.identity
        except Exception as exc:
            installation_note = f'Installation could not be read: {exc}'
        metadata['reviewInstallationNote'] = installation_note
    version = metadata.get('lastSavedIn', '') or installation_version
    supported = profile == 'CB-Series / e-Series' or (profile == 'Auto (URP version)' and version.split('.')[0] in ('3', '5'))
    effective_profile = 'CB-Series / e-Series deployment guidance' if supported else 'Unconfirmed robot family; numeric guidance not graded'
    add = lambda category, status, detail, call=None, reference=0: findings.append(Finding(category, status, detail, call.line if call else 0, call.excerpt if call else '', reference))
    if installation is not None:
        add(CATEGORIES[0], 'Source observation' if installation.payload_mass is not None else 'Manual review', f'Saved installation payload: {installation.payload_mass if installation.payload_mass is not None else "unavailable"} kg; CoG={installation.payload_cog} m. {installation_note}. Saved values do not prove the physical tool setup.')
        add(CATEGORIES[2], 'Source observation' if installation.tcp is not None else 'Manual review', f'Saved installation TCP {installation.tcp_name or "(unnamed)"}: {installation.tcp if installation.tcp is not None else "unavailable"}. Translation in m, rotation vector in rad. Program set_tcp calls may override this.')
        for warning in installation.warnings:
            add(CATEGORIES[0] if 'Payload' in warning else CATEGORIES[2], 'Manual review', 'Installation: ' + warning)
        add(CATEGORIES[7], 'Manual review', 'Joint and tool speed limits depend on the configured Normal/Reduced safety modes and application. Saved installation safety serialization is not interpreted as an active speed specification. Advanced overrides can supply verified limits.', reference=9)
    elif installation_note:
        add(CATEGORIES[0], 'Manual review', installation_note)
    if not script:
        for category in CATEGORIES:
            add(category, 'Manual review', 'No matching generated .script file. Select the .script saved with this URP to review commands. URP metadata alone cannot establish executed behavior.')
        return ProgramReview(pair, metadata, hashes, 0, calls, findings, effective_profile)
    if pair.urp and pair.script and PurePosixPath(pair.urp.name).stem != PurePosixPath(pair.script.name).stem:
        add(CATEGORIES[0], 'Manual review', 'Selected URP and script names differ. Confirm they are from the same saved program; source pairing is not verified.')
    definitions = source_definitions(script)
    for names, first_category, changes_category, advice in (
        (('set_payload', 'set_target_payload', 'set_payload_mass', 'set_payload_cog'), CATEGORIES[0], CATEGORIES[1], 'Confirm tool plus workpiece mass, centre of gravity and inertia against the real tool; update payload when the workpiece changes.'),
        (('set_tcp',), CATEGORIES[2], CATEGORIES[3], 'Confirm the configured TCP against the actual tool and each tool change.'),
    ):
        selected = [c for c in calls if c.name in names]
        if not selected:
            add(first_category, 'Manual review', 'No configuration call found in this source. Installation or external code may supply it. ' + advice)
        for i, call in enumerate(selected):
            category = first_category if i == 0 else changes_category
            values, invalid = configuration_values(call, definitions)
            add(category, 'Review recommended' if invalid else 'Source observation', f'{"First source occurrence" if i == 0 else "Additional source occurrence"}; execution order and physical correctness are not proven. ' + values + ' ' + advice, call, 4 if call.name == 'set_target_payload' else 0)
        add(changes_category, 'Manual review', f'{len(selected)} configuration call(s) in source, including definitions and branches. Call counts do not prove updates at every pickup, release or tool change. ' + advice)
    blends = analyze_motion(script, calls, supported, limits, add, CATEGORIES, argument)
    versions = [c for c in calls if c.name == 'motion_version_set']
    version_text = '; '.join(f'line {c.line}: {c.excerpt}' for c in versions[:12]) or 'No explicit motion-version call found.'
    add(CATEGORIES[5], 'Manual review', version_text + (f' ({len(versions)} source occurrences in total.)' if len(versions) > 12 else '') + ' Version 2 can reduce blend radii during planning. A static geometric overlap must not be presented as a proven skipped waypoint.', reference=2)
    add(CATEGORIES[6], 'Manual review', f'{len(blends)} motion calls have nonzero or unresolved radii. Verify continuation through branches, waits, stops and helper functions on the applicable controller. Static source inspection does not establish the executed path.', reference=3)
    _deployment_findings(calls, supported, add)
    return ProgramReview(pair, metadata, hashes, len(script.splitlines()), calls, findings, effective_profile)
