"""Bundled UR log parser and evidence summaries.

Adapted from Vibe_UR_Graph_generator at d9b0b6a; kept here so desktop
builds do not depend on an external Git checkout.
"""
from collections import Counter, deque
from datetime import date, datetime, timedelta
from pathlib import Path
import re

UR_CODES = "https://www.universal-robots.com/manuals/EN/HTML/SW5_24/Content/prod-err-codes/topics/"

JOINTS = ("Base", "Shoulder", "Elbow", "Wrist 1", "Wrist 2", "Wrist 3")
# ErrorCodes.pdf lists these families under the indicated headings. The A number
# identifies the joint (0 through 5); the colon suffix is a log-message variant.
CODES = {
    "C153": ("Position deviation", "Position deviates from path"),
    "C159": ("Payload warning", "Payload set to 0 kg; check mass and center of gravity"),
    "C157": ("Collision", "Collision detected by joint"),
    "C158": ("Payload warning", "Payload set to 0 kg; check mass and center of gravity"),
}
# Joint-health codes selected from the supplied ErrorCodes.pdf. These are
# indicators to investigate, not a prediction that a joint will fail.
# CB3-style A values identify a joint; e-Series messages use source 110-115.
HEALTH_CODES = {
    "C163": ("Repeated protective stops", "a_joint"),
    "C174": ("Sudden jump in joint torque", "a_joint_variants"),
    "C261A4": ("Joint temperature too high", "source"),
    "C263A0": ("Motor encoder critical error", "source"),
    "C263A1": ("Motor encoder unavailable", "source"),
    "C263A2": ("Motor encoder calibration invalid", "source"),
    "C263A3": ("Motor encoder calibration quality poor", "source"),
    "C263A4": ("Motor encoder calibration quality poor", "source"),
    "C263A39": ("Motor encoder data CRC invalid", "source"),
    "C263A41": ("Too many invalid motor encoder samples", "source"),
    "C265A0": ("Joint encoder position invalid", "source"),
    "C265A5": ("Joint encoder near operating limits", "source"),
    "C265A6": ("Joint encoder not responding", "source"),
    "C265A26": ("Joint encoder decoding warning", "source"),
    "C265A41": ("Joint encoder read-head temperature out of range", "source"),
    "C265A42": ("Joint encoder signal amplitude low", "source"),
    "C265A47": ("Joint encoder magnetic decoding error", "source"),
    "C265A48": ("Joint encoder signal lost", "source"),
    "C265A53": ("Repeated joint encoder warnings", "source"),
    "C284A0": ("Brake release critical error", "source"),
    "C284A6": ("Motor could not move far enough during brake release", "source"),
    "C306A1": ("Joint did not stop fast enough", "source"),
    "C306A9": ("Joint moved beyond allowable limit; possible brake problem", "source"),
    "C306A10": ("Motor current exceeded limit", "source"),
    "C350A0": ("Joint drifted in failed-brake state", "source"),
    "C350A1": ("Movement blocked by failed brake system", "source"),
    "C350A3": ("Brake system failed to engage", "source"),
    "C361A0": ("Excessive gear torsion", "source"),
    "C361A1": ("Movement blocked after excessive gear torsion", "source"),
}
# Family-level CB3 signals retain their A subcode; only source 110-115 maps a joint.
HEALTH_CODES.update({
    "C46": ("Loose gearbox or bad encoder mounting", "source"),
    "C62": ("Thermal issue", "source"),
    "C75": ("Joint encoder error", "source"),
})
CODE_RE = re.compile(r"^(C\d+A(\d+)):\d+$")


