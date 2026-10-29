"""To-do list and the "Today" card (fix 52). Pure Python on the device: no LLM, no internet.

Tasks are kept in the same memories.db (table "todos"): ticked tasks stay with a check mark instead of disappearing.
The Today card shows the whole day in one place:
  - Timeline: meetings, appointments and reminders with a time (from the reminders and the schedule list)
  - Tasks: things to do today, important ones first, with tick boxes you can tap
  - Left from before: unfinished tasks from earlier days (Jarvis asks before moving them)
Also: clash warnings, "when am I free?", meeting tasks land here, and an evening check."""
import re
import time
from datetime import date, datetime, timedelta

from tools import resolve_dates
from safety import is_unsafe, REFUSAL

TODO_NAMES = {"task", "tasks", "to do", "todo", "to-do", "to dos", "todos", "to-dos"}
_LISTWORD = r"(?:to-?\s?dos?|todos?|tasks?)(?:\s+list)?"
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ("january|february|march|april|may|june|july|august|september|october|november|december"
          "|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_DAYWORD = (rf"today|tonight|tomorrow|day after tomorrow|(?:this |next )?(?:{'|'.join(WEEKDAYS)})"
            rf"|\d{{1,2}}(?:st|nd|rd|th)?(?: of)? (?:{MONTHS})|(?:{MONTHS}) \d{{1,2}}(?:st|nd|rd|th)?")
DEADLINE = re.compile(rf"[,\s]*\b(?:by|before|due(?: on| by)?|latest by|no later than)\s+(?:the\s+)?({_DAYWORD})\b", re.I)
PLANNED = re.compile(rf"[,\s]*\b(?:(?:on|for)\s+(?:the\s+)?({_DAYWORD})|(today|tonight|tomorrow|day after tomorrow))\b",
                     re.I)
IMPORTANT = re.compile(r"[,\s]*\b(?:(?:it'?s|it is|is|that'?s|this is|mark (?:it )?(?:as )?|as|make it)\s+)?"
                       r"(?:very |really |super )?(?:important|urgent|high priority|top priority|a priority)\b", re.I)
EVENT_WORDS = re.compile(r"\b(?:meeting|meet|appointment|interview|exam|test|class|lecture|call with|call|presentation"
                         r"|demo|viva|doctor|dentist|clinic|hospital|party|wedding|function|flight|train|bus|visit"
                         r"|session|standup|stand-up|review|seminar|webinar|workshop)\b", re.I)
