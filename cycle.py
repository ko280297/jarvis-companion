"""Period tracker: private, on this device only, never spoken unless asked (except a discreet reminder
you set up yourself), blocked in guest mode, never sent online, and no period date is saved without a 'yes'.
Estimates only, never medical advice. Typical adult cycles are 21 to 35 days, and periods last up to
about 7 days (ACOG)."""
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
TYPICAL = 28              # used until there are at least two logged periods
NORMAL = (21, 35)         # common adult cycle range
LONG_PERIOD = 7           # periods longer than this are worth mentioning to a doctor
SAME_PERIOD_DAYS = 10     # two start logs this close are the same period (a correction)
DEFAULT_PHRASE = "A gentle heads-up: your self-care days may be coming up soon."

# Symptoms we know (anything else is not logged, so "log cramps and chest pain" goes to emergency help instead)
SYMPTOMS = {
    "cramps": r"cramps?|crams?|period pain|stomach ?ache|tummy ?ache",
    "headache": r"headaches?|migraines?",
    "bloating": r"bloat(?:ing|ed)?",
    "back pain": r"back ?(?:pain|ache)",
    "tiredness": r"tired(?:ness)?|fatigue|low energy|exhausted",
    "mood swings": r"mood swings?|irritab\w*|moody",
    "low mood": r"low mood|feeling low|sad(?:ness)?",
    "anxiety": r"anxi\w*",
    "acne": r"acne|pimples?|breakouts?",
    "tender breasts": r"tender breasts?|breast (?:pain|tenderness)",
    "nausea": r"nausea|nauseous",
    "cravings": r"cravings?",
    "trouble sleeping": r"insomnia|trouble sleeping|can'?t sleep|poor sleep",
    "heavy flow": r"heavy (?:flow|bleeding)",
    "light flow": r"light (?:flow|bleeding)|spotting",
}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
WHEN = (r"(?:\s+(?:today|yesterday|(?:\d+|one|two|three|four|five|six|seven) days? ago"
        r"|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)))?")

LOG = re.compile(r"\b(?:my period (?:started|came|began|has started)|(?:i got|i've got|got) my period|my period is here)\b")
END = re.compile(r"\bmy period (?:just )?(?:ended|stopped|finished|is over|got over|has ended)\b")
NEXT = re.compile(r"\b(?:when (?:is|will|should) my (?:next )?period|my next period|my period (?:is )?due)\b")
LAST = re.compile(r"\bwhen (?:did|was) my (?:last )?period\b|\bmy last period\b")
LENGTH = re.compile(r"\bhow long is my (?:menstrual |period )?cycle\b|\bmy (?:average |menstrual )?cycle length\b")
PERIOD_LEN = re.compile(r"\bhow long (?:does|do) my periods? (?:usually )?last\b|\bmy (?:average )?period length\b")
CYCLE_DAY = re.compile(r"\b(?:what|which) day of my (?:cycle|period)\b|\bmy cycle day\b|\bam i on my period\b")
SYMLOG = re.compile(r"^(?:please\s+)?(?:log|track|record|note|note down|jot down)\s+(?:my\s+)?(?:symptoms?:?\s*)?(.+?)(" + WHEN + r")$")
PATTERN = re.compile(r"\bdo i (?:usually |always |often )?(?:get|have) (.+?) (?:before|during|around|after) my periods?\b"
                     r"|\bpatterns? in my symptoms\b|\bwhat symptoms do i (?:usually |normally |often )?(?:get|have)\b")
