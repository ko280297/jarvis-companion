"""Reminders and timers. Pure Python: no LLM and no internet.
Stored on the device, so they survive a restart."""
import re
import sqlite3
import time
from datetime import datetime, timedelta

from safety import is_unsafe, REFUSAL

DB_PATH = "memories.db"

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
    "twenty": 20, "thirty": 30, "forty five": 45, "sixty": 60,
    "eleven": 11, "twelve": 12, "twenty five": 25, "forty": 40, "fifty": 50, "ninety": 90,   # fix 41
}
_NUM = r"\d+|" + "|".join(sorted(NUMBER_WORDS, key=len, reverse=True))
UNITS = {"second": 1, "minute": 60, "hour": 3600}

DURATION = re.compile(rf"\b(?:in|for|after|within)\s+(?:the\s+)?(?:next\s+)?({_NUM})\s+(second|minute|hour)s?\b")
DURATION_TIMER = re.compile(rf"\b({_NUM})[\s-]+(second|minute|hour)s?\s+timer\b")
HALF_HOUR = re.compile(r"\b(?:in|for|after)\s+half an hour\b")
CLOCK = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?)?")

FILLER = re.compile(
    r"^(?:(?:oh|ok|okay|so|and|please|hey|can you|could you|would you"
    r"|i want you to|i'd like you to|i'm asking you to|i am asking you to)[,]?\s+)+")
REMIND = re.compile(r"\b(?:remind(?:\s+me)?|(?:set|create|add|make)\s+(?:a\s+|an\s+)?reminder)\b\s*(.*)$")
SET_TIMER = re.compile(r"\b(?:set|start|put)\b.*\btimer\b|^timer\b")
CANCEL = re.compile(r"\b(cancel|delete|remove|stop|clear)\b.*\b(reminder|timer)s?\b")
LIST_Q = re.compile(
    r"\b(what|which|any|list|tell me|show|how many|count)\b.*\b(reminders?|timers?)\b"
    r"|\breminders?\b.*\b(do i have|are there|pending)\b")
LAST_Q = re.compile(r"\b(when|what|which|tell me)\b.*\b(last|latest|previous|that|this|the|my)\s+reminder\b")

# ----- Fix 47: repeating reminders, snooze, edit, cancel by name -----
REPEAT_EVERY = re.compile(rf"\bevery\s+(?:({_NUM})\s+)?(minute|hour)s?\b|\bevery half an? hour\b")
REPEAT_WEEKDAYS = re.compile(r"\b(?:(?:on |every )?week ?days?|every working day|monday to friday)\b")
REPEAT_DAILY = re.compile(r"\b(?:every\s*day|each day|every (?:morning|evening|night|afternoon)"
                          r"|(?<=remind me )daily|daily(?=\s+(?:to|at|by|from|starting|around)\b|\s*$))\b")
REPEAT_WEEKLY = re.compile(r"\b(?:every week|once a week|(?<=remind me )weekly|weekly(?=\s+(?:to|at|on|by)\b|\s*$)"
                           r"|every (?=monday|tuesday|wednesday|thursday|friday|saturday|sunday))")
PART_OF_DAY = {"morning": 8, "afternoon": 14, "evening": 18, "night": 21}
SNOOZE = re.compile(rf"^(?:snooze(?: it| that| this| the reminder| for now)?|remind me (?:again|later)|ask me again"
                    rf"|tell me again)(?:\s+(?:for|in|after|by)?\s*(?:({_NUM})\s+(second|minute|hour)s?|half an hour))?"
                    rf"(?:\s+(?:later|please))?$")
EDIT = re.compile(r"^(?:change|move|edit|update|reschedule|shift|postpone|push|delay|make)\b"
                  r"(?!\s+(?:a|an|new)\s+reminder)")
STOP_REMINDING = re.compile(r"\b(?:stop|don'?t|do not) remind(?:ing)? me\b")
_SKIP_WORDS = {"reminder", "reminders", "remind", "reminding", "change", "move", "edit", "update", "reschedule",
               "shift", "postpone", "push", "delay", "make", "cancel", "delete", "remove", "stop", "clear", "please",
               "that", "this", "with", "from", "about", "time", "text", "today", "tomorrow", "tonight", "instead",
               "minute", "minutes", "hour", "hours", "after", "before", "every", "daily", "weekly", "again",
               "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "morning", "evening"}
_CLOCK_NUMS = {w: str(n) for w, n in NUMBER_WORDS.items() if isinstance(n, int) and 1 <= n <= 12 and " " not in w
               and w not in ("a", "an")}


def _words(s):
    """Content words, with a plural 's' dropped: 'medicines' matches 'medicine'."""
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w
            for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 3} - _SKIP_WORDS