CLOCK = re.compile(r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?\s?m\.?|p\.?\s?m\.?|o'?clock)?(?=\s|$|[,.])", re.I)

ADD = re.compile(rf"^(?:please\s+)?(?:add|put|note|write)\s+(.+?)\s+(?:to|in|on|into)\s+(?:my\s+|the\s+)?{_LISTWORD}\b(.*)$", re.I)
NEW = re.compile(r"^(?:please\s+)?(?:add|create|make|new)\s+(?:a\s+)?(?:new\s+)?(?:task|to-?\s?do)(?:\s+to)?[:,]?\s+(.+)$", re.I)
SHOW = re.compile(rf"\b(?:what(?:'s| is| are)|read|show|tell me|list)\b.*\b(?:my\s+|the\s+)?{_LISTWORD}\b"
                  r"|\bwhat(?:'s| is) left (?:to do|today|on my list|for today)\b|\bwhat do i (?:have|need) to do\b"
                  r"|\bwhat(?:'s| is) (?:pending|remaining)\b|\bmy tasks? (?:for )?today\b"
                  r"|\bwhat do i have\b.*\b(?:to-?\s?dos?|todos?|tasks?)\b"     # fix 54: what do I have in my to-do
                  r"|\bhow many (?:tasks?|to-?\s?dos?|things)\b(?! (?:on|in) my (?!to-?\s?do|tasks?)\w+ list)",
                  re.I)                                       # fix 97: "how many tasks are left?" (not the grocery list)
CARD = re.compile(r"\b(?:show|open|display)(?: me)? (?:my |the )?(?:day|today|to-?\s?do|todo|tasks?|plan)\b", re.I)
MARK = re.compile(rf"^(?:please\s+)?(?:mark|tick|check)(?: off)?\s+(.+?)(?:\s+(?:as\s+)?(?:done|complete(?:d)?|finished))?"
                  rf"(?:\s+(?:on|from|in|off) (?:my |the )?{_LISTWORD})?$"
                  r"|^(?:i(?:'ve| have)?\s+)?(?:finished|completed|done with|did)\s+(.+?)$"
                  r"|^(.+?)\s+(?:is|are)\s+(?:done|finished|complete(?:d)?)$", re.I)
UNMARK = re.compile(rf"^(?:please\s+)?(?:untick|uncheck|unmark)\s+(.+?)$"
                    r"|^(?:mark\s+)?(.+?)\s+(?:as\s+)?(?:not done|undone|not finished|pending|incomplete)$"
                    r"|^(.+?)\s+(?:is|are)\s+not\s+(?:done|finished|complete(?:d)?)(?: yet)?$", re.I)
REMOVE = re.compile(rf"^(?:please\s+)?(?:remove|delete)\s+(.+?)\s+from\s+(?:my\s+|the\s+)?{_LISTWORD}$"
                    r"|^(?:please\s+)?(?:remove|delete)\s+(?:the\s+)?task\s+(.+)$", re.I)
CLEAR_DONE = re.compile(rf"\b(?:clear|remove|delete)\b.*\b(?:done|finished|completed|ticked)\b.*\b(?:tasks?|to-?\s?dos?|items?)\b", re.I)
CLEAR_ALL = re.compile(rf"^(?:please\s+)?(?:clear|empty|wipe)\s+(?:out\s+)?(?:my\s+|the\s+)?{_LISTWORD}$", re.I)
FREE = re.compile(r"\b(?:when am i free|when (?:am i|i am) not busy|when do i have (?:free )?time|my free time"
                  r"|am i free\b|do i have (?:any )?free time|am i busy)\b", re.I)
EVENING_Q = re.compile(r"\b(?:evening check|how did my day go|how was my day|how much did i (?:do|finish|get done)"
                       r"|review my day|what did i (?:finish|complete|get done) today)\b", re.I)
# fix 107: "What should I do first?" -> one task, picked by code, with the reason
WHAT_FIRST = re.compile(
    r"\bwhat should i (?:do|start|work on|focus on|pick|finish|tackle)(?:\s+(?:first|next|now|today))?\s*[?.!]*$"
    r"|\bwhere (?:should|do) i start\b|\bwhat (?:to|do i|can i) do (?:first|next)\b"
    r"|\bwhat(?:'s| is) (?:my )?(?:top|first|next|biggest|main) (?:priority|task|thing|job)\b"
    r"|\bwhat(?:'s| is) (?:the )?(?:one|first) thing\b|\bwhat(?:'s| is) (?:the )?most important (?:task|thing)?"
    r"|\bwhich (?:task|one|work|job|thing) (?:first|should i do|do i do)\b|\bhelp me prioriti[sz]e\b|\bprioriti[sz]e my\b", re.I)
NOT_THAT = re.compile(r"^(?:not (?:that|this)(?: one)?|something else|another one|(?:the )?next(?: one)?|skip(?: it| that)?"
                      r"|anything else|what else|i (?:can'?t|don'?t want to) do that(?: now)?)\b", re.I)
_first = {"skipped": [], "at": 0.0}


def _why(r, today):
    """The reason, in plain words: the code's own ranking, said back."""
    if r["due"] and r["due"] < today:
        d = date.fromisoformat(r["due"])
        return f"it was due on {d:%A}, so it's overdue"
    if r["due"] == today:
        return "it's due today" + (", and it's marked important" if r["important"] else "")
    if r["important"]:
        return "it's marked important"
    if r["due"]:
        return _due_words(r["due"]).replace("due", "it's due", 1)
    if r["day"] and r["day"] < today:
        return f"it's been waiting since {date.fromisoformat(r['day']):%A}"
    return "it's the first one on today's list"


def what_first(memory, reminders=None, skip=()):
    """fix 107: one task to start with, ranked by the code (never the AI):
    overdue -> due today -> important -> due in the next 2 days -> waiting since an earlier day -> the rest, oldest first."""
    today = _iso(date.today())
    soon = _iso(date.today() + timedelta(days=2))
    pool = [r for r in _rows(memory, "done = 0 AND (day <= ? OR (due IS NOT NULL AND due <= ?))", (today, soon))
            if r["id"] not in skip]
    if not pool:
        if skip:
            return {"say": "That was the last one on your list. Want to add something, or take a short break?",
                    "card": False}
        left = _rows(memory, "done = 0")
        return {"say": ("Nothing is planned for today. Want to start one from later: " + left[0]["text"] + "?")
                if left else "Your to-do list is empty. Want to add something?", "card": False}

    def rank(r):
        overdue = bool(r["due"] and r["due"] < today)
        due_today = r["due"] == today
        due_soon = bool(r["due"] and today < r["due"] <= soon)
        older = bool(r["day"] and r["day"] < today)
        return (not overdue, not due_today, not r["important"], not due_soon, not older, r["day"] or "", r["id"])
    best = sorted(pool, key=rank)[0]
    _first.update(skipped=list(skip) + [best["id"]], at=time.time())
    global _touched
    _touched = best["id"]                                        # so "mark it done" means this task
    _TOUCH["at"] = time.time()
    say = f"Start with: {best['text']}. Because {_why(best, today)}."
    try:                                                         # a reminder very soon comes first
        now = datetime.now()
        near = [(d, t, k) for d, t, k in (reminders.upcoming() if reminders else [])
                if now <= d <= now + timedelta(minutes=30)]
        if near:
            mins = max(1, int((near[0][0] - now).total_seconds() // 60))
            what = "your timer goes off" if near[0][2] == "timer" else f"your reminder: {near[0][1]}"
            say += f" Heads up: in {mins} minute{'s' if mins != 1 else ''}, {what}."
    except Exception:
        pass
    others = len(pool) - 1
    if others:
        say += " Say: next one, if you'd rather do something else."
    return {"say": say, "card": False}


YES = re.compile(r"^(?:yes|yeah|yep|yup|sure|ok(?:ay)?|please(?: do)?|do it|go ahead|move (?:them|it)|haan|ha)\b", re.I)
NO = re.compile(r"^(?:no|nope|not now|leave (?:them|it)|don'?t|never ?mind|keep (?:them|it))\b", re.I)
_SKIP = {"mark", "tick", "check", "done", "task", "tasks", "todo", "list", "with", "that", "this", "finished",
         "completed", "complete", "from", "please", "have", "the", "untick", "uncheck", "pending", "remove", "delete",
         "today", "tomorrow"}

_pending = None          # {"kind": "carry" | "tomorrow" | "pick", ..., "at": time}
_touched = None          # id of the task just added / ticked, for "mark it done"
_ready = set()           # databases already set up
REMINDERS = None         # set by main.py, so a tap on the screen can redraw the card


# ---------- storage ----------
def _db(memory):
    db = memory.db
    if id(db) not in _ready:
        db.execute("CREATE TABLE IF NOT EXISTS todos (id INTEGER PRIMARY KEY, text TEXT, day TEXT, due TEXT, "
                   "important INTEGER DEFAULT 0, done INTEGER DEFAULT 0, done_day TEXT, created REAL)")
        db.commit()
        _ready.add(id(db))
        # tasks saved by older versions (the "task" list, e.g. from meeting mode) move here once
        old = db.execute("SELECT id, item FROM list_items WHERE list IN ('task', 'tasks', 'to do', 'todo', 'to-do')"
                         ).fetchall()
        for rid, item in old:
            m = re.search(r"\s*\(by ([^)]+)\)\s*$", item)
            text = item[:m.start()] if m else item
            _insert(memory, text, due=_date_of(m.group(1)) if m else None)
            db.execute("DELETE FROM list_items WHERE id = ?", (rid,))
        if old:
            db.commit()
            print(f"   (moved {len(old)} old task(s) to the new to-do list)")
    return db


def _iso(d):
    return d.isoformat() if d else None


def _date_of(phrase):
    """'Friday' / 'by tomorrow' / '3 November' -> a date (or None)."""
    tagged = resolve_dates(phrase)
    m = re.search(r"\[date: \w+, (\d{1,2}) (\w+) (\d{4})\]", tagged)
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y").date()
    except ValueError:
        return None


def _insert(memory, text, day=None, due=None, important=False):
    global _touched
    cur = memory.db.execute("INSERT INTO todos (text, day, due, important, created) VALUES (?, ?, ?, ?, ?)",
                            (text, _iso(day or date.today()), _iso(due), int(important), time.time()))
    memory.db.commit()
    _touched = cur.lastrowid
    _TOUCH["at"] = time.time()   # fix 71a
    return cur.lastrowid


def _rows(memory, where="1=1", args=()):
    _db(memory)
    return [dict(zip(("id", "text", "day", "due", "important", "done", "done_day"), r)) for r in memory.db.execute(
        f"SELECT id, text, day, due, important, done, done_day FROM todos WHERE {where} ORDER BY id", args)]


def _today_tasks(memory):
    """Tasks planned for today (or with a deadline that has come), plus the ones ticked today."""
    t = _iso(date.today())
    rows = _rows(memory, "(day = ? AND done = 0) OR (done = 1 AND done_day = ?) OR (done = 0 AND due IS NOT NULL "
                         "AND due <= ? AND day <= ?)", (t, t, t, t))
    return sorted(rows, key=lambda r: (r["done"], -r["important"], r["due"] or "9999", r["id"]))


def _older(memory):
    t = _iso(date.today())
    return [r for r in _rows(memory, "done = 0 AND day < ?", (t,)) if not (r["due"] and r["due"] <= t)]


def _later(memory):
    return _rows(memory, "done = 0 AND day > ?", (_iso(date.today()),))


def clear_all(memory):
    _db(memory).execute("DELETE FROM todos")
    memory.db.commit()


# ---------- words ----------
def _words(s):
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w
            for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2} - _SKIP


def _due_words(due):
    if not due:
        return ""
    d = date.fromisoformat(due)
    diff = (d - date.today()).days
    if diff < 0:
        return "overdue"
    return "due today" if diff == 0 else "due tomorrow" if diff == 1 else f"due {d:%A}" if diff < 7 else f"due {d.day} {d:%b}"


def _spoken(r):
    extra = [x for x in (("important" if r["important"] else ""), _due_words(r["due"])) if x]
    return r["text"] + (f" ({', '.join(extra)})" if extra else "")


def _find(memory, said, pool=None):
    """The task(s) the user means. 'it' / 'that' = the one just added or ticked."""
    pool = pool if pool is not None else _rows(memory)
    if re.fullmatch(r"(?:it|that|this|that one|this one|the last one)", said.strip(" .").lower()):
        return [r for r in pool if r["id"] == _touched][:1]
    words = _words(said)
    if not words:
        return []
    scored = [(len(words & _words(r["text"])), r) for r in pool]
    best = max((s for s, _ in scored), default=0)
    return [r for s, r in scored if s == best and s > 0]


def _event_like(text):
    return bool(EVENT_WORDS.search(text))


def _clock_of(text):
    """'meeting with Rahul at 5 pm' -> (17, 0) or None."""
    for m in CLOCK.finditer(re.sub(r"\[date:.*?\]", "", text)):
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
        if not ap and not m.group(0).lower().startswith("at"):
            continue                                        # a bare number isn't a time ("2 apples")
        if h > 23 or mi > 59:
            continue
        if ap.startswith("p") and h < 12:
            h += 12
        elif ap.startswith("a") and h == 12:
            h = 0
        elif not ap.startswith(("a", "p")) and 1 <= h <= 7:
            h += 12                                          # "at 5" in a plan is almost always 5 pm
        return h, mi
    return None


# ---------- the day's timeline ----------
def timeline(memory, reminders, day=None):
    """[(datetime, text, is_event)] for one day: reminders and schedule items that have a time."""
    day = day or date.today()
    out = []
    if reminders is not None:
        for due, text, kind in reminders.upcoming():
            if kind == "reminder" and due.date() == day:
                out.append((due, text, _event_like(text)))
    tag = f"[date: {day:%A}, {day.day} {day:%B %Y}]"
    for item in memory.list_get("schedule"):
        if tag in item:
            clk = _clock_of(item)
            text = re.sub(r"\s*\[date:.*?\]", "", item)
            text = re.sub(r"\b(?:today|tomorrow|tonight)\b", "", text, flags=re.I)
            if clk:
                text = re.sub(r"\b(?:at\s+)?\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?clock)?(?=\s|$|[,.])",
                              "", text, count=1, flags=re.I)
            text = " ".join(text.split()).strip(" ,.")
            when = datetime(day.year, day.month, day.day, *clk) if clk else None
            out.append((when, text, True))
    # the same plan saved twice (a reminder and a schedule item): keep one
    kept = []
    for w, t, ev in sorted(out, key=lambda x: (x[0] is not None, x[0] or datetime.min)):
        if any((w == w2 or w is None or w2 is None) and len(_words(t) & _words(t2)) >= 2 for w2, t2, _ in kept):
            continue
        kept.append((w, t, ev))
    return kept


def _clock(w):
    return w.strftime("%I:%M %p").lstrip("0").replace(":00", "")


# ---------- the Today card ----------
def _read_day(memory, reminders, d):
    """Fix 54: 'what's on my to-do list for tomorrow?' -> that day's tasks and timeline."""
    label = "tomorrow" if d == date.today() + timedelta(days=1) else f"{d:%A}"
    rows = _rows(memory, "done = 0 AND (day = ? OR due = ?)", (_iso(d), _iso(d)))
    parts = [f"On your to-do list for {label}: " + "; ".join(_spoken(r) for r in rows) + "." if rows
             else f"No tasks are planned for {label}."]
    plans = [(w, t) for w, t, _ in timeline(memory, reminders, d) if w]
    if plans:
        parts.append("On your timeline: " + "; ".join(f"{_clock(w)}, {t}" for w, t in plans) + ".")
    return " ".join(parts)


# ---------- fix 59: "Did I ...?" and "I called mom" (ticks the to-do and the reminder that rang) ----------
PAST = {"called": "call", "took": "take", "taken": "take", "drank": "drink", "drunk": "drink", "paid": "pay",
        "bought": "buy", "sent": "send", "went": "go", "gone": "go", "ate": "eat", "eaten": "eat", "did": "do",
        "done": "do", "fed": "feed", "met": "meet", "wrote": "write", "written": "write", "read": "read",
        "gave": "give", "given": "give", "got": "get", "made": "make", "brought": "bring", "spoke": "speak",
        "spoken": "speak", "slept": "sleep", "ran": "run", "swam": "swim", "drove": "drive", "left": "leave",
        "kept": "keep", "put": "put", "sold": "sell", "told": "tell", "found": "find", "taught": "teach",
        "studied": "study", "applied": "apply", "submitted": "submit", "emailed": "email", "texted": "text"}
NOT_DONE = {"want", "plan", "miss", "tri", "try", "decid", "start", "hop", "lik", "lov", "hat", "us", "fail", "wish",
            "intend", "promis", "agre", "schedul", "remind", "ask", "receiv", "learn", "worri", "notic", "listen", "plann",
            "forget", "get", "set", "add", "save", "have", "be", "tell", "say", "mean", "feel"}
NOT_PAST = {"need", "bleed", "speed", "seed", "breed", "proceed", "exceed", "succeed", "red", "bed", "shed", "wed"}
DID_Q = re.compile(r"^(?:did|have)\s+i\s+(?:already\s+|really\s+|even\s+)?(\w+)\b\s*(.*?)[?.!]*$", re.I)
SAID_DONE = re.compile(r"^(?:ok(?:ay)?[, ]+|yes[, ]+|so[, ]+)?(?:i'?ve|i have|i)\s+(?:just\s+|already\s+|finally\s+)?"
                       r"(\w+)\b\s*(.*?)[.!]*$", re.I)


def _base(verb):
    v = verb.lower()
    if v in PAST:
        return PAST[v]
    if v.endswith("ed") and len(v) > 4 and v not in NOT_PAST:
        stem = v[:-2]
        if len(stem) > 2 and stem[-1] == stem[-2]:
            stem = stem[:-1]                                   # "stopped" -> "stop"
        return stem
    return None


def _obj(rest):
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w
            for w in re.findall(r"[a-z]+", rest.lower()) if len(w) > 2} - _SKIP - {"today", "yesterday", "already"}


def _matches(obj, text):
    return bool(obj and obj & _words(text))


def _log_rows(reminders, day):
    try:
        return reminders.log_day(day) if reminders is not None else []
    except Exception:
        return []


def _when(ts):
    return _clock(datetime.fromtimestamp(ts))


def _did_or_done(clean, memory, reminders):
    """'Did I take my medicine?' / 'I took my medicine' / 'I called mom' -> answer or tick. None if not about this."""
    reminders = reminders if reminders is not None else REMINDERS
    lower = clean.lower().strip(" .!?,")
    m = DID_Q.match(lower)
    if m and (_base(m.group(1)) or m.group(1)) not in NOT_DONE | {"is", "am"} \
            and not re.search(r"\b(?:any|anything|something|reminders?|meetings?|plans?|schedule|tasks?|to-?do|list"
                              r"|memor\w*|you|that)\b", m.group(2)):
        obj = _obj(m.group(2))
        if not obj:
            return None
        day = date.today() - (timedelta(days=1) if "yesterday" in lower else timedelta())
        label = "yesterday" if "yesterday" in lower else "today"
        for r in _rows(memory, "done = 1 AND done_day = ?", (_iso(day),)):
            if _matches(obj, r["text"]):
                return {"say": f"Yes, you ticked off {r['text']} {label}.", "card": False}
        logs = [lg for lg in _log_rows(reminders, day) if _matches(obj, lg[1])]
        done = [lg for lg in logs if lg[4]]
        if done:
            lg = done[-1]
            return {"say": f"Yes. Your {_when(lg[2])} reminder, {lg[1]}, was ticked at {_when(lg[4])}.", "card": False}
        if logs:
            lg = logs[-1]
            return {"say": f"Your {_when(lg[2])} reminder, {lg[1]}, rang, but it isn't ticked yet. If you've done it, "
                           "just tell me, or tap it on the screen.", "card": False}
        if label == "today" and reminders is not None:
            later = [(d, x) for d, x, k in reminders.upcoming() if k == "reminder" and d.date() == day
                     and _matches(obj, x)]
            if later:
                return {"say": f"Not yet. Your reminder for it is at {_clock(later[0][0])}.", "card": False}
        open_ = [r for r in _rows(memory, "done = 0") if _matches(obj, r["text"])]
        if open_:
            return {"say": f"Not yet. {open_[0]['text'][:1].upper() + open_[0]['text'][1:]} is still on your to-do list.",
                    "card": False}
        return {"say": f"I don't have a record of that {label}.", "card": False}
    m = SAID_DONE.match(lower)
    if m and _base(m.group(1)) and _base(m.group(1)) not in NOT_DONE and not m.group(2).startswith("to "):
        obj = _obj(m.group(2))
        if not obj:
            return None
        tasks = [r for r in _rows(memory, "done = 0") if _matches(obj, r["text"])]
        logs = [lg for lg in _log_rows(reminders, date.today()) if not lg[4] and _matches(obj, lg[1])]
        if not tasks and not logs:
            return None                                        # "I watched a movie": not a task, carry on
        for lg in logs:
            reminders.log_tick(lg[0], True)
        if tasks:
            return _apply(memory, "done", tasks)               # "Done: call mom. 2 left."
        names = list(dict.fromkeys(lg[1] for lg in logs))
        return {"say": f"Done: {', '.join(names)}. Well done!", "card": True}
    return None


def toggle_log(lid):
    """A tap on a reminder that rang today (fix 59)."""
    if REMINDERS is None:
        return None
    row = next((lg for lg in _log_rows(REMINDERS, date.today()) if lg[0] == lid), None)
    if not row:
        return None
    REMINDERS.log_tick(lid, not row[4])
    return f"{'Unticked' if row[4] else 'Done'}: {row[1]}."


def card(memory, reminders=None):
    reminders = reminders if reminders is not None else REMINDERS
    d = date.today()
    now = datetime.now()
    tl = [{"time": _clock(w) if w else "Today", "text": t, "past": bool(w and w < now)}
          for w, t, _ in timeline(memory, reminders)]
    tasks = [{"id": r["id"], "text": r["text"], "done": bool(r["done"]), "star": bool(r["important"]),
              "due": _due_words(r["due"])} for r in _today_tasks(memory)]
    rang = [{"id": lg[0], "time": _when(lg[2]), "text": lg[1], "done": bool(lg[4])}   # fix 59
            for lg in _log_rows(reminders, d)]
    return {"title": f"📅 Today · {d:%A} {d.day} {d:%B}", "lines": [],
            "today": {"timeline": tl, "tasks": tasks, "older": len(_older(memory)), "rang": rang}}


def summary_line(memory):
    left = [r for r in _today_tasks(memory) if not r["done"]]
    if not left:
        return None
    top = next((r for r in left if r["important"]), left[0])
    return f"On your to-do list: {len(left)} task{'s' if len(left) != 1 else ''} left, including {_spoken(top)}."


def carry_question(memory):
    """'2 tasks are left from before. Move them to today?' once a day."""
    global _pending
    older = _older(memory)
    if not older or memory.get_setting("todo_carry_asked") == _iso(date.today()):
        return None
    memory.set_setting("todo_carry_asked", _iso(date.today()))
    _pending = {"kind": "carry", "ids": [r["id"] for r in older], "at": time.time()}
    names = "; ".join(r["text"] for r in older[:3]) + (" and more" if len(older) > 3 else "")
    return (f"You have {len(older)} unfinished task{'s' if len(older) != 1 else ''} from before: {names}. "
            f"Should I move {'it' if len(older) == 1 else 'them'} to today?")


# ---------- clash and free time ----------
def clash_for_schedule(memory, reminders):
    """After 'add ... to my schedule': does the newest schedule item clash with something?"""
    items = memory.list_get("schedule")
    if not items:
        return None
    item = items[-1]
    m = re.search(r"\[date: \w+, (\d{1,2}) (\w+) (\d{4})\]", item)
    clk = _clock_of(item)
    if not m or not clk:
        return None
    d = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%d %B %Y")
    text = " ".join(re.sub(r"\b(?:today|tomorrow|tonight)\b", "", re.sub(r"\s*\[date:.*?\]", "", item),
                           flags=re.I).split()).strip(" ,.")
    bare = re.sub(r"\b(?:at\s+)?\d{1,2}(?:[:.]\d{2})?\s*(?:a\.?\s?m\.?|p\.?\s?m\.?|o'?clock)?(?=\s|$|[,.])",
                  "", text, count=1, flags=re.I)              # fix 73: the time is not part of the name on the timeline
    bare = " ".join(bare.split()).strip(" ,.")
    return clash_note(memory, reminders, d.replace(hour=clk[0], minute=clk[1]), text, skip_text=bare)


def clash_note(memory, reminders, when, text, skip_text=None):
    """A plan at a time that's already taken: a short warning, or None."""
    if not when or not _event_like(text):
        return None
    for w, t, ev in timeline(memory, reminders, when.date()):
        if not w or not ev or (skip_text and t == skip_text) or t == text:
            continue
        if abs((w - when).total_seconds()) < 45 * 60:
            return f"Heads up: you already have {t} at {_clock(w)}."
    return None


def free_time(memory, reminders, day=None, at=None):
    """'You're free until 5 PM, and after 6 PM.' Busy = meetings and appointments (one hour each)."""
    day = day or date.today()
    now = datetime.now()
    start = max(datetime(day.year, day.month, day.day, 8, 0), now if day == now.date() else datetime.min)
    end = datetime(day.year, day.month, day.day, 22, 0)
    busy = sorted((w, w + timedelta(hours=1), t) for w, t, ev in timeline(memory, reminders, day) if w and ev)
    label = "today" if day == now.date() else "tomorrow" if day == now.date() + timedelta(days=1) else f"on {day:%A}"
    if at:
        hit = next(((s, t) for s, e, t in busy if s <= at < e), None)
        return f"No, you have {hit[1]} at {_clock(hit[0])}." if hit else f"Yes, you're free at {_clock(at)} {label}."
    if start >= end:
        return "It's late, so the day is almost over. Nothing else is planned." if not busy else None
    if not [b for b in busy if b[1] > start]:
        return f"You're free {label}: no meetings or appointments on your timeline."
    gaps, cur = [], start
    for s, e, _ in busy:
        if s > cur + timedelta(minutes=29):
            gaps.append((cur, s))
        cur = max(cur, e)
    if cur < end - timedelta(minutes=29):
        gaps.append((cur, end))
    if not gaps:
        return f"You're busy {label}, with no free gap between your plans."
    parts = []
    for a, b in gaps[:3]:
        if a == start and day == now.date() and now.hour >= 8:
            parts.append(f"from now until {_clock(b)}" if b < end else "for the rest of the day")
        elif a == start and b < end:
            parts.append(f"until {_clock(b)}")
        elif b >= end:
            parts.append(f"after {_clock(a)}")
        else:
            parts.append(f"from {_clock(a)} to {_clock(b)}")
    return f"You're free {label} " + ", and ".join(parts) + "."


# ---------- the evening check ----------
def evening_due(memory, now=None):
    """Once a day after 8 PM, if there were tasks today: how it went, and an offer to move what's left."""
    global _pending
    now = now or datetime.now()
    if now.hour < 20 or memory.get_setting("todo_evening") == _iso(now.date()):
        return None
    tasks = _today_tasks(memory)
    if not tasks:
        return None
    memory.set_setting("todo_evening", _iso(now.date()))
    return evening(memory, tasks)


def evening(memory, tasks=None):
    global _pending
    tasks = tasks if tasks is not None else _today_tasks(memory)
    if not tasks:
        return "There were no tasks on your to-do list today."
    done = [r for r in tasks if r["done"]]
    left = [r for r in tasks if not r["done"]]
    if not left:
        return f"You finished all {len(tasks)} of your tasks today. Well done!"
    _pending = {"kind": "tomorrow", "ids": [r["id"] for r in left], "at": time.time()}
    return (f"You finished {len(done)} of {len(tasks)} tasks today. Left: "
            + "; ".join(r["text"] for r in left[:3]) + (" and more" if len(left) > 3 else "")
            + f". Should I move {'it' if len(left) == 1 else 'them'} to tomorrow?")


# ---------- voice commands ----------
def add_task(memory, text, due_text=None):
    """Used by meeting mode too. Returns the spoken reply."""
    if is_unsafe(text):
        return REFUSAL
    due = _date_of(due_text) if due_text else None
    if due_text and not due:
        text = f"{text} (by {due_text})"
    return _add(memory, text, due=due)


def _add(memory, raw, due=None):
    text, day, important = raw.strip(" .,"), None, False
    m = IMPORTANT.search(text)
    if m:
        important, text = True, (text[:m.start()] + text[m.end():]).strip(" .,")
    m = DEADLINE.search(text)
    if m and not due:
        due = _date_of(m.group(1))
        text = (text[:m.start()] + text[m.end():]).strip(" .,")
    m = PLANNED.search(text)
    if m and (m.group(1) or m.group(2)):
        d = _date_of(m.group(1) or m.group(2))
        if d:
            day = d
            text = (text[:m.start()] + text[m.end():]).strip(" .,")
    text = re.sub(r"^(?:to|that i (?:need|have) to|i (?:need|have) to)\s+", "", text, flags=re.I)
    text = re.sub(r"\s+(?:and|also)$", "", text).strip(" .,")
    if not text:
        return "What should I add to your to-do list?"
    if len(text) > 120:
        return "That's a bit long for a task. Could you say it shorter?"
    if is_unsafe(text):
        return REFUSAL
    same = [r for r in _rows(memory, "done = 0") if r["text"].lower() == text.lower()]
    if same:
        return f"{text[:1].upper() + text[1:]} is already on your to-do list."
    _insert(memory, text, day=day, due=due, important=important)
    when = "" if not day or day == date.today() else (" for tomorrow" if day == date.today() + timedelta(days=1)
                                                      else f" for {day:%A}")
    extra = [x for x in (("marked important" if important else ""), _due_words(_iso(due))) if x]
    return f"Added to your to-do list{when}: {text}" + (f", {' and '.join(extra)}" if extra else "") + "."


def _read(memory, reminders):
    tasks = _today_tasks(memory)
    left = [r for r in tasks if not r["done"]]
    done = [r for r in tasks if r["done"]]
    later = _later(memory)
    if not tasks and not later and not _older(memory):
        return "Your to-do list is empty. Say, for example: add call the bank to my to-do list."
    if left:
        reply = (f"You have {len(left)} task{'s' if len(left) != 1 else ''} left today: "
                 + "; ".join(_spoken(r) for r in left) + ".")
    else:
        reply = "Nothing is left on your to-do list for today."
    if done:
        reply += f" {len(done)} {'is' if len(done) == 1 else 'are'} done."
    if later and len(later) <= 3:                                # fix 92: say WHICH tasks are planned for later
        reply += (" And planned for later: "
                  + "; ".join(f"{r['text']}, " + (lambda w: w if w in ("today", "tomorrow") else "on " + w)(
                      _day_words(date.fromisoformat(r['day']))) for r in later) + ".")
    elif later:
        reply += f" And {len(later)} planned for later."
    q = carry_question(memory)
    return reply + (" " + q if q else "")


def _set_done(memory, rows, done):
    global _touched
    for r in rows:
        memory.db.execute("UPDATE todos SET done = ?, done_day = ? WHERE id = ?",
                          (int(done), _iso(date.today()) if done else None, r["id"]))
        _touched = r["id"]
        _TOUCH["at"] = time.time()   # fix 71b
    memory.db.commit()


def toggle(memory, tid):
    """A tap on the screen. Returns what to say."""
    rows = _rows(memory, "id = ?", (tid,))
    if not rows:
        return None
    r = rows[0]
    _set_done(memory, rows, not r["done"])
    return f"{'Unticked' if r['done'] else 'Done'}: {r['text']}."


def handle_todo(text, memory, reminders):
    """Returns {"say": ..., "card": bool} if this was about the to-do list / the day, otherwise None."""
    global _pending
    clean = re.sub(r"^(?:ok(?:ay)?|so|and|please|hey|can you|could you)[,]?\s+", "", text.strip(" .!?,"), flags=re.I)
    clean = re.sub(r"\b(?:total|tool|todu|tudu|tu du|two do|to-do's|todos)\s+list\b", "to-do list", clean,
                   flags=re.I)                                        # fix 53: "to-do" misheard
    clean = re.sub(r"\b(?:do-?\s?do|due do|dodo|doo doo)\b", "to-do", clean, flags=re.I)   # fix 54
    clean = re.sub(r"\bwhen am i (?:through|tree|three)\b", "when am i free", clean, flags=re.I)
    lower = clean.lower()
    lower = clean.lower()

    if WHAT_FIRST.search(lower):                                 # fix 107
        return what_first(memory, reminders)
    if NOT_THAT.match(lower) and time.time() - _first["at"] < 180:
        return what_first(memory, reminders, skip=_first["skipped"])   # edit option: "not that one"

    if _pending and time.time() - _pending["at"] < 180:
        p, _pending = _pending, None
        if p["kind"] in ("carry", "tomorrow") and (YES.match(lower) or NO.match(lower)):
            if NO.match(lower):
                return {"say": "Okay, I'll leave them as they are.", "card": False}
            day = date.today() + (timedelta(days=1) if p["kind"] == "tomorrow" else timedelta())
            for tid in p["ids"]:
                memory.db.execute("UPDATE todos SET day = ? WHERE id = ? AND done = 0", (_iso(day), tid))
            memory.db.commit()
            n = len(p["ids"])
            return {"say": f"Done. {'It is' if n == 1 else f'All {n} are'} on your list for "
                           f"{'tomorrow' if p['kind'] == 'tomorrow' else 'today'}.", "card": p["kind"] == "carry"}
        if p["kind"] == "pick":
            m = re.search(r"\b(first|1st|one|second|2nd|two|third|3rd|three|both|all)\b",
                          re.sub(r"\b(?:on|oll|al|aal)\b", "all", lower))       # fix 91: "all" heard as "on"
            if m:
                rows = p["rows"]
                i = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1, "third": 2, "3rd": 2,
                     "three": 2}.get(m.group(1))
                chosen = rows if i is None else rows[i:i + 1]
                if chosen:
                    return _apply(memory, p["action"], chosen)
        # anything else: carry on normally

    m = re.search(r"\bmove (?:them|those|it|the (?:old|unfinished|pending|other|remaining) (?:tasks?|ones)"
                  r"|(?:all )?(?:the )?(?:unfinished|pending|remaining) tasks?|the rest)(?: to)? (today|tomorrow)\b", lower)
    if m:
        if m.group(1) == "today":
            rows = _older(memory)
            day = date.today()
        else:
            rows = [r for r in _today_tasks(memory) if not r["done"]]
            day = date.today() + timedelta(days=1)
        if not rows:
            return {"say": "There's nothing to move.", "card": False}
        for r in rows:
            memory.db.execute("UPDATE todos SET day = ? WHERE id = ?", (_iso(day), r["id"]))
        memory.db.commit()
        return {"say": f"Done. Moved {len(rows)} task{'s' if len(rows) != 1 else ''} to {m.group(1)}.", "card": True}

    _ed = _edit_todo(clean, lower, memory, reminders)        # fix 71
    if _ed:
        return _ed

    if EVENING_Q.search(lower):
        return {"say": evening(memory), "card": True}

    m = FREE.search(lower)
    if m:
        day = date.today() + timedelta(days=1) if "tomorrow" in lower else date.today()
        at = None
        clk = _clock_of(lower)
        if clk and re.search(r"\bam i free\b|\bam i busy\b", lower):
            at = datetime(day.year, day.month, day.day, *clk)
        return {"say": free_time(memory, reminders, day, at), "card": False}

    m = ADD.match(clean) or ADD.match(re.sub(r"^.*?\b(?=(?:add|put)\s)", "", clean, count=1, flags=re.I))   # fix 54
    if m:
        return {"say": _add(memory, f"{m.group(1)} {m.group(2) or ''}"), "card": True}
    m = NEW.match(clean)
    if m:
        return {"say": _add(memory, m.group(1)), "card": True}

    m = CLEAR_ALL.match(clean)
    if m:
        clear_all(memory)
        return {"say": "Done, your to-do list is cleared.", "card": True}
    if CLEAR_DONE.search(lower):
        n = memory.db.execute("DELETE FROM todos WHERE done = 1").rowcount
        memory.db.commit()
        return {"say": f"Cleared {n} finished task{'s' if n != 1 else ''}." if n else "There are no finished tasks to clear.",
                "card": True}

    m = REMOVE.match(clean)
    if m:
        hits = _find(memory, m.group(1) or m.group(2))
        if not hits:
            return {"say": "I couldn't find that on your to-do list.", "card": False}
        return _ask_or(memory, "remove", hits)

    # fix 96: "mark call the bank as important" / "call the bank is urgent" / "it's not important any more"
    _imp = r"(?:very |really |super )?(?:important|urgent|high priority|top priority|a priority)"
    m = re.match(rf"^(?:please\s+)?(?:mark|make|set|flag|put)\s+(.+?)\s+(?:as\s+)?{_imp}$|^(.+?)\s+is\s+{_imp}$", lower)
    m_not = re.match(rf"^(?:please\s+)?unmark\s+(.+?)\s+(?:as\s+)?{_imp}$"
                     rf"|^(?:please\s+)?(?:mark|make)\s+(.+?)\s+(?:as\s+)?not\s+{_imp}$"
                     rf"|^(.+?)\s+is\s+(?:not|no longer)\s+{_imp}(?:\s+any\s*more)?$", lower)
    if m_not or (m and not re.search(r"\bnot\b", lower)):
        said = next((g for g in (m_not.groups() if m_not else m.groups()) if g), "")
        hits = _find(memory, said, [r for r in _rows(memory) if not r["done"]])
        if not hits:
            return {"say": "I couldn't find that on your to-do list.", "card": False}
        r = hits[0]
        memory.db.execute("UPDATE todos SET important = ? WHERE id = ?", (0 if m_not else 1, r["id"]))
        memory.db.commit()
        if m_not:
            return {"say": f"Okay, {r['text']} is no longer marked important.", "card": True}
        return {"say": f"Marked as important: {r['text']}. It's now near the top of your list.", "card": True}

    m = UNMARK.match(clean)
    if m:
        hits = _find(memory, next(g for g in m.groups() if g), [r for r in _rows(memory) if r["done"]])
        if hits:
            return _ask_or(memory, "untick", hits)
        if re.match(r"(?:untick|uncheck|unmark)\b", lower):
            return {"say": "I couldn't find a ticked task like that.", "card": False}
        return None

    _dd = _did_or_done(clean, memory, reminders)                 # fix 59
    if _dd:
        return _dd
    m = MARK.match(clean)
    if m:
        said = next(g for g in m.groups() if g)
        hits = _find(memory, said, [r for r in _rows(memory) if not r["done"]])
        if hits:
            return _ask_or(memory, "done", hits)
        if re.match(r"(?:mark|tick|check)\b", lower) and re.search(r"\b(?:to-?\s?do|todo|tasks?|list)\b", lower):
            return {"say": "I couldn't find that on your to-do list.", "card": False}
        return None                                     # "I finished my homework": not a task here, carry on

    if CARD.search(lower) and re.search(r"\b(?:to-?\s?do|todo|tasks?)\b", lower):
        return {"say": _read(memory, reminders), "card": True}
    if SHOW.search(lower):
        m_day = re.search(r"\b(?:for |on )?(day after tomorrow|tomorrow|monday|tuesday|wednesday|thursday|friday"
                          r"|saturday|sunday)\b", lower)                      # fix 54: "... for tomorrow"
        if m_day:
            d = _date_of(m_day.group(1))
            if d and d != date.today():
                return {"say": _read_day(memory, reminders, d), "card": False}
        return {"say": _read(memory, reminders), "card": True}
    return None