SUMMARY = re.compile(r"\b(?:cycle|period) (?:summary|report)\b|\bsummary of my (?:cycle|periods?)\b")
NUDGE_SET = re.compile(r"\bremind me (\d+|one|two|three|four|five|six|seven) days? before my (?:next )?period\b")
NUDGE_OFF = re.compile(r"\b(?:stop|cancel|turn off|no more) (?:my )?period reminders?\b")
PHRASE_SET = re.compile(r"\b(?:for|in) my period reminders?,? (?:just )?say (.+)$")
FORGET = re.compile(r"\b(?:delete|forget|erase|clear) (?:all )?my (?:period|cycle|menstrual) (?:data|dates|history|log)\b")
YES = re.compile(r"^(?:yes|yeah|yep|sure|ok|okay|please|correct|right)\b")

_said_estimate_note = False
_pending_log = None        # a start date waiting for "yes" before it's saved


# ---------- Storage (on this device only) ----------
def _db():
    db = sqlite3.connect(HERE / "memories.db")
    db.execute("CREATE TABLE IF NOT EXISTS cycle_log (id INTEGER PRIMARY KEY, day TEXT, created REAL)")
    if "end_day" not in [r[1] for r in db.execute("PRAGMA table_info(cycle_log)")]:
        db.execute("ALTER TABLE cycle_log ADD COLUMN end_day TEXT")
    db.execute("CREATE TABLE IF NOT EXISTS symptom_log (id INTEGER PRIMARY KEY, day TEXT, symptom TEXT)")
    db.execute("CREATE TABLE IF NOT EXISTS cycle_settings (key TEXT PRIMARY KEY, value TEXT)")
    return db


def _periods():
    """[(id, start, end or None), ...] oldest first."""
    db = _db()
    rows = db.execute("SELECT id, day, end_day FROM cycle_log ORDER BY day").fetchall()
    db.close()
    return [(i, date.fromisoformat(d), date.fromisoformat(e) if e else None) for i, d, e in rows]


def _starts():
    return [s for _, s, _ in _periods()]


def _setting(key, default=None):
    db = _db()
    row = db.execute("SELECT value FROM cycle_settings WHERE key = ?", (key,)).fetchone()
    db.close()
    return row[0] if row else default


def _set(key, value):
    db = _db()
    if value is None:
        db.execute("DELETE FROM cycle_settings WHERE key = ?", (key,))
    else:
        db.execute("INSERT OR REPLACE INTO cycle_settings (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()
    db.close()


def clear_cycle_log():
    db = _db()
    for table in ("cycle_log", "symptom_log", "cycle_settings"):
        db.execute(f"DELETE FROM {table}")
    db.commit()
    db.close()


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


# ---------- Helpers ----------
def _fmt(d):
    return f"{d:%A}, {d.day} {d:%B}"


def _short(d):
    return f"{d.day} {d:%B}"


def _join(xs):
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]


def _when(lower):
    """'today' / 'yesterday' / '2 days ago' / 'on monday' -> a date (never in the future)."""
    t = date.today()
    if "yesterday" in lower:
        return t - timedelta(days=1)
    m = re.search(r"\b(\d+|" + "|".join(NUMS) + r") days? ago\b", lower)
    if m:
        return t - timedelta(days=int(m.group(1)) if m.group(1).isdigit() else NUMS[m.group(1)])
    m = re.search(r"\b(?:on |last )?(" + "|".join(DAYS) + r")\b", lower)
    if m:
        return t - timedelta(days=(t.weekday() - DAYS.index(m.group(1))) % 7)
    return t


def _lengths(starts):
    """Cycle lengths between logged periods, ignoring obvious logging slips."""
    return [(b - a).days for a, b in zip(starts, starts[1:]) if 15 <= (b - a).days <= 60]


def _period_lengths():
    return [(e - s).days + 1 for _, s, e in _periods() if e and 1 <= (e - s).days + 1 <= 15]


def _avg(xs):
    return round(sum(xs) / len(xs)) if xs else None


def _predict():
    """(next start, average cycle, recent cycle lengths), or None with no dates."""
    starts = _starts()
    if not starts:
        return None
    lengths = _lengths(starts)[-6:]
    avg = _avg(lengths) or TYPICAL
    return starts[-1] + timedelta(days=avg), avg, lengths


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


