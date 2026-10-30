"""Bounded, non-executing URScript expression and source-path analysis."""
import ast
from dataclasses import dataclass
import math
import operator
import re


@dataclass(frozen=True)
class Pose:
    values: tuple


@dataclass(frozen=True)
class Record:
    """A numeric URScript struct, never a Python object exposed to attributes."""
    fields: dict


def scalar(value):
    return type(value) in (int, float) and math.isfinite(value)


def resolve(expression, values):
    """Evaluate a small numeric AST allowlist; unknown/runtime inputs stay unknown."""
    if not expression or len(expression) > 8192:
        return None
    try:
        tree = ast.parse(expression.strip().replace('\n', ' '), mode='eval')
        if sum(1 for _ in ast.walk(tree)) > 200:
            return None

        def visit(node, depth=0):
            if depth > 32:
                raise ValueError('Expression too deep')
            sub = lambda n: visit(n, depth + 1)
            if isinstance(node, ast.Constant) and scalar(node.value):
                result = float(node.value)
            elif isinstance(node, ast.Name):
                result = values.get(node.id)
            elif isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 64:
                result = tuple(sub(n) for n in node.elts)
                if not all(scalar(n) for n in result):
                    return None
            elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
                value = sub(node.operand)
                result = (+value if isinstance(node.op, ast.UAdd) else -value) if scalar(value) else None
            elif isinstance(node, ast.BinOp):
                left, right = sub(node.left), sub(node.right)
                operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                              ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
                              ast.Mod: operator.mod, ast.Pow: operator.pow}
                operation = operations.get(type(node.op))
                if not operation or not scalar(left) or not scalar(right):
                    return None
                if isinstance(node.op, ast.Pow) and abs(right) > 32:
                    return None
                result = operation(left, right)
            elif isinstance(node, ast.Subscript):
                if isinstance(node.value, ast.Name) and node.value.id == 'p' and isinstance(node.slice, ast.Tuple):
                    parts = sub(node.slice)
                    return Pose(parts) if parts is not None and len(parts) == 6 else None
                vector, index = sub(node.value), sub(node.slice)
                if isinstance(vector, Pose):
                    vector = vector.values
                result = vector[int(index)] if isinstance(vector, tuple) and scalar(index) and index == int(index) else None
            elif isinstance(node, ast.Attribute):
                record = sub(node.value)
                result = record.fields.get(node.attr) if isinstance(record, Record) else None
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'struct' and 'struct' not in values and not node.args and len(node.keywords) <= 64:
                fields = {item.arg: sub(item.value) for item in node.keywords if item.arg is not None}
                result = Record(fields) if len(fields) == len(node.keywords) and all(v is not None for v in fields.values()) else None
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords and node.func.id not in values:
                functions = {'sqrt': math.sqrt, 'sin': math.sin, 'cos': math.cos, 'tan': math.tan,
                             'atan2': math.atan2, 'abs': abs, 'floor': math.floor, 'ceil': math.ceil,
                             'd2r': math.radians, 'r2d': math.degrees}
                function = functions.get(node.func.id)
                args = [sub(n) for n in node.args]
                result = function(*args) if function and all(scalar(n) for n in args) else None
            else:
                return None
            if scalar(result) and abs(result) > 1e100:
                return None
            return result if result is None or isinstance(result, (Pose, tuple, Record)) or scalar(result) else None
        return visit(tree.body)
    except (ValueError, TypeError, SyntaxError, OverflowError, ZeroDivisionError, IndexError, RecursionError):
        return None


def statements(script):
    """Logical statements, including multiline assignments; strip comments/labels."""
    chars, line, start, depth, quote, escaped, comment = [], 1, 1, 0, None, False, False
    for char in script + '\n':
        if comment and char != '\n':
            continue
        if char == '\n':
            comment = False
        if quote:
            chars.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
            chars.append(char)
        elif char in ('#', '$'):
            comment = True
        elif (char == '\n' or char == ';') and depth == 0:
            text = ''.join(chars).strip()
            if text:
                yield start, line, text
            chars = []
            start = line + (char == '\n')
        else:
            if char in '([{':
                depth += 1
            elif char in ')]}':
                depth -= 1
            chars.append(char)
        if char == '\n':
            line += 1