# ----- fix 71: rename a task, move it to another day, change its deadline, and "undo" -----
_TOUCH = {"at": 0.0}     # when a task was last added / ticked (so "it" can mean that task)
_undo = None             # {"t": time, "rows": [the rows as they were]}
_NOT_TODO = re.compile(r"\b(?:reminders?|alarms?|timers?|grocery|shopping|voice|volume|city|name|pin|birthday"
                       r"|blood group|password)\b", re.I)
RENAME = re.compile(r"^(?:please\s+)?(?:change|rename|edit|update|correct|fix)\s+(?:the\s+)?(?:task\s+|to-?do\s+)?(.+?)"
                    rf"\s+(?:to|into|as)\s+(.+?)(?:\s+(?:on|in)\s+(?:my\s+|the\s+)?{_LISTWORD})?$", re.I)
MOVE = re.compile(r"^(?:please\s+)?(?:move|shift|push|postpone|reschedule|change|put)\s+(?:the\s+)?(?:task\s+)?(.+?)"
                  rf"\s+(?:to|till|until|for|on)\s+(?:the\s+)?({_DAYWORD})(?:\s+instead)?$", re.I)
DUE = re.compile(rf"^(?:please\s+)?(?:make|change|set|move)\s+(?:the\s+)?(?:deadline|due date)\s+(?:of|for)\s+(.+?)"
                 rf"\s+to\s+(?:the\s+)?({_DAYWORD})$"
                 rf"|^(?:please\s+)?(?:make|set)\s+(.+?)\s+due\s+(?:on\s+|by\s+)?(?:the\s+)?({_DAYWORD})$", re.I)