# ---------- Symptoms and patterns ----------
def symptoms_in(text):
    """'cramps and a bad headache' -> ['cramps', 'headache']; [] if any part isn't a known symptom."""
    found = []
    for part in re.split(r",|;|\band\b", text.lower()):
        part = part.strip(" .!")
        if not part:
            continue
        name = next((n for n, pat in SYMPTOMS.items()
                     if re.fullmatch(r"(?:a |an |some |bad |mild |severe |really bad |a bad |a mild )?(?:" + pat + ")", part)),
                    None)
        if name is None:
            return []                                  # something we don't know: not a symptom log
        found.append(name)
    return list(dict.fromkeys(found))


def is_symptom_log(text):
    m = SYMLOG.match(text.lower().strip(" .!?,"))
    return bool(m and symptoms_in(m.group(1)))


def _log_symptoms(names, day):
    db = _db()
    for n in names:
        if not db.execute("SELECT 1 FROM symptom_log WHERE day = ? AND symptom = ?", (day.isoformat(), n)).fetchone():
            db.execute("INSERT INTO symptom_log (day, symptom) VALUES (?, ?)", (day.isoformat(), n))
    db.commit()
    db.close()
    when = "today" if day == date.today() else _fmt(day)
    return f"Noted: {_join(names)}, for {when}. This stays only on this device."


def _symptom_rows():
    db = _db()
    rows = [(date.fromisoformat(d), s) for d, s in db.execute("SELECT day, symptom FROM symptom_log ORDER BY day")]
    db.close()
    return rows


def _phase(day, starts, plen, predicted):
    """Where a day falls: ('during', day of period, cycle) / ('before', days before, cycle) / ('other', None, None)."""
    prev = [s for s in starts if s <= day]
    nxt = [s for s in starts if s > day]
    nxt_start = nxt[0] if nxt else predicted
    if prev and (day - prev[-1]).days < plen:
        return "during", (day - prev[-1]).days + 1, prev[-1]
    if nxt_start and 1 <= (nxt_start - day).days <= 7:
        return "before", (nxt_start - day).days, nxt_start
    return "other", None, None


def _pattern_for(symptom):
    days = [d for d, s in _symptom_rows() if s == symptom]
    if not days:
        return f"You haven't logged {symptom} yet. You can say, for example: log {symptom} today."
    starts = _starts()
    pred = _predict()
    plen = _avg(_period_lengths()) or 5
    by_phase = {"before": {}, "during": {}}
    for d in days:
        phase, n, cycle = _phase(d, starts, plen, pred[0] if pred else None)
        if phase in by_phase:
            by_phase[phase].setdefault(cycle, []).append(n)
    best = max(by_phase, key=lambda p: len(by_phase[p]))
    cycles = by_phase[best]
    if len(cycles) < 2:
        return (f"So far you've logged {symptom} on {len(days)} day{'s' if len(days) > 1 else ''}. "
                "I need a couple more cycles of notes to see a pattern.")
    nums = [n for ns in cycles.values() for n in ns]
    lo, hi = min(nums), max(nums)
    span = str(lo) if lo == hi else f"{lo} to {hi}"
    when = (f"about {span} day{'s' if hi > 1 else ''} before your period" if best == "before"
            else f"on day {span} of your period")
    return f"You logged {symptom} in {len(cycles)} cycles, usually {when}."


