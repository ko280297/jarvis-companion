"""Named lists (grocery, ideas, schedule, or any list the user names) + universal capture.
Two ways in: fast exact commands (regex), and an LLM tool router for natural phrasing.
Either way, the CODE does the actual work and checks every argument."""
import re
import time   # fix 72
from difflib import SequenceMatcher

import numpy as np
from memory import _embed
from tools import resolve_dates
from safety import is_unsafe, REFUSAL

# Starter sections, with a short description so "capture" can guess where things go.
# The user can create any other list just by naming it ("add X to my packing list").
STARTER_LISTS = {
    "grocery": "groceries, food, and things to buy from the shop",
    "idea": "ideas, thoughts, plans to try, and inspiration",
    "schedule": "meetings, appointments, and events on a day or date",
}
CAPTURE_THRESHOLD = 0.30    # below this, captured items go to the inbox
MAX_ITEM_LENGTH = 200

_last_list = ""             # so "add it to that list" works

# ---------- Fast path: exact commands ----------
_LIST = r"(?:my\s+|the\s+)?([a-z]+(?:\s[a-z]+){0,2}?)(?:\s+list)?(?:\s+(?:too|also|as well|please))?"
FILLER = re.compile(
    r"^(?:(?:oh|ok|okay|so|or|yes|yeah|really|just|please|can you|could you"
    r"|i want you to|i'm saying to you)[,]?\s+)+", re.I)
# "and" / "at" are how speech-to-text often mishears "add"
ADD = re.compile(rf"^((?:(?:add|put|and|at)[,]?\s+)+)(.+)\s+(?:to|in|into|on)\s+{_LIST}$")
REMOVE = re.compile(rf"^(?:remove|delete)\s+(.+)\s+from\s+{_LIST}$")
CLEAR = re.compile(rf"^(?:clear|empty|delete)\s+{_LIST}$")
SHOW = re.compile(
    rf"^(?:what(?:'s| is)\s+(?:on|in)|what do (?:i|we) have\s+(?:on|in)|read|read me|show me|tell me)\s+{_LIST}$")
CAPTURE = re.compile(r"^(?:capture|note down|jot down)[:,]?\s+(?:that\s+|and\s+)?(.+)$")

# ---------- Smart path: tools the LLM router can choose ----------
LIST_TOOLS = [
    {"type": "function", "function": {
        "name": "add_to_list",
        "description": "Add an item to one of the user's lists, such as grocery, idea, schedule, "
                       "or any list they name. If no list is named, leave list_name empty.",
        "parameters": {"type": "object", "properties": {
            "list_name": {"type": "string", "description": "List name only, e.g. grocery. Empty if not mentioned."},
            "item": {"type": "string", "description": "The thing to add, in the user's own words."},
        }, "required": ["item"]},
    }},
    {"type": "function", "function": {
        "name": "read_list",
        "description": "Read out what is on one of the user's lists.",
        "parameters": {"type": "object", "properties": {
            "list_name": {"type": "string", "description": "List name only, e.g. grocery."},
        }},
    }},
    {"type": "function", "function": {
        "name": "remove_from_list",
        "description": "Remove one item from one of the user's lists.",
        "parameters": {"type": "object", "properties": {
            "list_name": {"type": "string", "description": "List name only, e.g. grocery."},
            "item": {"type": "string", "description": "The thing to remove."},
        }, "required": ["item"]},
    }},
    {"type": "function", "function": {
        "name": "clear_list",
        "description": "Clear or delete a whole list, removing everything on it.",
        "parameters": {"type": "object", "properties": {
            "list_name": {"type": "string", "description": "List name only, e.g. grocery."},
        }, "required": ["list_name"]},
    }},
    {"type": "function", "function": {
        "name": "show_all_lists",
        "description": "Say which lists the user has and how many items are on each.",
        "parameters": {"type": "object", "properties": {}},
    }},
]
LIST_TOOL_NAMES = {t["function"]["name"] for t in LIST_TOOLS}


# ---------- Helpers ----------
def normalize(name):
    """'groceries' -> 'grocery', 'ideas' -> 'idea'."""
    name = name.strip()
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _known_lists(memory):
    return set(memory.list_names()) | set(STARTER_LISTS)