UNDO_EDIT = re.compile(r"^(?:no,? )?(?:undo(?: (?:it|that))?|change it back|put it back)$", re.I)
_ONLY_WHEN = re.compile(rf"^(?:at\s+)?\d|^(?:{_DAYWORD})$|^(?:the\s+)?(?:next|this)\s+\w+$|^(?:\d+\s+)?(?:minutes?|hours?)", re.I)


def _day_words(d):
    diff = (d - date.today()).days
    return "today" if diff == 0 else "tomorrow" if diff == 1 else f"{d:%A}" if 1 < diff < 7 else f"{d.day} {d:%B}"


def _edit_targets(memory, said, reminders):
    """Which task(s) 'said' means. 'it' only if a task was the last thing touched (not a reminder)."""
    if re.fullmatch(r"(?:it|that|this|that one|this one)", said.strip(" .").lower()):
        r_at = (getattr(reminders, "touched", None) or (0, 0))[1] if reminders is not None else 0
        if not _touched or time.time() - _TOUCH["at"] > 600 or r_at > _TOUCH["at"]:
            return []
    hits = _find(memory, said, [r for r in _rows(memory) if not r["done"]])
    return hits or _find(memory, said, _rows(memory))


def _edit_todo(clean, lower, memory, reminders):
    global _undo
    if UNDO_EDIT.match(lower):
        if not _undo or time.time() - _undo["t"] > 120:
            return None
        u, _undo = _undo, None
        for r in u["rows"]:
            memory.db.execute("UPDATE todos SET text = ?, day = ?, due = ? WHERE id = ?",
                              (r["text"], r["day"], r["due"], r["id"]))
        memory.db.commit()
        return {"say": "Okay, I've put it back: " + "; ".join(_spoken(r) for r in u["rows"]) + ".", "card": True}
    if _NOT_TODO.search(lower):
        return None
    m = DUE.match(clean)
    if m:
        said, when = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        d = _date_of(when)
        hits = _edit_targets(memory, said, reminders) if d else []
        return _ask_or(memory, ("due", d), hits) if hits else None
    m = MOVE.match(clean)
    if m:
        d = _date_of(m.group(2))
        hits = _edit_targets(memory, m.group(1), reminders) if d else []
        return _ask_or(memory, ("move", d), hits) if hits else None
    m = RENAME.match(clean)
    if m and not _ONLY_WHEN.match(m.group(2).strip()):
        hits = _edit_targets(memory, m.group(1), reminders)
        new = re.sub(r"^(?:the\s+)?(?:task\s+)?", "", m.group(2).strip(" .,"), flags=re.I)
        if hits and new and len(new) <= 120:
            return _ask_or(memory, ("rename", new), hits)
    return None


