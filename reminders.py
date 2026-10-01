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
        tomorrow = "tomorrow" in t
        if tomorrow and not ampm and 1 <= hour <= 6:
            hour += 12                    # "tomorrow at 5" almost always means 5 pm
        due = (now + timedelta(days=1 if tomorrow else 0)).replace(
            hour=hour, minute=minute, second=0, microsecond=0)
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
        self.db.commit()

    def add(self, due, text, kind):
        self.db.execute("INSERT INTO reminders (due, text, kind) VALUES (?, ?, ?)",
                        (due.timestamp(), text, kind))
        self.db.commit()

    def upcoming(self):
        return [(datetime.fromtimestamp(d), t, k) for d, t, k in self.db.execute(
            "SELECT due, text, kind FROM reminders WHERE done = 0 ORDER BY due")]

    def due_now(self):
        return self.db.execute(
            "SELECT id, text, kind, due FROM reminders WHERE done = 0 AND due <= ? ORDER BY due",
            (time.time(),)).fetchall()

    def mark_done(self, rid):
        self.db.execute("UPDATE reminders SET done = 1 WHERE id = ?", (rid,))
        self.db.commit()

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


_pending_task = None   # "remind me to call dad" with no time -> wait for the time in the next sentence


def handle_reminder_command(text, rem):
    """Returns a spoken reply if this was a reminder/timer command, otherwise None."""
    global _pending_task
    t = FILLER.sub("", text.lower().strip(" .!?,"))

    # The previous turn asked "When should I remind you?" -> this may be just the time
    if _pending_task is not None:
        task, _pending_task = _pending_task, None
        due, _ = parse_when(t)
        if not due:
            due, _ = parse_when("at " + t)          # "6 pm" on its own
        if not due:
            due, _ = parse_when("in " + t)          # "two minutes" on its own
        if due:
            if is_unsafe(task):
                return REFUSAL
            rem.add(due, task or "your reminder", "reminder")
            if task:
                return f"Okay, I'll remind you: {task}, {when_words(due)}."
            return f"Okay, reminder set {when_words(due)}."
        # not a time after all: carry on and treat it as a normal sentence

    # Cancel comes first, so "cancel that reminder" cancels instead of reading it out
    m = CANCEL.search(t)
    if m:
        kind = "timer" if m.group(2).startswith("timer") else "reminder"
        n = rem.cancel(kind, everything=bool(re.search(r"\b(all|every|reminders|timers)\b", t)))
        return f"Cancelled {n} {kind}{'s' if n != 1 else ''}." if n else f"You don't have any {kind}s set."

    if LAST_Q.search(t):
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
        items = rem.upcoming()
        if not items:
            return "You don't have any reminders or timers set."
        parts = [("a timer" if k == "timer" else x) + f", {when_words(d)}" for d, x, k in items]
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
        due, phrase = parse_when(t)
        if not due and re.match(rf"({_NUM})\s+(second|minute|hour)", m.group(1)):
            due, p = parse_when("in " + m.group(1))     # "remind me two minutes to ..." (no "in")
            phrase = p[3:] if p else None                # drop the "in " we added
        task = m.group(1)
        if phrase:
            task = task.replace(phrase, " ")
            task = re.sub(r"\b(tomorrow|today)\b", " ", task)
        task = re.split(r"[.!?]", task)[0]                       # keep only the first sentence
        task = " ".join(task.split())
        for _ in range(3):                                       # "for me to ..." -> "..."
            task = re.sub(r"^(?:to|that|about|for|me)\s+", "", task)
        task = task.strip(" ,.")
        if task.lower() in ("me", "it", "this", "that", "something"):
            task = ""
        if re.match(r"(what|who|when|where|why|how)\b", task):
            return None      # "remind me what my meeting is" is a question, not a new reminder
        if is_unsafe(task):
            return REFUSAL   # never store (and later repeat) a harmful request
        if not due:
            _pending_task = task                      # remember it, and ask for the time
            return "Sure. When should I remind you?"
        rem.add(due, task or "your reminder", "reminder")
        if task:
            return f"Okay, I'll remind you: {task}, {when_words(due)}."
        return f"Okay, reminder set {when_words(due)}."

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