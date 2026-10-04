"""Meeting mode (lite): listens during a meeting for dates and tasks, then asks before saving anything.
The transcript is never saved: each piece is checked for dates and tasks, then dropped.
No LLM, no internet: everything happens on this device."""
import re
import time

from lists import STARTER_LISTS
from safety import is_unsafe
from tools import resolve_dates

SCHEDULE_LIST = next((n for n in STARTER_LISTS if n.startswith("sched")), "schedule")
TASKS_LIST = "tasks"

START = re.compile(r"\bmeeting mode (?:on|start)\b|\b(?:start|turn on|begin|switch on) (?:the )?meeting mode\b")
STOP = re.compile(r"\bmeeting mode (?:off|stop|end)\b|\b(?:stop|end|turn off|switch off|close) (?:the )?meeting mode\b"
                  r"|\bthe meeting is (?:over|done|finished)\b")
YES = re.compile(r"\b(?:yes|yeah|yep|sure|ok|okay|please|add it|save it|go ahead)\b")
NO = re.compile(r"\b(?:no|nope|skip|don'?t|not needed|leave it)\b")
END_REVIEW = re.compile(r"\b(?:stop|that'?s all|cancel|forget (?:it|them|all|the rest))\b")

MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
WHEN = re.compile(
    rf"\b(?:today|tomorrow|tonight|day after tomorrow|next week|this week"
    rf"|(?:next |this |on )?(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?(?: of)? (?:{MONTHS})|(?:{MONTHS}) \d{{1,2}}(?:st|nd|rd|th)?"
    rf"|\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm|a\.m\.|p\.m\.)|at \d{{1,2}}(?::\d{{2}})?)\b", re.I)
EVENT = re.compile(r"\b(?:meet|meeting|call|deadline|due|review|demo|presentation|interview|submit\w*|sync"
                   r"|standup|catch up|appointment|class|exam|launch|release|session|workshop)\b", re.I)
TASK = re.compile(r"\b(?:I|We|we|You|you|He|he|She|she|They|they|(?!It\b|That\b|This\b|There\b)[A-Z][a-z]+)"
                  r"(?: will|'ll| need to| needs to| has to| have to)\s+(?!be\b|have\b|see\b|go\b)[a-z]+"
                  r"|\b(?:[Cc]an|[Cc]ould) you\s+[a-z]+|\b[Pp]lease\s+[a-z]+")

_active = False
_started = 0.0
_found = []          # [{"kind": "event" | "task", "text": ..., "when": ...}]: only what looked useful
_seen = set()
_review = []         # items still to ask about after the meeting


def _say(text, tone="calm"):
    return ("say", text, tone)


def meeting_active():
    return _active


def meeting_screen():
    if _active:
        lines = ["Listening for dates and tasks · nothing is recorded"]
        lines += [("📅 " if f["kind"] == "event" else "✅ ") + f["text"] for f in _found[-4:]]
        return {"title": "🔴 Meeting mode", "lines": lines}
    if _review:
        f = _review[0]
        return {"title": "🗂️ After the meeting", "lines": [("📅 " if f["kind"] == "event" else "✅ ") + f["text"],
                                                          f"{len(_review)} left to check"]}
    return None


def _extract(text):
    """Find dates and tasks in one piece of the meeting. Returns how many new ones were found."""
    new = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        words = sentence.split()
        if len(words) < 3:
            continue
        short = " ".join(words[:20])
        key = short.lower()
        if key in _seen or is_unsafe(short):
            continue
        if EVENT.search(sentence) and WHEN.search(sentence):
            m = re.search(r"\[date: (.*?)\]", resolve_dates(sentence))
            _found.append({"kind": "event", "text": short, "when": m.group(1) if m else ""})
        elif TASK.search(sentence):
            _found.append({"kind": "task", "text": short, "when": ""})
        else:
            continue
        _seen.add(key)
        new += 1
        print(f"   📌 Noticed ({_found[-1]['kind']}): {short}")
    return new


def _ask_next(memory):
    global _found
    if not _review:
        _found = []
        return [("screen", None), _say("That's everything. The rest of the meeting wasn't kept.")]
    item = _review[0]
    if item["kind"] == "event":
        question = f"I heard: {item['text']}"
        if item["when"]:
            question += f" That's {item['when']}."
            clash = [i for i in memory.list_get(SCHEDULE_LIST) if f"[date: {item['when']}]" in i]
            if clash:
                question += f" You already have {clash[0].split(' [date:')[0]} that day."
        question += " Should I add it to your schedule?"
    else:
        question = f"I heard a task: {item['text']} Should I save it to your tasks?"
    return [("screen", meeting_screen()), _say(question)]


def _save(item, memory):
    if item["kind"] == "event":
        memory.list_add(SCHEDULE_LIST, f"{item['text']} [date: {item['when']}]" if item["when"] else item["text"])
        return "your schedule"
    memory.list_add(TASKS_LIST, item["text"])
    return "your tasks"


def meeting_chunk(text, memory):
    """One piece of the meeting (while meeting mode is on). Returns a plan, or None to stay quiet."""
    global _active, _review
    if STOP.search(text.lower()):
        _active = False
        minutes = max(1, round((time.time() - _started) / 60))
        if not _found:
            return [("screen", None), _say(f"Meeting mode is off. That was about {minutes} minutes, "
                                            "and I didn't notice any dates or tasks. Nothing was kept.")]
        _review = list(_found)
        n = len(_review)
        return [_say(f"Meeting mode is off. I noticed {n} thing{'s' if n > 1 else ''}.")] + _ask_next(memory)
    if _extract(text):
        return [("screen", meeting_screen())]                # show it on screen, but stay quiet
    return None


def handle_meeting(text, memory):
    """Starting meeting mode, and the yes/no questions afterwards. Returns a plan, or None."""
    global _active, _found, _seen, _started, _review
    lower = text.lower().strip(" .!?,")

    if _review:
        item = _review[0]
        if END_REVIEW.search(lower):
            _review, _found = [], []
            return [("screen", None), _say("Okay, I won't save the rest. Nothing else was kept.")]
        if YES.search(lower) and not NO.search(lower):
            where = _save(item, memory)
            _review.pop(0)
            return [_say(f"Added to {where}.")] + _ask_next(memory)
        if NO.search(lower):
            _review.pop(0)
            return [_say("Okay, skipped.")] + _ask_next(memory)
        return [_say("Should I save it? Please say yes or no.")]

    if START.search(lower):
        _active, _found, _seen, _started = True, [], set(), time.time()
        return [("screen", meeting_screen()),
                _say("Meeting mode is on. I'll quietly listen for dates and tasks, and nothing is recorded. "
                     "When you're done, just say: meeting mode off.")]
    return None