def _edit_apply(memory, action, rows):
    global _undo, _touched
    kind, value = action
    _undo = {"t": time.time(), "rows": [dict(r) for r in rows]}
    try:
        import memfix                                      # "undo" now means this edit, not an older memory change
        memfix.LAST = None
        import lists
        lists._undo = None
    except ImportError:
        pass
    old = "; ".join(r["text"] for r in rows)
    if kind == "rename":
        if is_unsafe(value):
            _undo = None
            return {"say": REFUSAL, "card": False}
        for r in rows:
            memory.db.execute("UPDATE todos SET text = ? WHERE id = ?", (value, r["id"]))
        memory.db.commit()
        _touched, _TOUCH["at"] = rows[-1]["id"], time.time()
        return {"say": f"Renamed: {value}. It was: {old}. Say undo if that's wrong.", "card": True}
    if value < date.today():
        _undo = None
        return {"say": "That day has already passed. Which day should it be?", "card": False}
    if kind == "move":
        for r in rows:
            memory.db.execute("UPDATE todos SET day = ? WHERE id = ?", (_iso(value), r["id"]))
        memory.db.commit()
        was = _day_words(date.fromisoformat(rows[-1]["day"])) if rows[-1]["day"] else "no day"
        _touched, _TOUCH["at"] = rows[-1]["id"], time.time()
        return {"say": f"Moved {old} to {_day_words(value)}. It was {was}. Say undo if that's wrong.", "card": True}
    for r in rows:                                         # "due"
        memory.db.execute("UPDATE todos SET due = ? WHERE id = ?", (_iso(value), r["id"]))
    memory.db.commit()
    was = f"It was {_due_words(rows[-1]['due'])}" if rows[-1]["due"] else "It had no deadline"
    _touched, _TOUCH["at"] = rows[-1]["id"], time.time()
    return {"say": f"Okay, {old} is now {_due_words(_iso(value))}. {was}. Say undo if that's wrong.",
            "card": True}


