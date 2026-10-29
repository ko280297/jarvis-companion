"""Correct, update or forget one saved fact (fix 70). No AI: plain matching on "my <thing> is <value>".

  "Actually my birthday is 5 May"      -> Updated: your birthday is 5 May (it was 4 May).
  "Change my blood group to O positive" -> Updated ...
  "Remember my birthday is 5 May"      -> the old birthday is replaced, never kept next to the new one
  "Forget my car number"               -> only that fact is deleted
  "Undo" / "keep the old one"          -> puts back what was there before (for 2 minutes)
"""
import re
import time
from datetime import date

import memory as _mem

# Things that are not facts in memory: they have their own features
NOT_FACTS = re.compile(r"^my (?:name|city|pin|password|voice|volume)\b"
                       r"|^my\b(?:.*\s)?(?:reminders?|alarms?|timers?|lists?|grocery|shopping|schedule|to-?do|tasks?"
                       r"|meetings?|appointments?|events?|plans?|day|period|cycle|mood|focus|notes?)\b")
                       # also "my medicine reminder", "my 5 pm meeting"; but "my sister's name" is a fact
# "my mom is ..." can be many different facts, so these are never replaced automatically
PEOPLE = {"mom", "mother", "dad", "father", "sister", "brother", "friend", "boss", "wife", "husband", "son",
          "daughter", "grandma", "grandpa", "grandmother", "grandfather", "uncle", "aunt", "cousin", "colleague",
          "teacher", "neighbour", "neighbor", "partner", "girlfriend", "boyfriend", "baby", "kids", "children", "family",
          "parents", "friends", "team", "manager", "doctor"}
SUBJ = re.compile(r"^(my (?:[a-z0-9'-]+ ){0,3}?[a-z0-9'-]+?) (is|are|was|were) (.+)$")

LAST = None          # {"t", "new_id", "old": [(id, text, created, vec)], "what"} for undo
NOTE = ""            # said after the normal "I'll remember that" when an old fact was replaced


def _core(text):
    return re.sub(r"\s*\[date:.*?\]", "", str(text).split(" (saved on")[0]).strip(" .")


def subject_of(text):
    """'My birthday is 4 May (saved on ...)' -> 'my birthday'. None if it isn't a simple, single-valued fact."""
    core = _core(text).lower()
    core = re.sub(r"^(?:and |also |now |so )", "", core)
    m = SUBJ.match(core)
    if not m or NOT_FACTS.match(m.group(1)) or len(m.group(3).split()) > 10:
        return None
    subj = m.group(1)
    last = subj.split()[-1]
    if last in PEOPLE or last.rstrip("s") in PEOPLE:
        return None
    return subj


def value_of(text):
    m = SUBJ.match(_core(text).lower())
    return _core(text)[m.start(3):] if m else _core(text)


def yours(text):
    """'my birthday is 5 May' -> 'your birthday is 5 May'"""
    t = _core(text)
    t = re.sub(r"\bmy\b", "your", t, flags=re.I)
    t = re.sub(r"\bI am\b", "you are", t)
    t = re.sub(r"\bI'm\b", "you're", t)
    return re.sub(r"\bmine\b", "yours", t, flags=re.I)


def same_subject(memory, subj):
    return [r for r in memory.db.execute("SELECT id, text, created, vec FROM memories ORDER BY id").fetchall()
            if subject_of(r[1]) == subj]


def replace(memory, rows, new_text):
    """The newest old row becomes the new fact; any other old copies go. Returns the old value said."""
    global LAST
    vec = _mem._embed(_core(new_text))
    keep = rows[-1]
    memory.db.execute("UPDATE memories SET text = ?, created = ?, vec = ? WHERE id = ?",
                      (new_text, time.time(), vec.tobytes(), keep[0]))
    for r in rows[:-1]:
        memory.db.execute("DELETE FROM memories WHERE id = ?", (r[0],))
    memory.db.commit()
    LAST = {"t": time.time(), "new_id": keep[0], "old": rows, "what": "update"}
    return value_of(keep[1])


def _stamp(fact):
    return f"{fact} (saved on {date.today():%A, %d %B %Y})"


UNDO = re.compile(r"^(?:no,? )?(?:undo(?: (?:it|that))?|keep the old(?: one)?|change it back|put it back|"
                  r"go back to the old(?: one)?|bring it back)$")