# ---------- Doctor summary ----------
def cycle_summary():
    """(title, lines) for the screen or a shared page; (None, []) without any dates."""
    periods = _periods()
    if not periods:
        return None, []
    starts = [s for _, s, _ in periods]
    lines = ["Period start dates: " + ", ".join(_short(s) for s in starts[-6:])]
    lengths = _lengths(starts)[-6:]
    if lengths:
        lines.append(f"Average cycle: {_avg(lengths)} days (range {min(lengths)} to {max(lengths)})")
    plens = _period_lengths()[-6:]
    if plens:
        lines.append(f"Average period length: {_avg(plens)} days")
        if max(plens) > LONG_PERIOD:
            lines.append("Some periods lasted longer than 7 days.")
    counts = {}
    for _, s in _symptom_rows():
        counts[s] = counts.get(s, 0) + 1
    if counts:
        top = sorted(counts, key=counts.get, reverse=True)[:4]
        lines.append("Most logged symptoms: " + ", ".join(f"{s} ({counts[s]} days)" for s in top))
    note = _irregular_note(lengths).strip()
    if note:
        lines.append(note)
    lines.append(f"Prepared on {_fmt(date.today())} from your own notes. Not a diagnosis.")
    return "Cycle summary for my doctor", lines


# ---------- Discreet reminder ----------
def cycle_nudge_due(peek=False):
    """The discreet reminder phrase if it's time (once per cycle, 9 AM to 9 PM), else None."""
    days = _setting("nudge_days")
    pred = _predict() if days else None
    if not pred:
        return None
    due = pred[0]
    today = date.today()
    if not (due - timedelta(days=int(days)) <= today <= due and 9 <= datetime.now().hour < 21):
        return None
    if _setting("nudged_for") == due.isoformat():
        return None
    if not peek:
        _set("nudged_for", due.isoformat())
    return _setting("nudge_phrase") or DEFAULT_PHRASE


# ---------- Entry points ----------
def is_cycle_question(text):
    """Only clear period-tracker sentences count, never 'free period', 'time period' or 'cycle to work'."""
    lower = text.lower()
    return bool(_pending_log or is_symptom_log(text) or any(p.search(lower) for p in (
        LOG, END, NEXT, LAST, LENGTH, PERIOD_LEN, CYCLE_DAY, PATTERN, SUMMARY, NUDGE_SET, NUDGE_OFF, PHRASE_SET, FORGET)))