def source_contexts(script, calls):
    """Conservative basic blocks: never carry values/geometry across runtime flow."""
    contexts, values, group = {}, {}, 0
    call_index = 0
    source_lines = script.splitlines()
    threaded = bool(re.search(r'^\s*thread\b|\brun\s+[A-Za-z_]\w*\s*\(', script, re.M))
    pure = {'sqrt', 'sin', 'cos', 'tan', 'atan2', 'abs', 'floor', 'ceil', 'd2r', 'r2d', 'struct', 'pose_add', 'pose_trans'}
    shadowed = {name: None for name in re.findall(r'^\s*def\s+([A-Za-z_]\w*)\s*\(', script, re.M) if name in pure}
    known = (pure - shadowed.keys()) | {call.name for call in calls}
    for first, last, text in statements(script):
        if re.match(r'^(def|sec|thread|if|elif|else|while|for|end|return|break|continue)\b', text):
            values = {}
            group += 1
            continue
        assignment = re.match(r'^(?:(?:local|global)\s+)?([A-Za-z_]\w*)(\[[^=]*\])?\s*=\s*(.*)$', text, re.S)
        while call_index < len(calls) and calls[call_index].line < first:
            call_index += 1
        current = []
        while call_index < len(calls) and calls[call_index].line <= last:
            current.append(calls[call_index])
            call_index += 1
        ambiguous = any(';' in line for line in source_lines[first-1:last])
        if any(name not in known for name in re.findall(r'\b([A-Za-z_]\w*)\s*\(', text)):
            values = {}
            group += 1
        for call in current:
            contexts[call] = ({}, -call.line) if ambiguous else ({**({} if threaded else values), **shadowed}, group)
        if assignment:
            name, indexed, expression = assignment.groups()
            # A named target can change between adjacent source motion calls.
            # Start a new geometric block even when that assignment is numeric.
            group += 1
            value = None if indexed else resolve(expression, {**values, **shadowed})
            if indexed:
                values = {}
            # A runtime call can mutate globals; do not retain prior assumptions.
            if value is None and '(' in expression:
                values = {}
                group += 1
            values[name] = value
        elif current and re.match(r'^(movej|movel|movep|movec|optimovej|optimovel)\s*\(', text):
            pass
        else:
            # Configuration changes, helper calls, waits, speed/servo moves and
            # unsupported statements interrupt the provable waypoint chain.
            group += 1
            if not current:
                values = {}
    return contexts


def source_definitions(script):
    """Unique unconditional initializers for *conditional* source calculations.

    These are not runtime facts: helpers may precede their initialization and
    external code can mutate globals. Callers must display that qualification.
    Exclude reassignment, field/index mutation, parameter shadowing and locals.
    """
    logical = list(statements(script))
    wrapper = False
    if logical and re.match(r'^(def|sec)\b', logical[0][2]):
        depth = 0
        for index, (_, _, text) in enumerate(logical):
            if re.match(r'^(def|sec|thread|if|while|for)\b', text):
                depth += 1
            elif re.match(r'^end\b', text):
                depth -= 1
                if depth == 0:
                    wrapper = index == len(logical) - 1
                    break
    definitions, writes, blocked, stack = {}, {}, set(), []
    for first, last, text in logical:
        header = re.match(r'^(def|sec|thread)\s+\w+\s*\((.*?)\)', text, re.S)
        if header:
            blocked.update(re.findall(r'\b[A-Za-z_]\w*\b', header.group(2)))
            stack.append(header.group(1))
            continue
        if re.match(r'^(if|while|for)\b', text):
            stack.append('flow')
            continue
        if re.match(r'^end\b', text):
            if stack:
                stack.pop()
            continue
        assignment = re.match(r'^(?:(local|global)\s+)?([A-Za-z_]\w*)([\[.][^=]*)?\s*=\s*(.*)$', text, re.S)
        if not assignment:
            continue
        scope, name, indexed, expression = assignment.groups()
        writes[name] = writes.get(name, 0) + 1
        allowed_scope = not stack or (wrapper and stack in (['def'], ['sec']))
        if indexed or scope == 'local' or not allowed_scope:
            blocked.add(name)
        else:
            definitions[name] = (expression, first)
    shadowed = {name: None for name in re.findall(r'^\s*def\s+([A-Za-z_]\w*)\s*\(', script, re.M)}
    pending = {name: value for name, value in definitions.items() if writes[name] == 1 and name not in blocked}
    resolved = {}
    # Definitions follow execution order. A reference to a later initializer
    # cannot establish an earlier initialized value.
    for name, (expression, line) in pending.items():
        value = resolve(expression, {**{key: item[0] for key, item in resolved.items()}, **shadowed})
        if value is not None:
            resolved[name] = (value, line)
    for match in re.finditer(r'^\s*def\s+([A-Za-z_]\w*)\s*\(', script, re.M):
        resolved[match.group(1)] = (None, script.count('\n', 0, match.start()) + 1)
    return resolved