CORRECT = re.compile(r"^(?:(?:actually|no|nope|correction|sorry|wait|oh)[,:!]?\s+)+(my .+? (?:is|are|was|were) .+)$")
CHANGE = re.compile(r"^(?:please )?(?:change|update|correct|fix|set) (my .+?) to (.+)$")
FORGET = re.compile(r"^(?:please )?(?:forget|delete|remove|erase) (my .+?)(?: from (?:your |my )?memory)?$")


def handle(text, memory, is_unsafe=lambda t: False):
    """A spoken reply for a correction / undo / forget-one-fact, or None if this isn't one."""
    global LAST
    raw = text.strip().rstrip(".!? ")
    low = re.sub(r"^(?:hey |ok |okay )?jarvis,? ", "", raw.lower())
    low = low.replace("’", "'")

    if UNDO.match(low):
        if not LAST or time.time() - LAST["t"] > 120:
            return None                                     # not about memory: other features may own "undo"
        last, LAST = LAST, None
        if last["new_id"] is not None:
            memory.db.execute("DELETE FROM memories WHERE id = ?", (last["new_id"],))
        for r in last["old"]:
            memory.db.execute("INSERT INTO memories (id, text, created, vec) VALUES (?, ?, ?, ?)", r)
        memory.db.commit()
        if last["old"]:
            return f"Okay, I've put it back: {yours(last['old'][-1][1])}."
        return "Okay, I've taken that out again."

    m = CHANGE.match(low)
    if m:
        subj, value = m.group(1), raw[m.start(2):]
        fact = f"{raw[m.start(1):m.end(1)]} is {value}"
    else:
        m = CORRECT.match(low)
        fact = raw[m.start(1):] if m else None
    if fact:
        subj = subject_of(fact)
        if not subj:
            return None
        if is_unsafe(fact):
            return "I can't save that."
        fact = fact[0].upper() + fact[1:]
        from tools import resolve_dates
        fact = resolve_dates(fact)   # fix 71: '5 May' also gets its real date, like a normal save
        rows = same_subject(memory, subj)
        if rows:
            if _core(rows[-1][1]).lower() == _core(fact).lower() and len(rows) == 1:
                return f"That's what I have already: {yours(fact)}."
            old = replace(memory, rows, _stamp(fact))
            return f"Updated: {yours(fact)}. It was {old}. If that's wrong, just say undo."
        memory.add(_stamp(fact))
        row = memory.db.execute("SELECT id FROM memories ORDER BY id DESC LIMIT 1").fetchone()
        LAST = {"t": time.time(), "new_id": row[0] if row else None, "old": [], "what": "add"}
        return f"Okay, I'll remember that {yours(fact)}."

    m = FORGET.match(low)
    if m and not NOT_FACTS.match(m.group(1)) and m.group(1) not in ("my memory", "my memories", "my data"):
        what = m.group(1)
        words = [w for w in what.split()[1:] if len(w) > 2 or w.isdigit()]
        if not words:
            return None
        rows = [r for r in memory.db.execute("SELECT id, text, created, vec FROM memories ORDER BY id").fetchall()
                if _core(r[1]).lower().startswith(what) or subject_of(r[1]) == what]
        if not rows:
            return (f"I don't have anything saved about {yours(what)}." if low.startswith("forget") else None)
        for r in rows:
            memory.db.execute("DELETE FROM memories WHERE id = ?", (r[0],))
        memory.db.commit()
        LAST = {"t": time.time(), "new_id": None, "old": rows, "what": "forget"}
        gone = yours(rows[-1][1])
        more = f" (and {len(rows) - 1} older note{'s' if len(rows) > 2 else ''} about it)" if len(rows) > 1 else ""
        return f"Done, I've forgotten that {gone}{more}. Say undo if you didn't mean that."
    return None


def on_add(memory, text):
    """Called before any save. If the same 'my X is ...' fact exists, it's replaced instead. True = handled."""
    global NOTE
    subj = subject_of(text)
    if not subj:
        return False
    rows = same_subject(memory, subj)
    if not rows:
        return False
    old = replace(memory, rows, text)
    NOTE = f"I've replaced the old one: it was {old}. Say undo to keep the old one."
    return True