def handle_cycle(text):
    """Returns a reply (a string, or {'say', 'lines'} for the summary), or None."""
    global _pending_log
    lower = text.lower().strip(" .!?,")

    if _pending_log:                                   # answer to "should I note it?"
        day, _pending_log = _pending_log, None
        if YES.search(lower):
            return _save(day)
        if re.match(r"^(?:no|nope|don'?t|not)\b", lower):
            return "Okay, I won't note anything."
        # another question instead: drop the offer and answer it

    if FORGET.search(lower):
        clear_cycle_log()
        return "Done. I've deleted all your period dates, symptoms and reminders from this device."

    if NUDGE_OFF.search(lower):
        _set("nudge_days", None)
        return "Okay, no more period reminders."

    m = PHRASE_SET.search(lower)
    if m:
        phrase = m.group(1).strip(" .!")
        _set("nudge_phrase", phrase[0].upper() + phrase[1:] + ".")
        return f"Okay. When it's time, I'll just say: {phrase}. Nothing else."

    m = NUDGE_SET.search(lower)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else NUMS[m.group(1)]
        _set("nudge_days", n)
        _set("nudged_for", None)
        phrase = _setting("nudge_phrase") or DEFAULT_PHRASE
        reply = (f"Okay. About {n} day{'s' if n > 1 else ''} before your next period, I'll say: {phrase} "
                 "Nothing else, so it stays private.")
        if not _starts():
            reply += " I'll start once you tell me when your period starts."
        return reply

    if END.search(lower):
        day = _when(lower)
        recent = [p for p in _periods() if p[1] <= day and (day - p[1]).days <= 15]
        if not recent:
            return "I don't have a start date for this period yet. Tell me when it started, and then when it ended."
        pid, start, _ = recent[-1]
        db = _db()
        db.execute("UPDATE cycle_log SET end_day = ? WHERE id = ?", (day.isoformat(), pid))
        db.commit()
        db.close()
        n = (day - start).days + 1
        reply = f"Noted. This period lasted {n} day{'s' if n != 1 else ''}."
        if n > LONG_PERIOD:
            reply += " Periods that last longer than a week are worth mentioning to a doctor."
        return reply

    if LOG.search(lower):                              # nothing is saved without a "yes"
        _pending_log = _when(lower)
        return f"Just to check: should I note your period as starting on {_fmt(_pending_log)}?"

    m = SYMLOG.match(lower)
    if m and symptoms_in(m.group(1)):
        return _log_symptoms(symptoms_in(m.group(1)), _when(m.group(2) or "today"))

    m = PATTERN.search(lower)
    if m:
        asked = symptoms_in(m.group(1)) if m.group(1) else []
        if asked:
            return " ".join(_pattern_for(s) for s in asked)
        counts = {}
        for _, s in _symptom_rows():
            counts[s] = counts.get(s, 0) + 1
        if not counts:
            return "You haven't logged any symptoms yet. You can say, for example: log cramps today."
        return " ".join(_pattern_for(s) for s in sorted(counts, key=counts.get, reverse=True)[:3])

    if SUMMARY.search(lower):
        _, lines = cycle_summary()
        if not lines:
            return "I don't have any period dates saved yet, so there's no summary to show."
        return {"say": "Here's your cycle summary, on my screen. If you'd like it for your doctor, "
                       "say: share my cycle summary.", "lines": lines}

    starts = _starts()
    if CYCLE_DAY.search(lower):
        if not starts:
            return "I don't have any period dates saved yet. When it starts, just tell me: my period started today."
        n = (date.today() - starts[-1]).days + 1
        if n > 60:
            return (f"It's been {n} days since your last logged period. "
                    "If one has started since, just tell me when.")
        _, _, end = _periods()[-1]
        plen = _avg(_period_lengths()) or 5
        during = (end and date.today() <= end) or (not end and n <= plen)
        return f"You're on day {n} of your cycle" + (", so your period is probably still going." if during else ".")

    if LAST.search(lower):
        if not starts:
            return "I don't have any period dates saved yet. When it starts, just tell me: my period started today."
        return f"Your last period started on {_fmt(starts[-1])}."

    if PERIOD_LEN.search(lower):
        plens = _period_lengths()
        if not plens:
            return "I don't know yet. When your period ends, tell me: my period ended today."
        return f"Your periods have lasted about {_avg(plens)} days, over the last {len(plens)} you told me about."

    if LENGTH.search(lower):
        lengths = _lengths(starts)
        if not lengths:
            return "I need at least two period dates to work out your cycle. Just tell me each time it starts."
        if len(lengths) == 1:
            return f"Your last cycle was {lengths[0]} days." + _irregular_note(lengths)
        return (f"Your cycle has averaged {_avg(lengths)} days, over the last {len(lengths)} cycles."
                + _irregular_note(lengths))

    if NEXT.search(lower):
        pred = _predict()
        if not pred:
            return "I don't have any period dates saved yet. When it starts, just tell me: my period started today."
        due, avg, lengths = pred
        if (date.today() - due).days > 2:
            reply = ("Based on your past cycles, it may be a few days late. Cycles often vary, "
                     "and stress, sleep or travel can shift them. If you're concerned, a doctor can help.")
        else:
            basis = (f"Based on your last cycle, which was {avg} days," if len(lengths) == 1 else
                     f"Based on your last {len(lengths)} cycles, averaging {avg} days," if lengths else
                     "Based on a typical 28-day cycle,")
            reply = f"{basis} your next period may start around {_fmt(due)}, give or take a few days."
            plen = _avg(_period_lengths())
            if plen:
                reply += f" It usually lasts about {plen} days."
            if not lengths:
                reply += " Once you log a couple more, I'll use your own cycle."
        return reply + _irregular_note(lengths) + _note_once()

    return None