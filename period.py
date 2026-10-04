"""Plans and progress over a period: 'What's on my schedule next week?', 'What did I do this week?'.
Reads the schedule list (dates saved as [date: ...]), the tasks list and the focus history. No LLM, no internet."""
import calendar
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from lists import STARTER_LISTS

HERE = Path(__file__).parent
SCHEDULE_LIST = next((n for n in STARTER_LISTS if n.startswith("sched")), "schedule")
TASKS_LIST = "tasks"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

PLANS_Q = re.compile(r"\b(?:schedule|plans?|planned|calendar|meetings?|events?|coming up"
                     r"|what do i have|what have i got|am i free|am i busy)\b")
WORK_Q = re.compile(r"\bwhat did i (?:do|get done|work on|achieve)\b|\b(?:work|focus|progress) (?:summary|report)\b"
                    r"|\bhow did i do\b|\bsummary of (?:this|last|the|my) (?:week|month)\b"
                    r"|\b(?:my|this|last) (?:week|month) (?:summary|so far)\b")


def parse_period(lower, today=None):
    """'next week' -> (start, end, 'next week'), or None if no period was mentioned."""
    t = today or date.today()

    def month(first):
        return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])

    if re.search(r"\bday after tomorrow\b", lower):
        d = t + timedelta(days=2)
        return d, d, "the day after tomorrow"
    if re.search(r"\btomorrow\b", lower):
        d = t + timedelta(days=1)
        return d, d, "tomorrow"
    if re.search(r"\b(?:today|tonight)\b", lower):
        return t, t, "today"
    if re.search(r"\bthis weekend\b|\bthe weekend\b", lower):
        sat = t + timedelta(days=(5 - t.weekday()) % 7)
        return sat, sat + timedelta(days=1), "this weekend"
    if re.search(r"\bnext week\b", lower):
        mon = t + timedelta(days=7 - t.weekday())
        return mon, mon + timedelta(days=6), "next week"
    if re.search(r"\b(?:last|past|previous) week\b", lower):
        mon = t - timedelta(days=t.weekday() + 7)
        return mon, mon + timedelta(days=6), "last week"
    if re.search(r"\b(?:next|coming) (?:7|seven) days\b", lower):
        return t, t + timedelta(days=6), "in the next 7 days"
    if re.search(r"\bthis week\b", lower):
        mon = t - timedelta(days=t.weekday())
        return mon, mon + timedelta(days=6), "this week"
    if re.search(r"\bnext month\b", lower):
        return (*month((t.replace(day=1) + timedelta(days=32)).replace(day=1)), "next month")
    if re.search(r"\b(?:last|previous) month\b", lower):
        return (*month((t.replace(day=1) - timedelta(days=1)).replace(day=1)), "last month")
    if re.search(r"\bthis month\b", lower):
        return (*month(t.replace(day=1)), "this month")
    m = re.search(r"\b(?:on |this |next )?(" + "|".join(DAYS) + r")\b", lower)
    if m:
        d = t + timedelta(days=(DAYS.index(m.group(1)) - t.weekday()) % 7)
        return d, d, f"on {d:%A}"
    return None


def _cap(label):
    return label[0].upper() + label[1:]


def _day(d):
    return f"{d:%A}, {d.day} {d:%B}"


def _hm(minutes):
    h, m = divmod(int(minutes), 60)
    parts = ([f"{h} hour{'s' if h > 1 else ''}"] if h else []) + ([f"{m} minute{'s' if m != 1 else ''}"] if m else [])
    return " ".join(parts) or "0 minutes"


def _dated_items(memory):
    """Schedule items with a real date: [(date, 'meeting with Rahul'), ...]."""
    out = []
    for item in memory.list_get(SCHEDULE_LIST):
        m = re.search(r"\[date: (.*?)\]", item)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1).strip(), "%A, %d %B %Y").date()
        except ValueError:
            continue
        out.append((d, item.split(" [date:")[0].strip()))
    return sorted(out)


def _focus_rows(start, end):
    db = sqlite3.connect(HERE / "memories.db")
    db.execute("CREATE TABLE IF NOT EXISTS focus_log "
               "(id INTEGER PRIMARY KEY, day TEXT, minutes INTEGER, task TEXT, energy TEXT, ended REAL)")
    rows = db.execute("SELECT minutes, energy FROM focus_log WHERE day BETWEEN ? AND ?",
                      (start.isoformat(), end.isoformat())).fetchall()
    db.close()
    return rows


def plans_text(memory, start, end, label):
    items = [(d, txt) for d, txt in _dated_items(memory) if start <= d <= end]
    if not items:
        return f"You don't have anything on your schedule {label}."
    if start == end:
        return f"{_cap(label)} you have: " + ", and ".join(txt for _, txt in items) + "."
    n = len(items)
    lines = [f"On {_day(d)}: {txt.rstrip('.')}." for d, txt in items[:8]]
    more = f" And {n - 8} more." if n > 8 else ""
    return f"{_cap(label)} you have {n} thing{'s' if n > 1 else ''}. " + " ".join(lines) + more


def work_text(memory, start, end, label):
    today = date.today()
    if start > today:
        return f"{_cap(label)} hasn't started yet. You can ask me what's planned instead."
    stop = min(end, today)
    so_far = " so far" if end >= today else ""
    parts = []
    rows = _focus_rows(start, stop)
    if rows:
        n, total = len(rows), sum(r[0] for r in rows)
        parts.append(f"{_cap(label)}{so_far}, you did {n} focus session{'s' if n > 1 else ''}, {_hm(total)} in total.")
        felt = [e for _, e in rows if e]
        if felt:
            parts.append(f"You felt mostly {max(set(felt), key=felt.count)}.")
    else:
        parts.append(f"I don't have any focus sessions {label}{so_far}.")
    done = [txt for d, txt in _dated_items(memory) if start <= d <= stop]
    if done:
        parts.append(f"You had {len(done)} thing{'s' if len(done) > 1 else ''} on your schedule: "
                     + "; ".join(done[:4]) + ".")
    later = [(d, txt) for d, txt in _dated_items(memory) if today < d <= end]
    if later:
        parts.append("Still coming up: " + "; ".join(f"{d:%A}: {txt}" for d, txt in later[:3]) + ".")
    tasks = memory.list_get(TASKS_LIST)
    if tasks:
        parts.append(f"You have {len(tasks)} open task{'s' if len(tasks) > 1 else ''}.")
    return " ".join(parts)


def handle_period(text, memory):
    """Returns a reply for questions about plans or progress over a period, otherwise None."""
    lower = text.lower().strip(" .!?,")
    period = parse_period(lower)
    if not period:
        return None
    start, end, label = period
    if WORK_Q.search(lower):
        return work_text(memory, start, end, label)
    if PLANS_Q.search(lower):
        return plans_text(memory, start, end, label)
    return None