"""Desktop log loading and bounded, zero-filled chart aggregation."""
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from log_parser import JOINTS, read_events, summarize

MAX_LOG_BYTES = 64 * 1024 * 1024


@dataclass
class LogData:
    source: str
    events: list
    skipped: int
    metadata: dict
    diagnostics: dict


def load_log(path, source=None, check_cancel=None):
    path = Path(path)
    paths = [path]
    if path.suffix in ('.txt', '.bak'):
        partner = path.with_suffix('.bak' if path.suffix == '.txt' else '.txt')
        if partner.is_file():
            paths = [path.with_suffix('.bak'), path.with_suffix('.txt')]
    return load_logs(paths, source, check_cancel)


def load_logs(paths, source=None, check_cancel=None):
    """Read backup first, then current, sharing session and incident identities."""
    paths = [Path(path) for path in paths]
    for path in paths:
        if path.stat().st_size > MAX_LOG_BYTES:
            raise ValueError("Log exceeds 64 MiB per file. Choose a smaller log file.")
    metadata = {}
    events, skipped = read_events(paths, metadata, check_cancel=check_cancel)
    metadata['files'] = [path.name for path in paths]
    events.sort(key=lambda e: e['time'], reverse=True)
    if check_cancel:
        check_cancel()
    diagnostics = summarize(events, JOINTS, metadata)
    return LogData(source or ' + '.join(metadata['files']), events, skipped, metadata, diagnostics)


def select_events(events, joint="All joints", code="All codes", category="All incidents", start="", end=""):
    for value in (start, end):
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError()
            except ValueError:
                raise ValueError("Use dates in YYYY-MM-DD format.") from None
    if start and end and start > end:
        raise ValueError("Start date must be on or before end date.")
    return [e for e in events if (joint == "All joints" or e['joint'] == joint)
            and (code == "All codes" or e['code'] == code)
            and (category == "All incidents" or e['category'] == category)
            and (not start or e['date'] >= start) and (not end or e['date'] <= end)]


def chart_series(events, period="Day", start="", end="", split_code=False):
    """Limit chart complexity without dropping counts; return actual bucket width."""
    if not events:
        return [], {}, period
    first = date.fromisoformat(start or min(e['date'] for e in events))
    last = date.fromisoformat(end or max(e['date'] for e in events))
    span = (last - first).days + 1
    if period == 'Month':
        first = first.replace(day=1)
        months = (last.year - first.year) * 12 + last.month - first.month + 1
        step = max(1, (months + 365) // 366)
        def bucket(value):
            return ((value.year - first.year) * 12 + value.month - first.month) // step
        labels = [date((first.year * 12 + first.month - 1 + n) // 12,
                       (first.month - 1 + n) % 12 + 1, 1) for n in range(0, months, step)]
        label = 'Calendar months' if step == 1 else f'{step}-month bins'
    else:
        if period == 'Week':
            first -= timedelta(days=first.weekday())
        span = (last - first).days + 1
        unit = 7 if period == 'Week' else 1
        step = unit * max(1, (span + 366 * unit - 1) // (366 * unit))
        labels = [first + timedelta(days=n) for n in range(0, span, step)]
        def bucket(value):
            return (value - first).days // step
        label = 'Calendar weeks · Monday' if period == 'Week' and step == 7 else f'{step}-day bins'
    counts = Counter((bucket(date.fromisoformat(e['date'])),
                      e['joint'] + (" · " + e['code'] if split_code else "")) for e in events)
    names = sorted({name for _, name in counts})
    # Keep every incident but combine long tails into a labeled series.
    if len(names) > 18:
        totals = Counter()
        for (_, name), count in counts.items():
            totals[name] += count
        keep = {name for name, _ in totals.most_common(17)}
        reduced = Counter()
        for (bucket, name), count in counts.items():
            reduced[bucket, name if name in keep else "Other series"] += count
        counts, names = reduced, sorted(keep) + ["Other series"]
    series = {name: [counts[i, name] for i in range(len(labels))] for name in names}
    return labels, series, label