def definition_estimate(expression, definitions):
    """Return a bounded substituted value and an auditable assumption note."""
    value = resolve(expression, {name: item[0] for name, item in definitions.items()})
    if value is None:
        return None, ''
    names = sorted(set(re.findall(r'\b[A-Za-z_]\w*\b', expression or '')) & definitions.keys())
    if not names:
        return value, ''
    def describe(name):
        item, line = definitions[name]
        return f'{name}={item:.9g} (line {line})' if scalar(item) else f'{name} (line {line})'
    note = 'Source-definition estimate using ' + ', '.join(describe(name) for name in names) + '. Initialization order, runtime changes and execution are not proven. '
    return value, note


def relative_distance(start_expression, end_expression, values):
    """Translation between a named pose and its rigid offset, independent of origin.

    Never equate repeated get_actual_tcp_pose() calls or arbitrary expressions.
    This is source geometry only; the named pose must be unchanged between moves.
    """
    try:
        start = ast.parse(start_expression.strip(), mode='eval').body
        end = ast.parse(end_expression.strip(), mode='eval').body
        if (isinstance(start, ast.Name) and isinstance(end, ast.Call)
                and isinstance(end.func, ast.Name) and end.func.id in ('pose_add', 'pose_trans')
                and end.func.id not in values and len(end.args) == 2 and not end.keywords
                and isinstance(end.args[0], ast.Name) and start.id == end.args[0].id):
            offset = resolve(ast.unparse(end.args[1]), values)
            if isinstance(offset, Pose):
                return math.hypot(*offset.values[:3])
    except (AttributeError, SyntaxError, ValueError, RecursionError):
        pass
    return None


def distance(start, end, via=None):
    """Unblended translational length (metres), including a three-point circle."""
    if not isinstance(start, Pose) or not isinstance(end, Pose):
        return None
    a, c = start.values[:3], end.values[:3]
    if via is None:
        return math.dist(a, c)
    if not isinstance(via, Pose):
        return None
    b = via.values[:3]
    u, v = [a[i]-b[i] for i in range(3)], [c[i]-b[i] for i in range(3)]
    ab, bc, ac = math.dist(a, b), math.dist(b, c), math.dist(a, c)
    cross = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
    area2 = math.hypot(*cross)
    if min(ab, bc, ac) < 1e-12 or area2 < 1e-12:
        return None
    radius = ab * bc * ac / (2 * area2)
    angle = math.atan2(area2, sum(x*y for x, y in zip(u, v)))
    length = radius * 2 * (math.pi - angle)
    return length if math.isfinite(length) and length > 0 else None