def match_list(name, memory):
    """Map what the user said to a real list:
    'good grocery' -> 'grocery', 'groceries' -> 'grocery', 'that list' -> the last list used.
    If nothing is close, it's a new list name."""
    name = re.sub(r"\b(my|the|list|lists)\b", " ", name.lower())
    name = normalize(" ".join(name.split()))
    if name in ("that", "this", "same", "it", "that one"):
        return _last_list
    if not name:
        return ""
    known = _known_lists(memory)
    for k in known:
        if k in name.split():               # "good grocery" contains "grocery"
            return k
    best = max(known, key=lambda k: SequenceMatcher(None, name, k).ratio())
    return best if SequenceMatcher(None, name, best).ratio() >= 0.6 else name


def _classify(item, memory):
    """Pick the list whose meaning is closest to the item (on-device embeddings)."""
    lists = dict(STARTER_LISTS)
    for name in memory.list_names():
        lists.setdefault(name, name)
    vec = _embed(item)
    scores = {name: float(np.dot(vec, _embed(desc))) for name, desc in lists.items()}
    best = max(scores, key=scores.get)
    print("   capture scores: " + ", ".join(f"{n} {s:.2f}" for n, s in scores.items()))
    return best if scores[best] >= CAPTURE_THRESHOLD else "inbox"


def _spoken_item(item):
    """'meeting on Friday [date: Friday, 2 October 2026]' -> 'meeting on Friday, 2 October 2026'."""
    m = re.search(r"\s*\[date: (\w+), (.*?)\]", item)
    if not m:
        return item
    base, weekday, rest = item[:m.start()], m.group(1), m.group(2)
    if weekday.lower() in base.lower():
        return f"{base}, {rest}"
    return f"{base}, on {weekday}, {rest}"


_NUMW = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a dozen": 12, "dozen": 12,
         "half a dozen": 6}
_QTY = re.compile(r"^(\d+|half a dozen|a couple of|a dozen|dozen|one|two|three|four|five|six|seven|eight"
                  r"|nine|ten|eleven|twelve)\s+(?:more\s+)?(.+)$", re.I)


def _split_qty(item):
    """'Two Apples' -> (2, 'Apples'); 'milk' -> (None, 'milk')."""
    item = item.strip()
    m = _QTY.match(item)
    if m:
        g = m.group(1).lower()
        return (int(g) if g.isdigit() else _NUMW[g]), m.group(2)
    return None, re.sub(r"^(?:a|an|the|some)\s+", "", item, flags=re.I)


def _key(thing):
    """'Apples' / 'apple', 'potatoes' / 'potato' -> the same key."""
    w = " ".join(thing.lower().split())
    for suffix, repl in (("oes", "o"), ("ies", "y"), ("s", "")):
        if w.endswith(suffix) and len(w) > len(suffix) + 1:
            return w[:-len(suffix)] + repl
    return w


def _drop(memory, name, entry):
    if hasattr(memory, "list_remove_exact"):
        return memory.list_remove_exact(name, entry)
    return memory.list_remove(name, entry)


def _add(memory, name, item):
    """Add without repeats: 'Milk' is the same as 'milk', and '2 apples' + 'Two Apples' = '4 apples'."""
    global _last_list
    if is_unsafe(item):
        return REFUSAL
    _last_list = name
    if name in ("task", "to do", "todo", "to-do", "to dos", "todos"):   # fix 52: to-do list
        import todo
        return todo.add_task(memory, item)
    if name == "schedule":
        item = resolve_dates(item)      # Python works out the real date
        memory.list_add(name, item)
        return f"Added to your {name} list."
    parts = [p.strip(" .") for p in re.split(r",|\band\b|&", item) if p.strip(" .")] or [item]
    if any(len(p.split()) > 4 for p in parts):
        parts = [item]                  # a longer thought ("call mom and ask about dinner") stays one item
    added, more, already = [], [], []
    for part in parts:
        qty, thing = _split_qty(part)
        hits = [e for e in memory.list_get(name) if _key(_split_qty(e)[1]) == _key(thing)]
        if hits and qty:
            total = sum(_split_qty(e)[0] or 0 for e in hits) + qty
            for e in hits:
                _drop(memory, name, e)
            memory.list_add(name, f"{total} {thing.lower()}")
            more.append(f"{total} {thing.lower()}")
        elif hits:
            already.append(thing)
        else:
            memory.list_add(name, f"{qty} {thing}" if qty else thing)
            added.append(f"{qty} {thing}" if qty else thing)          # fix 104: "Added 2 kg of rice", not "kg of rice"
    said = []
    if added:
        said.append(f"Added {', '.join(added)} to your {name} list.")      # fix 99: always say WHAT was added
    if more:
        said.append(f"You now have {', '.join(more)} on your {name} list.")
    if already:
        what = ", ".join(already)
        said.append(f"{what[:1].upper() + what[1:]} {'is' if len(already) == 1 else 'are'} already on your {name} list.")
    return " ".join(said)


