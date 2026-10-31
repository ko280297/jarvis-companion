"""Follow-up tracker (fix 113, desk idea #4). Pure code on this device: no AI, no internet. Names never go online.

Both directions, in one list:
  - they owe you:  "I'm waiting for Arjun to send the report" / "Rahul owes me 500 rupees" / "Riya will call me back"
  - you owe them:  "I told Riya I'd send the file by Friday" / "I promised mom I'll call her tomorrow"
Jarvis asks once, on the day it's due (or in 2 days): "Any news from Arjun about the report?"
  yes -> done.  no / not yet -> asks again in 2 days.  "ask me on Monday" -> then.  "forget it" -> removed.
Also:  "what am I waiting for?"   "what did I promise?"   "Arjun sent the report" (ticks it)
Edit options:  "change it to the final report"   "ask me about the Arjun one on Monday"
               "forget the Arjun one"   "stop asking me about follow-ups" / "start asking me about follow-ups again"
Never for a guest or in kid mode (main.py), never during focus or a meeting, only between 9 AM and 9 PM.
"""
import re
import time
from datetime import date, datetime, timedelta

MEMORY = None            # set by main.py (for the quick check in the wake-word loop)
_ready = set()
_pending = None          # {"id", "at"}: Jarvis just asked about this one
_touched = {"id": None, "at": 0.0, "reply": ""}   # "it" = this follow-up, only if my LAST reply was about it
ASK_AFTER_DAYS = 2
HOURS = (9, 21)          # proactive questions only between 9 AM and 9 PM

_DAY = (r"today|tonight|tomorrow|day after tomorrow|(?:this |next )?(?:monday|tuesday|wednesday|thursday|friday"
        r"|saturday|sunday)")
WHEN = re.compile(rf"[,\s]*\b(?:by|before|on|until|till)?\s*(?:the\s+)?({_DAY})\b", re.I)
REL = (r"(?:my\s+)?(?:mom|mum|mother|dad|father|papa|mummy|sister|brother|didi|bhaiya|wife|husband|friend|colleague"
       r"|boss|manager|teacher|landlord|neighbou?r|cousin|uncle|aunt|son|daughter|grandma|grandpa|plumber"
       r"|electrician|doctor)")
# words that are never a person ("I'm waiting for the bus")
NOT_A_PERSON = {"the", "a", "an", "my", "your", "you", "him", "her", "them", "it", "this", "that", "me", "us", "bus",
                "train", "rain", "results", "result", "reply", "response", "delivery", "order", "package", "parcel",
                "call", "money", "payment", "salary", "food", "weekend", "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday", "today", "tomorrow", "something", "someone", "somebody", "nothing",
                "everyone", "god", "jarvis", "what", "who", "how", "when", "why", "news", "summer", "winter", "class",
                "exam", "meeting", "water", "tea", "coffee", "lunch", "dinner", "breakfast", "cab", "taxi", "uber",
                "flight", "doctor's", "update", "updates", "approval", "permission"}
NAME = r"([A-Z][a-z]+(?: [A-Z][a-z]+)?|" + REL + r")"
THEY = [
    re.compile(rf"\bwaiting (?:for|on) {NAME}(?:'s)?\s+(?:to\s+)?(.+)$"),                       # ... for Arjun to send X
    re.compile(rf"\b{NAME}\s+(?:still\s+)?owes? me\s+(.+)$"),                                  # Rahul owes me 500 rupees
    re.compile(rf"\b{NAME}\s+(?:still\s+)?(?:has|have|needs?)\s+to\s+((?:send|give|return|pay|call|reply|share|get back"
               rf"|text|email|tell)\b.*)$"),
    re.compile(rf"\b{NAME}\s+(?:will|is going to|said (?:she|he|they)(?:'d| would| will))\s+((?:send|give|return|pay"
               rf"|call|reply|share|get back|text|email|tell)\b.*\bme\b.*)$"),           # Riya will call me back
]
ME = [
    re.compile(rf"\bI (?:told|promised)\s+{NAME}\s+(?:that\s+)?(?:I'd|I would|I will|I'll|to)\s+(.+)$", re.I),
    re.compile(rf"\bI owe\s+{NAME}\s+(.+)$", re.I),
]
LIST_Q = re.compile(r"\bwhat am i waiting (?:for|on)\b|\bwho owes me\b|\bwho do i need to (?:hear|chase) (?:back )?from\b"
                    r"|\b(?:my|any|show|list|read)\s+(?:my\s+)?follow[- ]?ups?\b|\bwhat did i promise\b"
                    r"|\bwhat do i owe\b|\bwho am i waiting (?:for|on)\b|\bpending follow[- ]?ups?\b", re.I)