def _ask_or(memory, action, hits):
    global _pending
    if len(hits) > 1 and action == "done" and len({r["text"].strip().lower() for r in hits}) == 1:
        # fix 91: two tasks with the same name ("pay rent" today and "pay rent" later): tick the earliest one
        hits = sorted(hits, key=lambda r: (r["day"] or "9999", r["id"]))[:1]
    if len(hits) > 1:
        _pending = {"kind": "pick", "action": action, "rows": hits[:3], "at": time.time()}
        same = len({r["text"].strip().lower() for r in hits[:3]}) == 1     # fix 92: same name -> say the day too
        return {"say": "Which one: " + "; ".join(f"{i + 1}. {r['text']}"
                                                  + (f" ({_day_words(date.fromisoformat(r['day']))})"
                                                     if same and r.get("day") else "")
                                                  for i, r in enumerate(hits[:3]))
                       + "? Say the first, the second, or all.", "card": False}
    return _apply(memory, action, hits)


def _apply(memory, action, rows):
    if isinstance(action, tuple):                           # fix 71: an edit
        return _edit_apply(memory, action, rows)
    names = ", ".join(r["text"] for r in rows)
    if action == "remove":
        for r in rows:
            memory.db.execute("DELETE FROM todos WHERE id = ?", (r["id"],))
        memory.db.commit()
        return {"say": f"Removed from your to-do list: {names}.", "card": True}
    _set_done(memory, rows, action == "done")
    if action == "done":
        left = [r for r in _today_tasks(memory) if not r["done"]]
        tail = " That was the last one for today. Well done!" if not left else f" {len(left)} left."
        return {"say": f"Done: {names}.{tail}", "card": True}
    return {"say": f"Okay, {names} is back on your list.", "card": True}