def _read(memory, name):
    global _last_list
    if name in ("task", "to do", "todo", "to-do", "to dos", "todos"):   # fix 52: read the to-do list
        import todo
        return todo.handle_todo("what's on my to-do list", memory, todo.REMINDERS)["say"]
    if name not in memory.list_names():
        return f"Your {name} list is empty."
    _last_list = name
    items = memory.list_get(name)
    spoken = "; ".join(_spoken_item(i) for i in items)
    return f"Your {name} list has {len(items)} item{'s' if len(items) != 1 else ''}: {spoken}."


def _all_lists(memory):
    names = memory.list_names()
    if not names:
        return "You don't have any lists yet."
    parts = []
    for n in names:
        count = len(memory.list_get(n))
        parts.append(f"{n}, with {count} item{'s' if count != 1 else ''}")
    return f"You have {len(names)} list{'s' if len(names) != 1 else ''}: " + "; ".join(parts) + "."


def _remove(memory, name, item):
    if memory.list_remove(name, item):
        return f"Removed it from your {name} list."
    return f"I couldn't find that on your {name} list."


def _clear(memory, name):
    if name in memory.list_names():
        memory.list_clear(name)
        return f"Done, your {name} list is cleared."
    return f"You don't have a {name} list." if name else "Which list should I clear?"


# ----- fix 72: change an item on a list, move a schedule item to another time or day, "undo" -----
_undo = None             # {"t", "rows": [(id, list, old item)]}
_pick = None             # {"t", "rows": [(id, list, item)], "kind", "new"}
_TODO_NAMES = {"task", "tasks", "to do", "todo", "to-do", "to dos", "todos"}
EDIT_ITEM = re.compile(rf"^(?:change|replace|edit|update|correct|swap)\s+(?:the\s+)?(.+?)\s+(?:to|with|into|for)\s+(.+?)"
                       rf"(?:\s+(?:on|in)\s+{_LIST})?$")
MOVE_EVENT = re.compile(r"^(?:move|shift|change|reschedule|push|postpone|prepone|make)\s+(?:the\s+|my\s+)?(.+?)\s+"
                        r"(?:to|till|until|for)\s+(.+)$")
UNDO_EDIT = re.compile(r"^(?:no,? )?(?:undo(?: (?:it|that))?|change it back|put it back)$")
TIME_RE = re.compile(r"\b(?:at\s+)?\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)(?=\W|$)|\bat\s+\d{1,2}(?:[:.]\d{2})?\b",
                     re.I)
_STOPW = {"the", "my", "list", "item", "and", "for", "with", "from", "this", "that", "please", "instead"}


def _wordset(s):
    return {w[:-1] if w.endswith("s") and len(w) > 4 else w
            for w in re.findall(r"[a-z]+", re.sub(r"\[date:.*?\]", "", s.lower())) if len(w) > 2} - _STOPW


def _items(memory, name=None):
    q = "SELECT id, list, item FROM list_items" + (" WHERE list = ?" if name else "") + " ORDER BY id"
    return [r for r in memory.db.execute(q, (name,) if name else ()).fetchall() if r[1] not in _TODO_NAMES]


def _find_items(memory, said, name=None, only=None):
    """Items the user means: same thing ('milk' = 'Milk', '2 apples' = 'apples'), or all the words said."""
    words = _wordset(said)
    if not words:
        return []
    rows = _items(memory, name)
    if only:
        rows = [r for r in rows if r[1] == only]
    same = [r for r in rows if _key(_split_qty(re.sub(r"\s*\[date:.*?\]", "", r[2]))[1]) == _key(_split_qty(said)[1])]
    return same or [r for r in rows if words <= _wordset(r[2])]