OFF = re.compile(r"\b(?:stop|don'?t|do not|no more)\s+(?:asking|ask)(?: me)? about (?:my )?follow[- ]?ups?\b", re.I)
ON = re.compile(r"\b(?:start|resume)\s+asking(?: me)? about (?:my )?follow[- ]?ups?(?: again)?\b", re.I)
YES = re.compile(r"^(?:yes|yeah|yep|yup|haan|ha|han|sure|done|got it|(?:s?he|they|i) did(?: it)?|already|it came"
                 r"|finally)\b", re.I)
NO = re.compile(r"^(?:no|nope|nahi|not yet|not now|still (?:no|waiting|not)|nothing yet|(?:s?he|they|i) didn'?t|not really)\b",
                re.I)
DONE_VERBS = (r"(?:sent|gave|given|returned|paid|replied|called|shared|texted|emailed|told|got back|did|done|answered"
              r"|delivered|finished|submitted)")


# ---------- storage ----------
def _db(memory):
    db = memory.db
    if id(db) not in _ready:
        db.execute("CREATE TABLE IF NOT EXISTS followups (id INTEGER PRIMARY KEY, who TEXT, what TEXT, dir TEXT, "
                   "due TEXT, ask_on TEXT, asked TEXT, done INTEGER DEFAULT 0, created REAL)")
        db.commit()
        _ready.add(id(db))
    return db


def _rows(memory, where="done = 0", args=()):
    return [dict(zip(("id", "who", "what", "dir", "due", "ask_on", "asked", "done"), r)) for r in _db(memory).execute(
        f"SELECT id, who, what, dir, due, ask_on, asked, done FROM followups WHERE {where} ORDER BY id", args)]


def names(memory):
    """Every person in your follow-ups: they stay on this device (never sent in a look-up or the news)."""
    try:
        return {w for r in _rows(memory, "1=1") for w in r["who"].split() if w[:1].isupper()}
    except Exception:
        return set()


def _on(memory):
    return (memory.get_setting("followups") or "on") != "off"


def _day(phrase):
    import todo
    return todo._date_of(phrase)


def _nice_day(iso):
    d = date.fromisoformat(iso)
    diff = (d - date.today()).days
    return "today" if diff == 0 else "tomorrow" if diff == 1 else f"on {d:%A}" if 1 < diff < 7 else f"on {d.day} {d:%B}"


def _touch(fid):
    _touched.update(id=fid, at=time.time())


def _untouch():
    _touched.update(id=None, at=0.0, reply="")


# ---------- understanding ----------
def _who(raw):
    w = raw.strip()
    if w.lower() in NOT_A_PERSON or w.split()[0].lower() in NOT_A_PERSON - {"my"}:
        return None
    if re.fullmatch(REL, w, re.I):
        w = re.sub(r"^my\s+", "your ", w, flags=re.I)         # "my sister" is said back as "your sister"
    return w


def _clean_what(what):
    what = re.sub(r"\s*\b(?:please|ok(?:ay)?|jarvis)\b[,.!]*\s*$", "", what.strip(" .,!?"), flags=re.I)
    due = None
    m = WHEN.search(what)
    if m:
        due = _day(m.group(1))
        what = (what[:m.start()] + what[m.end():]).strip(" .,")
    return what.strip(" .,"), due