def _repeat_of(t):
    """('daily' | 'weekdays' | 'weekly' | 'every:<seconds>' | '', the words that said it)."""
    m = REPEAT_EVERY.search(t)
    if m:
        secs = 1800 if "half" in m.group(0) else (_n(m.group(1)) if m.group(1) else 1) * UNITS[m.group(2)]
        return f"every:{max(secs, 900)}", m.group(0)
    for code, pat in (("weekdays", REPEAT_WEEKDAYS), ("daily", REPEAT_DAILY), ("weekly", REPEAT_WEEKLY)):
        m = pat.search(t)
        if m:
            return code, m.group(0)
    return "", None


def next_due(due, repeat, now=None):
    """The next time a repeating reminder rings, after 'now'."""
    now = now or datetime.now()
    if repeat.startswith("every:"):
        step = timedelta(seconds=int(repeat[6:]))
        while due <= now:
            due += step
        if due.hour >= 22 or due.hour < 7:                       # no hourly reminders at night
            day = due.date() if due.hour < 7 else due.date() + timedelta(days=1)
            due = datetime(day.year, day.month, day.day, 7, 0)
        return due
    step = timedelta(days=7 if repeat == "weekly" else 1)
    while due <= now or (repeat == "weekdays" and due.weekday() >= 5):
        due += step
    return due


def repeat_words(rep, due):
    """'every day at 4 PM' / 'every weekday at 9 AM' / 'every Monday at 10 AM' / 'every 2 hours'."""
    clock = due.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    if rep.startswith("every:"):
        secs = int(rep[6:])
        if secs % 3600 == 0:
            return "every hour" if secs == 3600 else f"every {secs // 3600} hours"
        return f"every {secs // 60} minutes"
    return {"daily": f"every day at {clock}", "weekdays": f"every weekday at {clock}",
            "weekly": f"every {due:%A} at {clock}"}.get(rep, when_words(due))


def _set_reply(task, due, rep):
    if rep:
        return (f"Okay, I'll remind you {repeat_words(rep, due)}" + (f": {task}" if task else "")
                + f". The first one is {when_words(due)}.")
    if task:
        return f"Okay, I'll remind you: {task}, {when_words(due)}."
    return f"Okay, reminder set {when_words(due)}."


def _clock_words(s):
    """'five pm' -> '5 pm', 'six o'clock' -> '6'."""
    s = re.sub(r"\b(" + "|".join(_CLOCK_NUMS) + r")\b(?=\s*(?:a\.?\s?m|p\.?\s?m|o'?clock)|\s*$)",
               lambda m: _CLOCK_NUMS[m.group(1)], s)
    return re.sub(r"\s*o'?clock\b", "", s)