def analyze_motion(script, calls, supported, limits, add, categories, argument):
    """Report each motion's resolved inputs, then compare adjacent source targets."""
    contexts = source_contexts(script, calls)
    definitions = source_definitions(script)
    motions = [c for c in calls if c.name in ('movej', 'movel', 'movep', 'movec',
                                              'optimovej', 'optimovel', 'servoj', 'speedj', 'speedl')]
    records = []
    for index, call in enumerate(motions):
        values, group = contexts.get(call, ({}, -index-1))
        estimates = {}
        def read(name, position, default=None):
            expression = argument(call, name, position, default)
            value = resolve(expression, values)
            if value is None:
                estimate, note = definition_estimate(expression, definitions)
                if note:
                    value = estimate
                    estimates[name] = note
            return value
        joint = call.name in ('movej', 'speedj', 'optimovej', 'servoj')
        optimized = call.name.startswith('optimo')
        circular = call.name == 'movec'
        velocity_mode = call.name in ('speedj', 'speedl')
        acceleration = read('a', 2 if circular else 1, None if velocity_mode or call.name == 'servoj' else '0.3' if optimized else '1.4' if joint else '1.2')
        velocity = read('v', 3 if circular else 2, None if call.name == 'servoj' else '0.3' if optimized else '1.05' if joint else '0.25')
        duration = read('t', 3, '0') if call.name in ('movej', 'movel') else 0.
        if velocity_mode:
            vector = read('qd' if joint else 'xd', 0)
            if isinstance(vector, tuple) and len(vector) == 6:
                velocity = max(abs(x) for x in vector) if joint else math.sqrt(sum(x*x for x in vector[:3]))
                if not joint:
                    rotation = math.sqrt(sum(x*x for x in vector[3:]))
                    add(categories[7], 'Manual review', f'Configured rotational velocity magnitude={rotation:g} rad/s; no TCP rotational velocity specification supplied.', call, 7)
            else:
                velocity = None
            if ('qd' if joint else 'xd') in estimates:
                estimates['v'] = estimates['qd' if joint else 'xd']
        for name, value, category, suffix in (('a', acceleration, categories[4], 'acceleration'),
                                               ('v', velocity, categories[7], 'velocity')):
            unit = 'fraction' if optimized else ('rad/s' if joint else 'm/s') + ('²' if name == 'a' else '')
            prefix = f'Configured {name}={value:g} {unit}. ' if scalar(value) else f'{suffix.capitalize()} is unresolved or invalid. '
            reference = 5 if optimized else 6 if circular else 7 if velocity_mode else 8 if call.name == 'servoj' else 0
            if call.name == 'servoj':
                status, detail = 'Manual review', prefix + 'ServoJ a/v are unused in this controller API; target timing, gain and runtime trajectory determine motion.'
            elif not scalar(value):
                status, detail = 'Manual review', prefix + 'Runtime input, unsupported expression or uncertain control flow prevents a numeric check.'
            elif not scalar(duration) or duration < 0:
                status, detail = 'Manual review', prefix + 'Duration is unresolved or invalid; cannot determine whether a/v are used.'
            elif duration > 0:
                status, detail = 'Manual review', prefix + f'Duration t={duration:g} s overrides a/v; actual velocity and acceleration require trajectory timing.'
            elif value < 0 or (value == 0 and not (velocity_mode and name == 'v')):
                status, detail = 'Review recommended', prefix + f'Non-positive {suffix}; check the generated command.'
            elif optimized:
                status = 'Review recommended' if value > 1 else 'Manual review'
                detail = prefix + ('Exceeds the documented OptiMove fraction range (0, 1]. ' if value > 1 else 'Within the documented OptiMove fraction range (0, 1]. ') + 'Absolute velocity/acceleration varies with the trajectory; physical limits are not established.'
            else:
                key = ('joint_' if joint else 'tcp_') + suffix
                limit = limits.get(key)
                guideline = False
                if limit is None and name == 'a' and supported:
                    limit = math.radians(800) if joint else 2.5
                    guideline = True
                if limit is None:
                    status = 'Manual review'
                    detail = prefix + f'Enter the applicable {"joint" if joint else "TCP"} {suffix} specification to check this value.'
                else:
                    status = 'Review recommended' if value > limit else ('Within guideline' if guideline else 'Source observation')
                    basis = 'UR suggested starting value' if guideline else 'user-supplied specification'
                    detail = prefix + f'{"Exceeds" if value > limit else "Within"} {basis} ≤{limit:.6g} {unit}. '
                    detail += 'This is not a safety limit or measured acceleration.' if guideline else 'Configured source value only; runtime motion is not certified.'
            note = estimates.get(name, '')
            if note:
                if status in ('Within guideline', 'Source observation'):
                    status = 'Manual review'
                detail += ' ' + note
            add(category, status, detail, call, reference)
        if call.name == 'speedl':
            rotation_accel = read('aRot', 3, argument(call, 'a', 1))
            add(categories[4], 'Review recommended' if scalar(rotation_accel) and rotation_accel <= 0 else 'Manual review',
                (f'Configured aRot={rotation_accel:g} rad/s². ' if scalar(rotation_accel) else 'Rotational acceleration is unresolved. ') + 'TCP rotational acceleration needs a separate applicable specification. ' + estimates.get('aRot', ''), call, 7)
        if call.name not in ('servoj', 'speedj', 'speedl'):
            radius = read('r', 3 if call.name in ('movep', 'optimovej', 'optimovel') else 4, '0')
            target = read('pose_to' if circular else 'q' if call.name == 'movej' else 'goal' if optimized else 'pose', 1 if circular else 0)
            target_expression = argument(call, 'pose_to' if circular else 'q' if call.name == 'movej' else 'goal' if optimized else 'pose', 1 if circular else 0, '')
            via = read('pose_via', 0) if circular else None
            records.append({'call': call, 'group': group, 'target': target, 'via': via,
                            'radius': radius, 'distance': None, 'outgoing': None, 'previous': None,
                            'target_expression': target_expression, 'values': values,
                            'estimates': estimates, 'relative': False})
    for index, record in enumerate(records):
        if index == 0 or records[index-1]['group'] != record['group']:
            continue
        previous = records[index-1]
        if record['call'].name not in ('movel', 'movep', 'movec', 'optimovel'):
            continue
        if record['call'].name == 'movec' and not isinstance(record['via'], Pose):
            continue
        length = distance(previous['target'], record['target'], record['via'])
        if length is None and record['call'].name != 'movec':
            length = relative_distance(previous['target_expression'], record['target_expression'], record['values'])
            record['relative'] = length is not None
        record['distance'] = length
        record['previous'] = previous
        previous['outgoing'] = length
    known = []
    for record in records:
        call, radius, length = record['call'], record['radius'], record['distance']
        if length is None:
            detail = 'Move distance unresolved: initial pose, joint-space trajectory, target/frame expression or intervening control flow is unknown.'
        else:
            known.append(length)
            detail = f'Unblended move distance={length:.6g} m from preceding source waypoint (translation only).'
            if record['relative']:
                detail += ' Derived from a rigid offset of the same named pose; runtime pose stability and execution require verification.'
            if record['estimates'] or record['previous']['estimates']:
                detail += ' Source-definition substitution is conditional on initialization and unchanged values.'
        add(categories[8], 'Source observation' if length is not None else 'Manual review', detail, call, 6 if call.name == 'movec' else 3)
        if not scalar(radius):
            add(categories[5], 'Manual review', 'Blend radius is unresolved; no geometric pass is inferred.', call, 3)
            continue
        detail = f'Configured radius r={radius:g} m. '
        detail += record['estimates'].get('r', '')
        issues = []
        if radius < 0:
            issues.append('Negative radius requires correction.')
        for direction, adjacent in (('incoming', length), ('outgoing', record['outgoing'])):
            if adjacent is not None:
                detail += f'{direction.capitalize()} move distance={adjacent:.6g} m. '
                if radius > adjacent:
                    issues.append(f'Radius exceeds {direction} move distance.')
        previous = record['previous']
        if length is not None and previous and scalar(previous['radius']) and radius > 0 and previous['radius'] > 0:
            total = radius + previous['radius']
            detail += f'Adjacent radii total={total:.6g} m. '
            if total > length:
                issues.append('Adjacent radii exceed connecting move distance; potential geometric overlap.')
        if issues:
            status = 'Review recommended'
            detail += ' '.join(issues) + ' '
        else:
            status = 'Source observation' if radius == 0 else 'Manual review'
        detail += 'Source geometry does not prove a skipped waypoint; controller planning and execution require verification.'
        add(categories[5], status, detail, call, 3)
    add(categories[8], 'Source observation' if known else 'Manual review',
        f'Known source path distance={sum(known):.6g} m across {len(known)} of {len(records)} move(s). '
        'This sums resolvable unblended translational segments only; unknown segments, first approach, rotations, branches and repetitions are excluded. It is not total executed travel.', reference=3)
    if not motions:
        for category in (categories[4], categories[7]):
            add(category, 'Manual review', 'No supported motion calls found. External code and generated motions are not resolved.')
    return [r['call'] for r in records if not scalar(r['radius']) or r['radius'] != 0]