def parse(text):
    """'I'm waiting for Arjun to send the report by Friday' -> ('they', 'Arjun', 'send the report', date) or None."""
    t = text.strip(" .!?")
    if re.search(r"\b(?:remind me|don'?t|do not|not|never|isn'?t|wasn'?t|if|whether|should i|can i|what|who|why|how)\b",
                 t, re.I) and not re.match(r"^(?:and |so |okay,? |ok,? )?i(?:'m| am)\s+(?:still\s+)?waiting", t, re.I):
        return None                                          # a question, a negation or a reminder: not a new follow-up
    for kind, pats in (("me", ME), ("they", THEY)):
        for p in pats:
            m = p.search(t)
            if m:
                who = _who(m.group(1))
                what, due = _clean_what(m.group(2))
                if who and what and len(what.split()) <= 12 and not re.fullmatch(r"(?:it|that|this|you)", what, re.I):
                    what = re.sub(r"^(?:me|us)\s+", "", what) if kind == "they" else what
                    return kind, who, what, due
    return None


def _topic(what):
    """'send the report' -> 'the report';  '500 rupees' stays;  'call me back' -> 'calling you back'."""
    w = re.sub(r"\bme\b", "you", what)
    w = re.sub(r"\bmy\b", "your", w)
    if re.fullmatch(r"[a-z]+(?: [a-z]+)?", w) and not re.match(r"^(?:send|give|return|pay|share|text|email|tell|call"
                                                               r"|reply|get|the|a|an|your|his|her|their)\b", w):
        return "the " + w                                      # "Arjun's report" -> "the report"
    m = re.match(r"^(?:send|give|return|pay|share|text|email|tell)\s+(?:you\s+)?(.+)$", w)
    if m:
        return m.group(1)
    m = re.match(r"^(call|reply|get back)(.*)$", w)
    if m:
        return {"call": "calling", "reply": "replying", "get back": "getting back"}[m.group(1)] + m.group(2)
    return w


def _say(r):
    if r["dir"] == "me":
        return f"{r['what']}, for {r['who']}" + (f", due {_nice_day(r['due'])}" if r["due"] else "")
    return f"{r['who']}, {_topic(r['what'])}" + (f", due {_nice_day(r['due'])}" if r["due"] else "")


def question(r):
    if r["dir"] == "me":
        return f"Did you {r['what']}? You told {r['who']} you would."
    return f"Any news from {r['who']} about {_topic(r['what'])}?"


def _add(memory, kind, who, what, due):
    same = [r for r in _rows(memory) if r["who"].lower() == who.lower() and r["what"].lower() == what.lower()]
    if same:
        _touch(same[0]["id"])
        return f"That's already on your follow-ups: {_say(same[0])}."
    ask = due or (date.today() + timedelta(days=ASK_AFTER_DAYS))
    cur = _db(memory).execute("INSERT INTO followups (who, what, dir, due, ask_on, created) VALUES (?, ?, ?, ?, ?, ?)",
                              (who, what, kind, due.isoformat() if due else None, ask.isoformat(), time.time()))
    memory.db.commit()
    _touch(cur.lastrowid)
    when = _nice_day(ask.isoformat())
    if kind == "me":
        return (f"Noted: you'll {what} for {who}" + (f", by {when.replace('on ', '')}" if due else "")
                + f". I'll check with you {when}. Say: change it, or forget it, if that's wrong.")
    return (f"Noted: you're waiting for {who}: {_topic(what)}. I'll ask you {when} if it came. "
            "Say: change it, or forget it, if that's wrong.")


def _match(memory, said, rows=None):
    """Follow-ups whose person is named in what you said (the person decides; the thing narrows it down)."""
    rows = rows if rows is not None else _rows(memory)
    low = said.lower()
    hits = [r for r in rows if re.search(rf"\b{re.escape(re.sub(r'^your ', '', r['who'].lower()))}\b", low)]
    if len(hits) > 1:
        words = set(re.findall(r"[a-z0-9]+", low))
        narrowed = [r for r in hits if words & set(re.findall(r"[a-z0-9]{3,}", r["what"].lower()))]
        hits = narrowed or hits
    return hits


def _finish(memory, rows, how="done"):
    for r in rows:
        if how == "done":
            memory.db.execute("UPDATE followups SET done = 1 WHERE id = ?", (r["id"],))
        else:
            memory.db.execute("DELETE FROM followups WHERE id = ?", (r["id"],))
    memory.db.commit()
    _untouch()                                                   # "it" can't mean a finished one any more
    names_ = "; ".join(_say(r) for r in rows)
    return (f"Great, ticked off: {names_}." if how == "done" else f"Okay, I've removed it: {names_}.")