def read_events(paths, metadata: dict | None = None, check_cancel=None) -> tuple[list[dict], int]:
    metadata = metadata if metadata is not None else {}
    metadata.update(first_date="", last_date="", context=[], unmapped=0)
    events = []
    seen = set()
    session = 0
    sessions = {}
    next_session = 0
    skipped = 0
    if isinstance(paths, (str, Path)):
        paths = [paths]
    for file_index, path in enumerate(paths):
        occurrences = Counter()
        with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                if check_cancel:
                    check_cancel()
                if line.startswith("******** Log start"):
                    header = line.strip()
                    occurrences[header] += 1
                    key = (header, occurrences[header])
                    previous = sessions.get(key)
                    # Match dated rotation headers, but never merge repeated
                    # reboot markers within one file or undated sessions.
                    if '(' in header and previous and previous[0] < file_index:
                        session = previous[1]
                    else:
                        next_session += 1
                        session = next_session
                        sessions[key] = (file_index, session)
                    continue
                if " :: " not in line:
                    continue
                fields = [part.strip() for part in line.split(" :: ")]
                if len(fields) < 6:
                    skipped += 1
                    continue
                try:
                    timestamp = datetime.fromisoformat(fields[2])
                except ValueError:
                    skipped += 1
                    continue
                date = timestamp.date().isoformat()
                metadata["first_date"] = min(metadata["first_date"] or date, date)
                metadata["last_date"] = max(metadata["last_date"], date)
                match = CODE_RE.match(fields[4])
                if not match:
                    continue
                code = match.group(1)
                family = re.match(r"C\d+", code).group()
                joint_index = int(match.group(2))
                if code == "C162A0":
                    key = (session, fields[1], code)
                    if key not in seen:
                        seen.add(key)
                        metadata["context"].append({"date": date, "time": timestamp.isoformat(sep=" "), "code": code})
                    continue
                if family in CODES and joint_index < len(JOINTS):
                    category, description = CODES[family]
                else:
                    rule = HEALTH_CODES.get(code)
                    if rule is None:
                        rule = HEALTH_CODES.get(family)
                    if rule is None:
                        continue
                    description, mode = rule
                    if mode == "source":
                        try:
                            joint_index = int(fields[3]) - 110
                        except ValueError:
                            metadata["unmapped"] += 1
                            continue
                    elif mode == "a_joint_variants":
                        joint_index = joint_index if joint_index < 6 else joint_index - 6
                    if not 0 <= joint_index < len(JOINTS):
                        metadata["unmapped"] += 1
                        continue
                    category = "Joint health signal"
                # One incident may appear as :5 and :6 within milliseconds. The
                # controller uptime is identical for that pair, while session keeps
                # reboots from accidentally merging independent incidents.
                key = (session, fields[1], code, joint_index)
                if key in seen:
                    continue
                seen.add(key)
                events.append({"date": timestamp.date().isoformat(),
                               "time": timestamp.isoformat(sep=" "),
                               "joint": JOINTS[joint_index], "code": code,
                               "family": family, "category": category,
                               "description": description, "session": session,
                               "message": " :: ".join(fields[5:])})
    return events, skipped


def summarize(events, joints, coverage):
    """Compare calendar windows; never describe counts as runtime-normalized rates."""
    last = coverage.get('last_date') or max((e['date'] for e in events), default='')
    first = coverage.get('first_date') or min((e['date'] for e in events), default='')
    result = {}
    for period in ('all', '30', '90'):
        end = date.fromisoformat(last) if last else None
        start = (end - timedelta(days=int(period)-1)).isoformat() if end and period != 'all' else first
        cards = []
        for joint in joints:
            selected = sorted((e for e in events if e['joint'] == joint and e['date'] >= start), key=lambda e:e['time'])
            health = [e for e in selected if e['category'] == 'Joint health signal']
            stops = [e for e in selected if e['family'] in ('C153','C157','C158','C159')]
            # A rolling wall-clock window within each log session, not UR operating hours.
            windows = {}; peak = 0; peak_time = ''
            for e in stops:
                window = windows.setdefault(e.get('session',0), deque())
                now = datetime.fromisoformat(e['time'])
                while window and now-window[0] >= timedelta(hours=8):
                    window.popleft()
                window.append(now)
                if len(window) > peak:
                    peak, peak_time = len(window), e['time']
            counts = Counter(e['code'] for e in selected)
            recent_start = (end-timedelta(days=29)).isoformat() if end else ''
            previous_start = (end-timedelta(days=59)).isoformat() if end else ''
            recent = sum(recent_start <= e['date'] <= last for e in events if e['joint']==joint)
            previous = sum(previous_start <= e['date'] < recent_start for e in events if e['joint']==joint)
            status = 'Review health signals' if health else ('Investigate repeated stops' if len(stops)>1 else ('Review incident' if selected else 'No monitored incidents'))
            cards.append(dict(joint=joint,status=status,total=len(selected),health=len(health),stops=len(stops),
                              days=len({e['date'] for e in selected}),last=selected[-1]['time'] if selected else '',
                              peak=peak,peak_time=peak_time,recent=recent,previous=previous,
                              comparison_available=bool(first and first <= previous_start),
                              codes=[{'code':c,'count':n,'url':UR_CODES+'CODE_'+c.split('A')[0][1:]+'.html'} for c,n in counts.most_common()]))
        result[period] = dict(cards=cards,start=start,end=last,
                             payload_context=sum(e['date']>=start for e in coverage.get('context',[])))
    return result
