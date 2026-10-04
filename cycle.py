"""Period tracker (lite): log start dates, estimate the next one from your own cycles.
Private by design: stored only on this device, never spoken unless you ask, blocked in guest mode,
never sent online, and nothing is saved without a 'yes'. Estimates only, never medical advice.
Typical adult cycles are 21 to 35 days (ACOG)."""
import re
import sqlite3
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).parent
TYPICAL = 28              # used until there are at least two logged periods
NORMAL = (21, 35)         # common adult cycle range
SAME_PERIOD_DAYS = 10     # two logs this close are the same period (a correction)

LOG = re.compile(r"\b(?:my period (?:started|came|began|has started)|(?:i got|i've got|got) my period|my period is here)\b")
NEXT = re.compile(r"\b(?:when (?:is|will|should) my (?:next )?period|my next period|my period (?:is )?due)\b")
LAST = re.compile(r"\bwhen (?:did|was) my (?:last )?period\b|\bmy last period\b")
LENGTH = re.compile(r"\bhow long is my (?:menstrual |period )?cycle\b|\bmy (?:average |menstrual )?cycle length\b")
FORGET = re.compile(r"\b(?:delete|forget|erase|clear) (?:all )?my (?:period|cycle|menstrual) (?:data|dates|history|log)\b")
YES = re.compile(r"^(?:yes|yeah|yep|sure|ok|okay|please|correct|right)\b")
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}

_said_estimate_note = False
_pending_log = None        # a date waiting for "yes" before it's saved


# ---------- Storage (on this device only) ----------
def _db():
    db = sqlite3.connect(HERE / "memories.db")
    db.execute("CREATE TABLE IF NOT EXISTS cycle_log (id INTEGER PRIMARY KEY, day TEXT, created REAL)")
    return db


def _starts():
    db = _db()
    days = [date.fromisoformat(d) for (d,) in db.execute("SELECT day FROM cycle_log ORDER BY day")]
    db.close()
    return days


def _save(day):
    db = _db()
    near = db.execute("SELECT id FROM cycle_log WHERE ABS(julianday(day) - julianday(?)) < ?",
                      (day.isoformat(), SAME_PERIOD_DAYS)).fetchone()
    if near:                                           # the same period, logged again: a correction
        db.execute("UPDATE cycle_log SET day = ? WHERE id = ?", (day.isoformat(), near[0]))
        reply = f"Updated: I've noted your period as starting on {_fmt(day)}."
    else:
        db.execute("INSERT INTO cycle_log (day, created) VALUES (?, ?)", (day.isoformat(), time.time()))
        reply = f"Got it, I've noted it for {_fmt(day)}. This stays only on this device."
    db.commit()
    db.close()
    return reply


def clear_cycle_log():
    db = _db()
    db.execute("DELETE FROM cycle_log")
    db.commit()
    db.close()


# ---------- Helpers ----------
def _fmt(d):
    return f"{d:%A}, {d.day} {d:%B}"


def _when(lower):
    """'today' / 'yesterday' / '2 days ago' / 'on monday' -> a date (never in the future)."""
    t = date.today()
    if "yesterday" in lower:
        return t - timedelta(days=1)
    m = re.search(r"\b(\d+|" + "|".join(NUMS) + r") days? ago\b", lower)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMS[m.group(1)]
        return t - timedelta(days=n)
    m = re.search(r"\b(?:on |last )?(" + "|".join(DAYS) + r")\b", lower)
    if m:
        return t - timedelta(days=(t.weekday() - DAYS.index(m.group(1))) % 7)
    return t


def _lengths(starts):
    """Cycle lengths between logged periods, ignoring obvious logging slips."""
    return [(b - a).days for a, b in zip(starts, starts[1:]) if 15 <= (b - a).days <= 60]


def _note_once():
    global _said_estimate_note
    if _said_estimate_note:
        return ""
    _said_estimate_note = True
    return " This is only an estimate, so please don't rely on it for birth control."


def _irregular_note(lengths):
    if lengths and (min(lengths) < NORMAL[0] or max(lengths) > NORMAL[1] or max(lengths) - min(lengths) > 7):
        return (" Your cycles vary quite a bit. That can be normal, "
                "but it's worth mentioning to a doctor if it worries you.")
    return ""


# ---------- Entry points ----------
def is_cycle_question(text):
    """Only clear period-tracker sentences count, never 'free period', 'time period' or 'cycle to work'."""
    lower = text.lower()
    return bool(_pending_log or LOG.search(lower) or NEXT.search(lower) or LAST.search(lower)
                or LENGTH.search(lower) or FORGET.search(lower))


def handle_cycle(text):
    """Returns a reply about period tracking, or None."""
    global _pending_log
    lower = text.lower().strip(" .!?,")

    if _pending_log:                                   # answer to "should I note it?"
        day, _pending_log = _pending_log, None
        if YES.search(lower):
            return _save(day)
        if not LOG.search(lower):
            return "Okay, I won't note anything."

    if FORGET.search(lower):
        clear_cycle_log()
        return "Done. I've deleted all your period dates from this device."

    if LOG.search(lower):                              # nothing is saved without a "yes"
        _pending_log = _when(lower)
        return f"Just to check: should I note your period as starting on {_fmt(_pending_log)}?"

    starts = _starts()
    if LAST.search(lower):
        if not starts:
            return "I don't have any period dates saved yet. When it starts, just tell me: my period started today."
        return f"Your last period started on {_fmt(starts[-1])}."

    if LENGTH.search(lower):
        lengths = _lengths(starts)
        if not lengths:
            return "I need at least two period dates to work out your cycle. Just tell me each time it starts."
        avg = round(sum(lengths) / len(lengths))
        return (f"Your cycle has averaged {avg} days, over the last {len(lengths)} "
                f"cycle{'s' if len(lengths) > 1 else ''}." + _irregular_note(lengths))

    if NEXT.search(lower):
        if not starts:
            return "I don't have any period dates saved yet. When it starts, just tell me: my period started today."
        lengths = _lengths(starts)[-6:]
        if lengths:
            avg = round(sum(lengths) / len(lengths))
            basis = f"Based on your last {len(lengths)} cycle{'s' if len(lengths) > 1 else ''}, averaging {avg} days,"
        else:
            avg = TYPICAL
            basis = "Based on a typical 28-day cycle,"
        due = starts[-1] + timedelta(days=avg)
        if (date.today() - due).days > 2:
            reply = ("Based on your past cycles, it may be a few days late. Cycles often vary, "
                     "and stress, sleep or travel can shift them. If you're concerned, a doctor can help.")
        else:
            reply = f"{basis} your next period may start around {_fmt(due)}, give or take a few days."
            if not lengths:
                reply += " Once you log a couple more, I'll use your own cycle."
        return reply + _irregular_note(lengths) + _note_once()

    return None