def _move(memory, r, d):
    memory.db.execute("UPDATE followups SET ask_on = ?, asked = NULL WHERE id = ?", (d.isoformat(), r["id"]))
    memory.db.commit()
    _touch(r["id"])
    return f"Okay, I'll ask you about it {_nice_day(d.isoformat())}."


def _last(memory, seconds=600, last_said=None):
    """The follow-up "it" means: only if my last reply was about it (so "change it" after a to-do is the to-do's)."""
    if last_said is not None and not (_touched["reply"] and _touched["reply"] in last_said):
        return None
    if _touched["id"] and time.time() - _touched["at"] < seconds:
        rows = _rows(memory, "id = ?", (_touched["id"],))
        return rows[0] if rows else None
    return None


# ---------- every turn ----------
def settings(text, memory):
    """Only the on/off edit options (main.py checks these early, before games hear the word "start")."""
    low = (text or "").lower().strip(" .!?")
    if OFF.search(low):
        memory.set_setting("followups", "off")
        return ("Okay, I won't ask about your follow-ups any more. They stay saved: ask, what am I waiting for. "
                "Say: start asking me about follow-ups, to turn it back on.")
    if ON.search(low):
        memory.set_setting("followups", "on")
        return "Okay, I'll ask you about your follow-ups again, once, on the day they're due."
    return None


def handle(text, memory, last_said=None):
    """A reply if this was about follow-ups, else None (everything else carries on as before).
    last_said: Jarvis's last reply (from main.py), so "it" is only a follow-up when we were just talking about one."""
    t0 = time.time()
    out = _handle(text, memory, last_said)
    if out and _touched["id"] and _touched["at"] >= t0:
        _touched["reply"] = out
    return out


