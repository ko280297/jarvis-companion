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
_LIST = r"(?:my\s+|the\s+)?([a-z]+(?:\s[a-z]+){0,2}?)(?:\s+list)?"
FILLER = re.compile(r"^(?:(?:oh|ok|okay|so|and|please|can you|could you|i want you to)[,]?\s+)+", re.I)
ADD = re.compile(rf"^(?:add|put|at)\s+(.+)\s+(?:to|in|into|on)\s+{_LIST}$")
REMOVE = re.compile(rf"^(?:remove|delete)\s+(.+)\s+from\s+{_LIST}$")
CLEAR = re.compile(rf"^(?:clear|empty)\s+{_LIST}$")
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
        "description": "Remove an item from one of the user's lists.",
        "parameters": {"type": "object", "properties": {
            "list_name": {"type": "string", "description": "List name only, e.g. grocery."},
            "item": {"type": "string", "description": "The thing to remove."},
        }, "required": ["item"]},
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
    known = set(memory.list_names()) | set(STARTER_LISTS)
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


def _spoken(items):
    return "; ".join(re.sub(r"\s*\[date: (.*?)\]", r", on \1", i) for i in items)


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
    return f"Your {name} list has {len(items)} item{'s' if len(items) != 1 else ''}: {_spoken(items)}."


def _all_lists(memory):
    names = memory.list_names()
    if not names:
        return "You don't have any lists yet."
    parts = [f"{n}, with {len(memory.list_get(n))}" for n in names]
    return f"You have {len(names)} list{'s' if len(names) != 1 else ''}: " + "; ".join(parts) + "."


def _remove(memory, name, item):
    if memory.list_remove(name, item):
        return f"Removed it from your {name} list."
    return f"I couldn't find that on your {name} list."


# ---------- Fast path ----------
def handle_list_command(text, memory):
    """Exact commands. Returns a spoken reply, or None if this isn't one."""
    clean = FILLER.sub("", text.strip(" .!?,"))
    lower = clean.lower()

    m = CAPTURE.match(lower)
    if m:
        item = clean[m.start(1):m.end(1)]
        return _add(memory, _classify(item, memory), item).replace("Added to", "Captured to")

    m = ADD.match(lower)
    if m:
        name = match_list(m.group(2), memory) or _classify(clean[m.start(1):m.end(1)], memory)
        return _add(memory, name, clean[m.start(1):m.end(1)])

    m = REMOVE.match(lower)
    if m:
        return _remove(memory, match_list(m.group(2), memory), m.group(1))

    m = CLEAR.match(lower)
    if m:
        name = match_list(m.group(1), memory)
        if name in memory.list_names():
            memory.list_clear(name)
            return f"Your {name} list is now empty."
        return None

    m = SHOW.match(lower)
    if m:
        name = match_list(m.group(1), memory)
        if name in memory.list_names() or name in STARTER_LISTS:
            return _read(memory, name)
        return None      # not a list at all, e.g. "tell me the news"

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
    if name == "show_all_lists":
        return _all_lists(memory)
    return None