def _moved_text(item, new):
    """'dentist on Friday at 5 pm [date: ...]' + 'Saturday' -> 'dentist on Saturday at 5 pm [date: ...]'."""
    from todo import _DAYWORD
    base = re.sub(r"\s*\[date:.*?\]", "", item)
    tag = re.search(r"\[date: \w+, (\d{1,2} \w+ \d{4})\]", item)
    new_day = re.search(rf"\b({_DAYWORD})\b", new, re.I)
    new_time = TIME_RE.search(new)
    if not new_day and not new_time:
        return None
    rest = re.sub(rf"\s*\b(?:on\s+)?(?:the\s+)?(?:{_DAYWORD})\b(?:,?\s+\d{{4}})?", "", base, flags=re.I)
    old_time = TIME_RE.search(rest)
    rest = " ".join(TIME_RE.sub("", rest).split()).strip(" ,.")
    rest = re.sub(r"\s+(?:at|on)$", "", rest)
    if new_day:
        d = new_day.group(1)
        day = d if re.match(r"(?:today|tonight|tomorrow|day after)", d, re.I) else f"on {d[:1].upper() + d[1:]}"
    elif tag:                                             # same day: say it the way people do
        from datetime import date as _d, datetime as _dt
        old_d = _dt.strptime(tag.group(1), "%d %B %Y").date()
        day = f"on {old_d:%A}" if 0 <= (old_d - _d.today()).days < 7 else f"on {old_d.day} {old_d:%B}"
    else:
        day = ""
    t = new_time.group(0) if new_time else (old_time.group(0) if old_time else "")
    if t and not t.lower().startswith("at"):
        t = "at " + t
    return resolve_dates(" ".join(x for x in (rest, day, t) if x))


def _edit_done(memory, rows, kind, new):
    global _undo, _pick
    _pick = None
    _undo = {"t": time.time(), "rows": [(r[0], r[1], r[2]) for r in rows]}
    for mod, attr in (("memfix", "LAST"), ("todo", "_undo")):   # "undo" now means this change
        try:
            setattr(__import__(mod), attr, None)
        except ImportError:
            pass
    rid, name, old = rows[0]
    if kind == "move":
        new_item = _moved_text(old, new)
        memory.db.execute("UPDATE list_items SET item = ? WHERE id = ?", (new_item, rid))
        memory.db.commit()
        return (f"Moved: {_spoken_item(new_item)}. It was {_spoken_item(old)}. Say undo if that's wrong.")
    if is_unsafe(new):
        _undo = None
        return REFUSAL
    new_item = resolve_dates(new) if name == "schedule" else new
    memory.db.execute("UPDATE list_items SET item = ? WHERE id = ?", (new_item, rid))
    memory.db.commit()
    return f"Done: {_spoken_item(new_item)} instead of {_spoken_item(old)}, on your {name} list. Say undo if that's wrong."


def _ask(rows, kind, new):
    global _pick
    _pick = {"t": time.time(), "rows": rows[:3], "kind": kind, "new": new}
    return ("Which one: " + "; ".join(f"{i + 1}. {_spoken_item(r[2])} ({r[1]} list)" for i, r in enumerate(rows[:3]))
            + "? Say the first, the second, or the third.")