def _handle(text, memory, last_said):
    global _pending
    clean = re.sub(r"^(?:ok(?:ay)?|so|and|well|hey|jarvis)[,]?\s+", "", text.strip(), flags=re.I).strip(" .!?")
    low = clean.lower()

    if _pending and time.time() - _pending["at"] < 180:          # the answer to "Any news from Arjun ...?"
        rows = _rows(memory, "id = ?", (_pending["id"],))
        p, _pending = _pending, None
        if rows:
            r = rows[0]
            m = re.search(rf"\b(?:ask|remind)(?: me)? (?:again )?(?:about it )?(?:on\s+)?({_DAY})\b", low)
            if m and _day(m.group(1)):
                return _move(memory, r, _day(m.group(1)))
            if re.match(r"^(?:forget (?:it|that|about it)|remove it|delete it|stop asking(?: about it)?|never ?mind)\b", low):
                return _finish(memory, [r], "remove")
            if YES.match(low):
                return _finish(memory, [r])
            if NO.match(low):
                return _move(memory, r, date.today() + timedelta(days=ASK_AFTER_DAYS)).replace(
                    "Okay, I'll ask", "Okay. I'll ask")
            # anything else: carry on normally (it stays saved, and I'll ask again another day)

    _st = settings(clean, memory)
    if _st:
        return _st

    if LIST_Q.search(low):
        rows = _rows(memory)
        they = [r for r in rows if r["dir"] == "they"]
        me = [r for r in rows if r["dir"] == "me"]
        if re.search(r"\bpromise|\bowe\b(?! me)", low) and not re.search(r"owes me|waiting", low):
            they = []
        if re.search(r"waiting|owes me", low):
            me = []
        if not they and not me:
            return ("You have no open follow-ups. Say, for example: I'm waiting for Arjun to send the report.")
        parts = []
        if they:
            parts.append("You're waiting for: " + "; ".join(_say(r) for r in they) + ".")
        if me:
            parts.append("You promised: " + "; ".join(_say(r) for r in me) + ".")
        return " ".join(parts)

    # "forget the Arjun one" / "remove my follow-up with Arjun" / "mark the Arjun one done"
    m = re.match(r"^(?:please\s+)?(forget|remove|delete|cancel|mark|tick|close)\s+(?:the|my)\s+(.+?)\s+"
                 r"(?:one|follow[- ]?up)(?:\s+(?:as\s+)?done)?$|^(?:please\s+)?(forget|remove|delete|cancel|mark|tick|close)"
                 r"\s+(?:my\s+|the\s+)?follow[- ]?up (?:with|from|for) (.+?)(?:\s+(?:as\s+)?done)?$", low)
    if m:
        verb, who = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        hits = _match(memory, who)
        if not hits:
            return f"I couldn't find a follow-up with {who}."
        return _finish(memory, hits[:1], "done" if verb in ("mark", "tick", "close") else "remove")

    # "ask me about the Arjun one on Monday" / "remind me about it on Monday" (right after adding it)
    m = re.match(rf"^(?:ask|remind) me (?:again )?about (?:it|that|the (.+?) one|my follow[- ]?up with (.+?)) (?:on\s+)?({_DAY})$", low)
    if m:
        r = (_match(memory, m.group(1) or m.group(2)) or [None])[0] if (m.group(1) or m.group(2)) \
            else _last(memory, last_said=last_said)
        d = _day(m.group(3))
        if r and d:
            return _move(memory, r, d)

    # "change it to the final report" right after adding one / "change the Arjun one to the final report"
    m = re.match(r"^(?:change|make) (?:it|that|the (.+?) one) (?:to|into) (.+)$", clean, re.I)
    if m:
        r = (_match(memory, m.group(1)) or [None])[0] if m.group(1) else _last(memory, 120, last_said)
        if r:
            new = m.group(2).strip(" .")
            if r["dir"] == "they" and re.match(r"^(?:the|a|an|my|his|her|their|\d)", new, re.I) \
                    and re.match(r"^(?:send|give|return|share|text|email)\b", r["what"]):
                new = r["what"].split()[0] + " " + new          # "send the report" -> "send the final report"
            memory.db.execute("UPDATE followups SET what = ? WHERE id = ?", (new, r["id"]))
            memory.db.commit()
            _touch(r["id"])
            r["what"] = new
            return f"Changed: {_say(r)}."

    # "Arjun sent the report" / "Riya replied" / "I sent the file to Riya"
    rows = _rows(memory)
    if rows and re.search(rf"\b{DONE_VERBS}\b", low) and not re.search(r"\b(?:not|didn'?t|never|hasn'?t|haven'?t|yet)\b"
                                                                         r"|\?$", low + ("?" if text.strip().endswith("?") else "")):
        hits = _match(memory, low, rows)
        mine = re.match(r"^i\b", low)
        hits = [r for r in hits if (r["dir"] == "me") == bool(mine)]
        if hits:
            words = set(re.findall(r"[a-z0-9]{3,}", low)) - {"sent", "gave", "paid", "replied", "called", "the", "and",
                                                               "back", "finally", "already", "today", "just"}
            sure = [r for r in hits if words & set(re.findall(r"[a-z0-9]{3,}", r["what"].lower()))]
            short = len(low.split()) <= 5 and re.search(r"\b(?:it|back|me|already|finally|replied|paid|called)\b|"
                                                        rf"\b{DONE_VERBS}$", low)
            if sure or short:
                return _finish(memory, (sure or hits)[:1])

    got = parse(clean)
    if got:
        return _add(memory, *got)
    return None


def waiting(now=None):
    """Quick check for the wake-word loop (no side effects): is a follow-up question due now?"""
    memory = MEMORY
    now = now or datetime.now()
    if memory is None or not _on(memory) or not (HOURS[0] <= now.hour < HOURS[1]):
        return False
    try:
        today = now.date().isoformat()
        return any(r["ask_on"] and r["ask_on"] <= today and r["asked"] != today for r in _rows(memory))
    except Exception:
        return False


def due(memory, now=None):
    """The one follow-up to ask about now, or None. Each one is asked at most once a day."""
    global _pending
    now = now or datetime.now()
    if not _on(memory) or not (HOURS[0] <= now.hour < HOURS[1]):
        return None
    today = now.date().isoformat()
    rows = [r for r in _rows(memory) if r["ask_on"] and r["ask_on"] <= today and r["asked"] != today]
    if not rows:
        return None
    r = rows[0]
    memory.db.execute("UPDATE followups SET asked = ? WHERE id = ?", (today, r["id"]))
    memory.db.commit()
    _pending = {"id": r["id"], "at": time.time()}
    _touch(r["id"])
    q = question(r) + " Say yes, not yet, or: ask me on Monday."
    _touched["reply"] = q
    return q