def _new_time(s, old, now):
    """A new time for an existing reminder: '5 pm', 'tomorrow at 6', 'tomorrow' (same clock time), 'in 10 minutes'."""
    s = _clock_words(s)
    due, _ = parse_when(s, now)
    if not due:
        m = re.search(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?(?=\s|$)", s)
        if m and not re.search(r"\b(?:minute|hour|second|day)s?\b", s):
            due, _ = parse_when(s[:m.start()] + "at " + s[m.start():], now)
    if due and not re.search(r"\b(?:today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday"
                             r"|days?|minutes?|hours?|seconds?|half)\b", s):
        hour = due.hour
        if not re.search(r"\b[ap]\.?\s?m\b", s) and hour != 0:      # "change it to 6": keep its morning/evening
            hour = hour % 12 + (12 if old.hour >= 12 else 0)
        same_day = old.replace(hour=hour, minute=due.minute, second=0, microsecond=0)
        if same_day > now:
            return same_day                           # "move it to 7 pm": same day as before, new time
    if due:
        return due
    days = None
    m_wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", s)
    if "day after tomorrow" in s:
        days = 2
    elif "tomorrow" in s:
        days = 1
    elif re.search(r"\b(?:today|tonight)\b", s):
        days = 0
    elif m_wd:
        wd = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(m_wd.group(1))
        days = (wd - now.weekday()) % 7 or 7
    if days is None:
        return None
    new = (now + timedelta(days=days)).replace(hour=old.hour, minute=old.minute, second=0, microsecond=0)
    return new if new > now else None


_pending_choice = None   # fix 50: "which one: the first, the second, or both?" waiting for its answer


def _ask_which(action, rows, said):
    """Two or more reminders match: list them and ask which one."""
    global _pending_choice
    rows = sorted(rows, key=lambda r: r[2])
    _pending_choice = {"action": action, "rows": rows, "said": said, "at": time.time()}
    word = next(iter(_words(said) & set().union(*(_words(r[1]) for r in rows))), "")
    items = "; ".join(f"{i + 1}. {r[1]}, {repeat_words(r[3], datetime.fromtimestamp(r[2])) if r[3] else when_words(datetime.fromtimestamp(r[2]))}"
                      for i, r in enumerate(rows[:4]))
    options = "the first, the second, or both" if len(rows) == 2 else "the first, the second, or all of them"
    return f"You have {len(rows)} {word + ' ' if word else ''}reminders: {items}. Which one: {options}?"


def _pick(t, rows):
    """'both' / 'the second' / 'the last one' / 'the one in 4 minutes' -> the chosen reminders, or None."""
    if re.search(r"\b(?:both|all|every|each|everything)\b", t):
        return rows
    order = [("first|1st|earlier|earliest|sooner|soonest", 0), ("second|2nd", 1), ("third|3rd", 2),
             ("fourth|4th", 3), ("last|latest|later|final", len(rows) - 1)]
    for pat, i in order:
        if re.search(rf"\b(?:{pat})\b", t) and i < len(rows):
            return [rows[i]]
    m = re.fullmatch(r"(?:the |number |no\.? )?(one|two|three|four|[1-4])(?: one)?", t.strip())
    if m:
        i = {"one": 0, "two": 1, "three": 2, "four": 3}.get(m.group(1), None)
        i = int(m.group(1)) - 1 if i is None else i
        return [rows[i]] if i < len(rows) else None
    said_nums = set(re.findall(r"\d+", t))
    if said_nums:
        hits = [r for r in rows if said_nums & set(re.findall(r"\d+", when_words(datetime.fromtimestamp(r[2]))))]
        if len(hits) == 1:
            return hits
    return None


def _edit_reminder(t, rem, forced=None):
    """'change my medicine reminder to 5 pm', 'move it to tomorrow', 'push it by 10 minutes',
    'change the reminder to call mom instead'. Returns None if this isn't about a reminder."""
    now = datetime.now()
    has_rem = bool(re.search(r"\breminders?\b", t))
    parts = re.split(r"\s+(?:to|until|till)\s+", t)
    matches = [forced] if forced else rem.find_all_active(parts[0])   # fix 50
    if len(matches) > 1:
        return _ask_which("edit", matches, t)
    target = matches[0] if matches else None
    if not target and has_rem:
        _named = _words(parts[0])                         # fix 72: a name was said, but no reminder has it
        if _named and rem.active():
            return (f"I couldn't find a {' '.join(sorted(_named))} reminder. "
                    "Say: what are my reminders, to hear them.")
        target = rem.recent() or next(iter(rem.active()), None)
    via_it = False
    if not target and re.search(r"\b(?:it|that|this)\b", t):
        target, via_it = rem.recent(), True
    if not target:
        return "You don't have any reminders to change." if has_rem else None
    rid, text_, due_ts, rep = target
    rep = rep or ""
    old = datetime.fromtimestamp(due_ts)
    new_due = None
    m_by = re.search(rf"\bby\s+(?:({_NUM})\s+(second|minute|hour)s?|half an hour)\b", t)
    if m_by:
        secs = _n(m_by.group(1)) * UNITS[m_by.group(2)] if m_by.group(1) else 1800
        new_due = max(old, now) + timedelta(seconds=secs)
    if not new_due and len(parts) > 1:
        new_due = _new_time(parts[-1], old, now)
    if not new_due and len(parts) == 1:
        new_due = _new_time(t, old, now)
    if new_due:
        while rep == "weekdays" and new_due.weekday() >= 5:
            new_due += timedelta(days=1)
        rem.set_due(rid, new_due)
        if rep:
            return f"Done. I'll remind you {repeat_words(rep, new_due)}: {text_}."
        return f"Done. Your reminder, {text_}, is now {when_words(new_due)}."
    if via_it:
        return None                                   # "make it louder": not about the reminder
    new_text = re.split(r"\s+(?:to|until|till)\s+", t, maxsplit=1)[1] if len(parts) > 1 else ""
    new_text = re.sub(r"\b(?:instead|please|now)\b", " ", new_text)
    new_text = re.sub(r"^\s*(?:say|says|read|be)\s+", "", " ".join(new_text.split())).strip(" ,.")
    if new_text and has_rem and not re.match(r"(?:what|when|who|where|why|how)\b", new_text):
        if is_unsafe(new_text):
            return REFUSAL
        rem.set_text(rid, new_text)
        return f"Done. Your reminder now says: {new_text}."
    return "What should I change it to? For example: change it to 6 pm, or move it to tomorrow."


def _n(word):
    return int(word) if word.isdigit() else NUMBER_WORDS[word]


def parse_when(t, now=None):
    """Find 'when' in lowercase text. Returns (due datetime, the phrase that said it), or (None, None)."""
    now = now or datetime.now()

    m = HALF_HOUR.search(t)
    if m:
        return now + timedelta(minutes=30), m.group(0)

    m = DURATION.search(t) or DURATION_TIMER.search(t)
    if m:
        return now + timedelta(seconds=_n(m.group(1)) * UNITS[m.group(2)]), m.group(0)

    m = CLOCK.search(t)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        ampm = re.sub(r"[^ap]", "", m.group(3) or "")      # "p.m." -> "p"
        if hour > 23 or minute > 59:
            return None, None
        if ampm == "p" and hour < 12:
            hour += 12
        if ampm == "a" and hour == 12:
            hour = 0
        # Which day: "day after tomorrow", "tomorrow", "in 3 days", "on Friday" (fix 26)
        days, weekday = 0, None
        m_days = re.search(rf"\bin\s+({_NUM})\s+days?\b", t)
        m_wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", t)
        if "day after tomorrow" in t:
            days = 2
        elif "tomorrow" in t:
            days = 1
        elif m_days:
            days = _n(m_days.group(1))
        elif m_wd:
            weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(m_wd.group(1))
            days = (weekday - now.weekday()) % 7
        tomorrow = days > 0
        if tomorrow and not ampm and 1 <= hour <= 6:
            hour += 12                    # "tomorrow at 5" almost always means 5 pm
        due = (now + timedelta(days=days)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
        if weekday is not None and due <= now:
            due += timedelta(days=7)      # "on Friday at 5" said on Friday evening = next Friday
            tomorrow = True
        if not ampm and hour <= 12 and not tomorrow:
            while due <= now:             # "at 5" with no am/pm = the next 5 o'clock still ahead
                due += timedelta(hours=12)
        elif due <= now:
            due += timedelta(days=1)
        return due, m.group(0)

    return None, None


def when_words(due, now=None):
    """'in 10 minutes' / 'at 5 PM today' / 'at 9 AM tomorrow'."""
    now = now or datetime.now()
    secs = (due - now).total_seconds()
    if secs <= 1:
        return "now"
    if secs < 59.5:
        return f"in {round(secs)} seconds"
    if secs < 3600:
        mins = round(secs / 60)
        return f"in {mins} minute{'s' if mins != 1 else ''}"
    clock = due.strftime("%I:%M %p").lstrip("0").replace(":00", "")
    if due.date() == now.date():
        return f"at {clock} today"
    if due.date() == (now + timedelta(days=1)).date():
        return f"at {clock} tomorrow"
    return f"at {clock} on {due:%A}"


class Reminders:
    def __init__(self, path=DB_PATH):
        self.db = sqlite3.connect(path)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS reminders "
            "(id INTEGER PRIMARY KEY, due REAL, text TEXT, kind TEXT, done INTEGER DEFAULT 0)"
        )
        if "repeat" not in [r[1] for r in self.db.execute("PRAGMA table_info(reminders)")]:   # fix 47
            self.db.execute("ALTER TABLE reminders ADD COLUMN repeat TEXT DEFAULT ''")
        self.db.commit()
        self.rang = []        # fix 47: (text, kind, when it rang, id): for snooze and the red banner
        self.touched = None   # fix 47: (id, when): the reminder just set or changed, for "change it to 6"
        self.db.execute("CREATE TABLE IF NOT EXISTS reminder_log (id INTEGER PRIMARY KEY, rid INTEGER, text TEXT, "
                        "due REAL, rang REAL, done REAL)")      # fix 59: each time a reminder rang, and when it was done
        self.db.commit()

    def add(self, due, text, kind, repeat=""):
        cur = self.db.execute("INSERT INTO reminders (due, text, kind, repeat) VALUES (?, ?, ?, ?)",
                              (due.timestamp(), text, kind, repeat))
        self.db.commit()
        self.touched = (cur.lastrowid, time.time())
        return cur.lastrowid

    def upcoming(self):
        return [(datetime.fromtimestamp(d), t, k) for d, t, k in self.db.execute(
            "SELECT due, text, kind FROM reminders WHERE done = 0 ORDER BY due")]

    def due_now(self):
        return self.db.execute(
            "SELECT id, text, kind, due FROM reminders WHERE done = 0 AND due <= ? ORDER BY due",
            (time.time(),)).fetchall()

    def mark_done(self, rid):
        """It rang. A repeating reminder moves on to its next time instead of being finished (fix 47)."""
        row = self.db.execute("SELECT text, kind, due, repeat FROM reminders WHERE id = ?", (rid,)).fetchone()
        if row:
            self.rang = self.rang_recent(1800)[-5:] + [(row[0], row[1], time.time(), rid)]
            if row[1] == "reminder":                      # fix 59: it rang; it can be ticked later
                self.db.execute("INSERT INTO reminder_log (rid, text, due, rang) VALUES (?, ?, ?, ?)",
                                (rid, row[0], row[2], time.time()))
        nxt = next_due(datetime.fromtimestamp(row[2]), row[3]) if row and row[3] else None
        if nxt:
            self.db.execute("UPDATE reminders SET due = ? WHERE id = ?", (nxt.timestamp(), rid))
        else:
            self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
        self.db.commit()

    # ----- fix 59: reminders that rang, ticked when done -----
    def log_day(self, day=None):
        day = day or datetime.now().date()
        start = datetime(day.year, day.month, day.day).timestamp()
        return self.db.execute("SELECT id, text, due, rang, done FROM reminder_log WHERE rang >= ? AND rang < ? "
                               "ORDER BY rang", (start, start + 86400)).fetchall()

    def log_tick(self, lid, done=True):
        self.db.execute("UPDATE reminder_log SET done = ? WHERE id = ?", (time.time() if done else None, lid))
        self.db.commit()

    def log_tick_latest(self, seconds=1800):
        row = self.db.execute("SELECT id FROM reminder_log WHERE done IS NULL AND rang >= ? ORDER BY rang DESC LIMIT 1",
                              (time.time() - seconds,)).fetchone()
        if row:
            self.log_tick(row[0])
        return row

    # ----- fix 47 -----
    def upcoming_full(self):
        return [(datetime.fromtimestamp(d), t, k, r or "") for d, t, k, r in self.db.execute(
            "SELECT due, text, kind, repeat FROM reminders WHERE done = 0 ORDER BY due")]

    def active(self):
        """Reminders still waiting, newest first: (id, text, due, repeat)."""
        return self.db.execute("SELECT id, text, due, repeat FROM reminders "
                               "WHERE done = 0 AND kind = 'reminder' ORDER BY id DESC").fetchall()

    def duplicate(self, due, text):                         # fix 54: the same reminder said twice
        for _, t, d, _r in self.active():
            if t.lower().strip() == (text or "").lower().strip() and abs(d - due.timestamp()) < 90:
                return True
        return False

    def find_all_active(self, said):                      # fix 50
        words = _words(said)
        return [row for row in self.active() if words & _words(row[1])] if words else []

    def find_active(self, said):
        words = _words(said)
        if not words:
            return None
        return next((row for row in self.active() if words & _words(row[1])), None)

    def recent(self, seconds=120):
        """The reminder set or changed in the last 2 minutes, if it's still waiting."""
        if self.touched and time.time() - self.touched[1] < seconds:
            return self.db.execute("SELECT id, text, due, repeat FROM reminders WHERE id = ? AND done = 0",
                                   (self.touched[0],)).fetchone()
        return None

    def set_due(self, rid, due):
        self.db.execute("UPDATE reminders SET due = ? WHERE id = ?", (due.timestamp(), rid))
        self.db.commit()
        self.touched = (rid, time.time())

    def set_text(self, rid, text):
        self.db.execute("UPDATE reminders SET text = ? WHERE id = ?", (text, rid))
        self.db.commit()
        self.touched = (rid, time.time())

    def cancel_id(self, rid):
        self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
        self.db.commit()

    def rang_recent(self, seconds=300):
        return [r for r in self.rang if time.time() - r[2] < seconds]

    def snooze_source(self):
        recent = self.rang_recent(1800)
        return recent[-1] if recent else None

    def cancel(self, kind, everything=False):
        if everything:
            cur = self.db.execute("UPDATE reminders SET done = 1 WHERE done = 0 AND kind = ?", (kind,))
            count = cur.rowcount
        else:
            row = self.db.execute(
                "SELECT id FROM reminders WHERE done = 0 AND kind = ? ORDER BY id DESC LIMIT 1",
                (kind,)).fetchone()
            if not row:
                return 0
            self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (row[0],))
            count = 1
        self.db.commit()
        return count

    def last(self):
        """The most recently created reminder: (text, due datetime, done) or None."""
        row = self.db.execute(
            "SELECT text, due, done FROM reminders WHERE kind = 'reminder' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return (row[0], datetime.fromtimestamp(row[1]), bool(row[2])) if row else None

    def find(self, question):
        """The newest reminder that shares a real word with the question ('water', 'father'...)."""
        skip = {"reminder", "remind", "when", "what", "that", "this", "last", "time", "about", "with"}
        words = {w for w in re.findall(r"[a-z]+", question) if len(w) > 3} - skip
        for text, due, done in self.db.execute(
                "SELECT text, due, done FROM reminders WHERE kind = 'reminder' ORDER BY id DESC"):
            if words & set(re.findall(r"[a-z]+", text.lower())):
                return (text, datetime.fromtimestamp(due), bool(done))
        return None


    def clear_all(self):
        self.db.execute("DELETE FROM reminders")
        self.db.commit()
        self.rang, self.touched = [], None     # fix 47


_pending_task = None   # "remind me to call dad" with no time -> wait for the time in the next sentence
_pending_repeat = ""   # fix 47: ... and whether it repeats


_NOT_THINGS = {"the", "off", "tick", "it", "is", "was", "has", "been", "have", "had", "done", "finished", "completed",
               "all", "and", "just", "now", "already", "today", "work", "task", "job", "of", "that", "this", "my",
               "i've", "did", "finally", "yes", "yeah", "okay", "also", "submitted", "sent", "took", "taken", "drank",
               "called", "too", "for", "with", "you", "please", "jarvis", "mark", "over"}


def _names_something_else(t, rang):
    """fix 97: "The watering of the plants is done" right after "call mom" rang is about the PLANTS,
    not the reminder: then the reminder banner stays, and the to-do list ticks the plants."""
    said = [w for w in re.findall(r"[a-z']+", t) if len(w) > 2 and w not in _NOT_THINGS]
    if not said:
        return False
    rang_words = [w for r in rang for w in re.findall(r"[a-z']+", str(r[0]).lower()) if len(w) > 2]
    return not any(a[:4] == b[:4] for a in said for b in rang_words)


def handle_reminder_command(text, rem):
    """Returns a spoken reply if this was a reminder/timer command, otherwise None."""
    global _pending_task, _pending_repeat, _pending_choice
    t = FILLER.sub("", text.lower().strip(" .!?,"))
    t = re.sub(r"\bat (\d{1,2})\.(\d{2})(?!\d)", r"at \1:\2", t)               # fix 53: "at 5.15pm" = 5:15 pm
    t = re.sub(r"\bat (\d{1,2})([0-5]\d)(?!\d)(?!\s*(?:minutes?|hours?|seconds?|days?))", r"at \1:\2", t)   # "at 515"
    if re.match(r"^(?:[\d.,]+\s*|(?!tell|show|give|let|help|send|teach|ask|excuse|pardon|for|to|with|call|text)"
                r"[a-z]{1,7}[.,]?\s+)?me\s+(?:tomorrow|today|tonight|at\s+\d+|in\s+(?:\d+|a|an|half|one|two|five|ten)"
                r"|every|on\s+\w+day)\b", t) and not t.startswith("remind"):
        t = re.sub(r"^.*?\bme\s+", "remind me ", t, count=1)                # fix 53: "9. me tomorrow at 5" (misheard)
    t = re.sub(r"\b(\d+|[a-z]+)[\s-]+(?:mins?|minutes?)\b", r"\1 minutes", t)   # fix 41: casual time words
    t = re.sub(r"\b(\d+|[a-z]+)[\s-]+(?:hrs?|hours?)\b", r"\1 hours", t)
    t = re.sub(r"\b(\d+|[a-z]+)[\s-]+(?:secs?|seconds?)\b", r"\1 seconds", t)
    t = re.sub(r"\ba couple of (minutes|hours|seconds)\b", r"two \1", t)
    t = re.sub(r"\ba few (minutes|hours|seconds)\b", r"three \1", t)

    if _pending_choice is not None:                         # fix 50: the answer to "which one?"
        pc, _pending_choice = _pending_choice, None
        chosen = _pick(text.lower().strip(" .!?,"), pc["rows"]) if time.time() - pc["at"] < 120 else None
        if chosen:
            if pc["action"] == "cancel":
                for r in chosen:
                    rem.cancel_id(r[0])
                if len(chosen) == 1:
                    return f"Okay, I've cancelled your reminder: {chosen[0][1]}, {when_words(datetime.fromtimestamp(chosen[0][2]))}."
                return "Okay, I've cancelled both." if len(chosen) == 2 else f"Okay, I've cancelled all {len(chosen)} of them."
            replies = [_edit_reminder(pc["said"], rem, forced=r) for r in chosen]
            return " ".join(r for r in replies if r)
        if re.match(r"(?:never ?mind|none|neither|leave it|no)\b", t):
            return "Okay, I've left them as they are."
        # not an answer: carry on and treat it as a normal sentence

    # The previous turn asked "When should I remind you?" -> this may be just the time
    if _pending_task is not None:
        task, _pending_task = _pending_task, None
        rep, _pending_repeat = _pending_repeat, ""          # fix 47: "remind me daily to ..." then "at 4 pm"
        due, _ = parse_when(t)
        if not due:
            due, _ = parse_when("at " + t)          # "6 pm" on its own
        if not due:
            due, _ = parse_when("in " + t)          # "two minutes" on its own
        if due:
            task = re.sub(r"\b(in|after|within)\b.*?\b(second|minute|hour)s?\b", " ", task)   # leftover time words
            task = " ".join(task.split()).strip(" ,.")
            for _ in range(3):
                task = re.sub(r"^(?:to|tool|that|about|for|me)\s+", "", task)
            if is_unsafe(task):
                return REFUSAL
            while rep == "weekdays" and due.weekday() >= 5:
                due += timedelta(days=1)
            if rem.duplicate(due, task or "your reminder"):        # fix 54: said twice
                return f"You already have that reminder: {task or 'your reminder'}, {when_words(due)}."
            rem.add(due, task or "your reminder", "reminder", rep)
            return _set_reply(task, due, rep)                # fix 47
        # not a time after all: carry on and treat it as a normal sentence

    # Fix 48: "done" / "I did it" right after a reminder rang: the red banner goes
    if re.match(r"(?:done|all done|i'?m done|(?:i'?ve |i have )?(?:done|finished|submitted|sent) it"
                r"|i did it|mark it (?:as )?done|dismiss(?: it)?)$", t) and rem.rang_recent(1800):
        rem.rang = []
        rem.log_tick_latest()                         # fix 59
        return "Great, I've cleared it."
    if rem.rang_recent(1800) and len(t.split()) <= 12 and "?" not in text \
            and not _names_something_else(t, rem.rang_recent(1800)) \
            and re.search(r"\b(?:done|did it|submitted|finished|completed|sent it|took it|taken it|drank|have taken"
                          r"|i took|i drank|i called)\b", t) \
            and not re.search(r"^(?:what|when|how|why|who|is|are|did|do|can|could|will)\b"
                              r"|\b(?:tell|play|set|remind|add|show|share|turn|start|stop|open|weather|news)\b", t):
        rem.rang = []                                   # fix 49: "I have submitted the report, done"
        rem.log_tick_latest()                         # fix 59b
        return "Great, well done! I've cleared it."

    # Fix 47: snooze, right after a reminder rang
    m = SNOOZE.match(t)
    if m:
        src = rem.snooze_source()
        if not src:
            return "There's nothing to snooze right now."
        secs = 1800 if "half an hour" in t else (_n(m.group(1)) * UNITS[m.group(2)] if m.group(1) else 600)
        due = datetime.now() + timedelta(seconds=secs)
        what = "your timer" if src[1] == "timer" else src[0]
        rem.add(due, what, "reminder")
        rem.rang = [r for r in rem.rang if r is not src]       # the red banner goes
        return f"Okay, I'll remind you again {when_words(due)}: {what}."

    # Cancel comes first, so "cancel that reminder" cancels instead of reading it out
    m = CANCEL.search(t)
    if m or STOP_REMINDING.search(t):
        kind = "timer" if m and m.group(2).startswith("timer") else "reminder"
        everything = bool(re.search(r"\b(all|every|reminders|timers)\b", t)) and not re.search(
            r"\bevery\s*(?:day|week|hour|morning|evening|night|weekday)\b", t)
        if kind == "reminder" and not everything:              # fix 47: "cancel my medicine reminder" -> that one
            hits = rem.find_all_active(t)                    # fix 50
            if len(hits) > 1:
                return _ask_which("cancel", hits, t)
            hit = hits[0] if hits else None
            if not hit and not m:                              # "stop reminding me": the repeating one, if only one
                reps = [r for r in rem.active() if r[3]]
                if len(reps) > 1:
                    return "Which one should I stop? For example: stop reminding me to drink water."
                hit = reps[0] if reps else None
            if hit:
                rem.cancel_id(hit[0])
                return f"Okay, I've cancelled your reminder: {hit[1]}." + (" It won't repeat any more." if hit[3] else "")
        n = rem.cancel(kind, everything=everything)
        return f"Cancelled {n} {kind}{'s' if n != 1 else ''}." if n else f"You don't have any {kind}s set."

    if EDIT.search(t):                                        # fix 47: change / move / push a reminder
        reply = _edit_reminder(t, rem)
        if reply:
            return reply

    if LAST_Q.search(t):
        m_at = re.search(r"\bat (\d{1,2}(?::\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?)?)", t)   # fix 54: "the reminder at 5 pm"
        want = parse_when("at " + m_at.group(1))[0] if m_at else None
        if want:
            ampm = bool(re.search(r"[ap]\.?\s?m", m_at.group(1)))
            hits = [(d, x) for d, x, k in rem.upcoming() if k == "reminder" and d.minute == want.minute
                    and (d.hour == want.hour if ampm else d.hour % 12 == want.hour % 12)]
            if hits:
                return "At that time you have: " + "; ".join(f"{x}, {when_words(d)}" for d, x in hits[:3]) + "."
            return f"You don't have a reminder at {m_at.group(1).strip()}."
        found = rem.find(t)                  # "...the reminder of drinking water?" -> that one
        last = found or rem.last()
        if not last:
            return "You haven't set any reminders yet."
        text_, due, done = last
        label = "That reminder" if found else "Your last reminder"
        clock = due.strftime("%I:%M %p").lstrip("0")
        if done or due <= datetime.now():
            return f"{label} was: {text_}, for {clock}."
        return f"{label} is: {text_}, {when_words(due)}."
    
    if LIST_Q.search(t):
        items = rem.upcoming_full()                              # fix 47: repeating ones say so
        if not items:
            return "You don't have any reminders or timers set."
        m_day = re.search(r"\b(day after tomorrow|tomorrow|today|tonight)\b", t)   # fix 54: only that day
        if m_day:
            shift = {"day after tomorrow": 2, "tomorrow": 1}.get(m_day.group(1), 0)
            target = (datetime.now() + timedelta(days=shift)).date()
            items = [i for i in items if i[0].date() == target]
            if not items:
                return f"You don't have any reminders {m_day.group(1)}."
        parts = [("a timer" if k == "timer" else x) + f", {repeat_words(r, d) if r else when_words(d)}"
                 for d, x, k, r in items]
        return f"You have {len(items)}: " + "; ".join(parts) + "."

    if SET_TIMER.search(t):
        due, _ = parse_when(t)
        if not due:
            return "How long should the timer be? For example, set a timer for 10 minutes."
        rem.add(due, "timer", "timer")
        return f"Timer set. I'll let you know {when_words(due)}."

    m = REMIND.search(t)
    if m:
        body = m.group(1)
        # "call dad at 6 pm and drink water in two minutes" -> two separate reminders
        for sep in re.finditer(r",?\s+and\s+(?:also\s+)?", body):
            left, right = body[:sep.start()], body[sep.end():]
            if parse_when(left)[0] and parse_when(right)[0]:
                replies = [handle_reminder_command("remind me " + part, rem) for part in (left, right)]
                return " ".join(r for r in replies if r)
        rep, rep_phrase = _repeat_of(t)                      # fix 47: daily / weekdays / weekly / every hour
        if rep:
            t = " ".join(t.replace(rep_phrase, " ", 1).split())
            m = REMIND.search(t)
            if not m:
                return None
        due, phrase = parse_when(t)
        if not due and rep.startswith("every:"):
            due = next_due(datetime.now(), rep)
        if not due and rep_phrase and rep_phrase.split()[-1] in PART_OF_DAY:      # "every morning" = 8 am
            due = datetime.now().replace(hour=PART_OF_DAY[rep_phrase.split()[-1]], minute=0, second=0, microsecond=0)
            if due <= datetime.now():
                due += timedelta(days=1)
        while due and rep == "weekdays" and due.weekday() >= 5:
            due += timedelta(days=1)
        if not due and re.match(rf"({_NUM})\s+(second|minute|hour)", m.group(1)):
            due, p = parse_when("in " + m.group(1))     # "remind me two minutes to ..." (no "in")
            phrase = p[3:] if p else None                # drop the "in " we added
        task = m.group(1)
        if phrase:
            task = task.replace(phrase, " ")
            task = re.sub(r"\b(?:(?:on\s+)?(?:the\s+)?day after tomorrow|tomorrow|today|in\s+\w+\s+days?"
                          r"|(?:on\s+)?(?:next\s+)?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
                          " ", task)
        task = re.split(r"[.!?]", task)[0]                       # keep only the first sentence
        task = " ".join(task.split())
        for _ in range(3):                                       # "for me to ..." -> "..."
            task = re.sub(r"^(?:to|that|about|for|me)\s+", "", task)
        task = re.sub(r"^(?:i|we)\s+(?:have got|have|'ve got|'ve|got)\s+|^there(?:'s| is| will be)\s+", "", task)   # fix 40
        task = task.strip(" ,.")
        if task.lower() in ("me", "it", "this", "that", "something"):
            task = ""
        if re.match(r"(what|who|when|where|why|how)\b", task):
            return None      # "remind me what my meeting is" is a question, not a new reminder
        if is_unsafe(task):
            return REFUSAL   # never store (and later repeat) a harmful request
        import remindai                               # fix 93: time words left in the task = not fully understood
        if remindai.suspicious(task):
            return remindai.understand(text, rem)     # the AI fills a form, the code checks it, you confirm
        if not due:
            _pending_task = task                      # remember it, and ask for the time
            _pending_repeat = rep                     # fix 47
            return "Sure. When should I remind you?"
        if rem.duplicate(due, task or "your reminder"):            # fix 54: said twice (normal)
            return f"You already have that reminder: {task or 'your reminder'}, {when_words(due)}."
        rem.add(due, task or "your reminder", "reminder", rep)
        return _set_reply(task, due, rep)                    # fix 47

    return None


if __name__ == "__main__":
    for q in ["remind me to call mom at 5 pm",
              "remind me in 10 minutes to drink water",
              "remind me tomorrow at 9 to submit the project",
              "set a timer for 5 minutes",
              "set a 2 minute timer",
              "remind me at 5 to call mom"]:
        due, phrase = parse_when(q)
        print(f"{q}\n   -> {due:%a %d %b %I:%M %p}  ({when_words(due)})" if due else f"{q}\n   -> not understood")