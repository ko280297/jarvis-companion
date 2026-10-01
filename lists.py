"""Named lists (grocery, ideas, schedule, or any list the user names) + universal capture.
Two ways in: fast exact commands (regex), and an LLM tool router for natural phrasing.
Either way, the CODE does the actual work and checks every argument."""
import re
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


def _add(memory, name, item):
    global _last_list
    if is_unsafe(item):
        return REFUSAL
    if name == "schedule":
        item = resolve_dates(item)      # Python works out the real date
    memory.list_add(name, item)
    _last_list = name
    return f"Added to your {name} list."


def _read(memory, name):
    global _last_list
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


# ---------- Fast path ----------
def _one_command(sentence, memory):
    clean = FILLER.sub("", sentence.strip(" .!?,"))
    lower = clean.lower()

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
        if re.match(r"(what|who|how|where|when|why)\b", item.lower()):
            return None      # it's a question, not an item
        return _add(memory, name or _classify(item, memory), item)

    m = REMOVE.match(lower)
    if m:
        return _remove(memory, match_list(m.group(2), memory), m.group(1))

    m = CLEAR.match(lower)
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