def edit_command(clean, lower, memory):
    """A reply if this changes a list item or moves a schedule item, else None (other features get it)."""
    global _undo, _pick
    if UNDO_EDIT.match(lower):
        if not _undo or time.time() - _undo["t"] > 120:
            return None
        u, _undo = _undo, None
        for rid, name, item in u["rows"]:
            memory.db.execute("UPDATE list_items SET item = ? WHERE id = ?", (item, rid))
        memory.db.commit()
        return f"Okay, I've put it back: {_spoken_item(u['rows'][0][2])}."
    if _pick and time.time() - _pick["t"] < 120:
        m = re.search(r"\b(first|1st|one|second|2nd|two|third|3rd|three)\b", lower)
        if m and len(lower.split()) <= 5:
            i = {"first": 0, "1st": 0, "one": 0, "second": 1, "2nd": 1, "two": 1}.get(m.group(1), 2)
            p = _pick
            if i < len(p["rows"]):
                return _edit_done(memory, [p["rows"][i]], p["kind"], p["new"])
    if re.search(r"\b(?:reminders?|alarms?|timers?|voice|volume|city|name|pin|birthday|password)\b", lower):
        return None
    m = MOVE_EVENT.match(lower)
    if m and _moved_text("x", m.group(2)):
        hits = _find_items(memory, m.group(1), only="schedule")
        if hits:
            return _edit_done(memory, hits, "move", m.group(2)) if len(hits) == 1 else _ask(hits, "move", m.group(2))
    m = EDIT_ITEM.match(lower)
    if m:
        name = match_list(m.group(3), memory) if m.group(3) else None
        new = clean[m.start(2):m.end(2)].strip(" .,")
        hits = _find_items(memory, m.group(1), name if name in memory.list_names() else None)
        if hits and new:
            kind = "move" if hits[0][1] == "schedule" and _moved_text("x", new) and len(new.split()) <= 4 else "rename"
            return _edit_done(memory, hits, kind, new) if len(hits) == 1 else _ask(hits, kind, new)
    return None


# ---------- Fast path ----------
def _one_command(sentence, memory):
    clean = FILLER.sub("", sentence.strip(" .!?,"))
    lower = clean.lower()

    _ed = edit_command(clean, lower, memory)          # fix 72
    if _ed:
        return _ed

    m = CAPTURE.match(lower)
    if m:
        item = clean[m.start(1):m.end(1)]
        return _add(memory, _classify(item, memory), item).replace("Added to", "Captured to")

    m = ADD.match(lower)
    if m:
        item = clean[m.start(2):m.end(2)]
        name = match_list(m.group(3), memory)
        weak_verb = not re.search(r"\b(add|put)\b", m.group(1))     # only "and"/"at"
        if weak_verb and name not in _known_lists(memory):
            return None      # "and ... in my list" might not be an add at all
        if name and name not in _known_lists(memory) and not re.search(r"\blists?\b", lower):
            return None      # fix 104: "add 2 kg of rice to the rice" is not a new "rice" list: main.py asks
        if re.match(r"(what|who|how|where|when|why)\b", item.lower()):
            return None      # it's a question, not an item
        return _add(memory, name or _classify(item, memory), item)

    m = REMOVE.match(lower)
    if m:
        return _remove(memory, match_list(m.group(2), memory), m.group(1))

    m = CLEAR.match(lower)
    if m and re.fullmatch(r"(?:that|this|it|that one|same)", m.group(1).strip()) and "list" not in lower:
        m = None         # "delete that" is about the last thing said, not a whole list
    if m:
        name = match_list(m.group(1), memory)
        return _clear(memory, name) if name in memory.list_names() else None

    m = SHOW.match(lower)
    if m:
        name = match_list(m.group(1), memory)
        if name in _known_lists(memory):
            return _read(memory, name)
        return None      # not a list at all, e.g. "tell me the news"

    return None


def handle_list_command(text, memory):
    """Exact commands, checked sentence by sentence. Returns a spoken reply, or None."""
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        reply = _one_command(sentence, memory)
        if reply:
            return reply
    return None


# ---------- Smart path ----------
def run_list_tool(name, args, memory):
    """The LLM chose a list tool. Check every argument, then do the work ourselves."""
    list_name = match_list(str(args.get("list_name") or ""), memory)
    item = str(args.get("item") or "").strip()
    if len(item) > MAX_ITEM_LENGTH:
        return "That's a bit long for a list item. Could you say it shorter?"

    if name == "add_to_list" and re.search(r"\blists?\b", item, re.I):     # fix 88: a list's name, not an item
        return "That sounds like the name of a list, not something to add. What should I add, and to which list?"
    if name == "add_to_list":
        if not item:
            return "What should I add?"
        return _add(memory, list_name or _classify(item, memory), item)
    if name == "read_list":
        return _read(memory, list_name) if list_name else _all_lists(memory)
    if name == "remove_from_list":
        if not item or not list_name:
            return "Which item, and from which list?"
        return _remove(memory, list_name, item)
    if name == "clear_list":
        return _clear(memory, list_name)
    if name == "show_all_lists":
        return _all_lists(memory)
    return None