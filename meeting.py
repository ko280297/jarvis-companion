"""Meeting mode: listens during a meeting for plans and tasks, then asks before saving anything.
The transcript is never saved: each sentence is checked, turned into a short, clean entry
(what, when, what time, who), and then dropped. No LLM, no internet: everything happens on this device."""
import re
import time

from lists import STARTER_LISTS
from safety import is_unsafe
from tools import resolve_dates

SCHEDULE_LIST = next((n for n in STARTER_LISTS if n.startswith("sched")), "schedule")
TASKS_LIST = "task"  # the same name the list commands use ("tasks" -> "task")

START = re.compile(r"\bmeeting mode (?:on|start)\b|\b(?:start|turn on|begin|switch on) (?:the )?meeting mode\b")
STOP = re.compile(r"\bmeeting mode (?:off|of|stop|end)\b|\b(?:stop|end|turn off|switch off|close) (?:the )?meeting mode\b"
                  r"|\bthe meeting is (?:over|done|finished)\b")
YES = re.compile(r"\b(?:yes|yeah|yep|sure|ok|okay|please|add it|save it|go ahead)\b")
NO = re.compile(r"\b(?:no|nope|skip|don'?t|not needed|leave it)\b")
END_REVIEW = re.compile(r"\b(?:stop|that'?s all|cancel|forget (?:it|them|all|the rest)|meeting mode|exit|close"
                        r"|later|skip (?:all|everything|the rest)|i'?m done|enough)\b")

MONTHS = (r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?")
WEEKDAYS = r"monday|tuesday|wednesday|thursday|friday|saturday|sunday"
WHEN = re.compile(
    rf"\b(?:today|tomorrow|tonight|day after tomorrow|next week|this week"
    rf"|(?:next |this |on )?(?:{WEEKDAYS})"
    rf"|\d{{1,2}}(?:st|nd|rd|th)?(?: of)? (?:{MONTHS})|(?:{MONTHS}) \d{{1,2}}(?:st|nd|rd|th)?"
    rf"|\d{{1,2}}(?::\d{{2}})?\s*(?:am|pm|a\.m\.|p\.m\.)|at \d{{1,2}}(?::\d{{2}})?)\b", re.I)
EVENT = re.compile(r"\b(?:meet|meeting|call|deadline|due|review|demo|presentation|interview|submit\w*|sync"
                   r"|standup|catch up|appointment|class|exam|launch|release|session|workshop)\b", re.I)
TASK = re.compile(r"\b(?:I|We|we|You|you|He|he|She|she|They|they|(?!It\b|That\b|This\b|There\b)[A-Z][a-z]+)"
                  r"(?: will|'ll| need to| needs to| has to| have to| should)\s+(?!be\b|have\b|see\b|go\b)[a-z]+"
                  r"|\b(?:[Cc]an|[Cc]ould) you\s+[a-z]+|\b[Pp]lease\s+[a-z]+"
                  r"|\b(?:[Rr]emind (?:me|us) to|[Dd]on'?t forget to|[Mm]ake sure (?:to|you|we))\s+[a-z]+"
                  r"|\b[Ll]et'?s\s+(?!go\b|start\b|begin\b|see\b|move on\b|talk\b|wrap\b)[a-z]+\s+\w+")

# Not a plan: talk about the past, or a plan that was called off
PAST = re.compile(rf"\b(?:last (?:week|month|{WEEKDAYS})|yesterday|days? ago)\b", re.I)
CALLED_OFF = re.compile(r"\b(?:don'?t (?:think )?(?:we )?need to|no need to|won'?t (?:need|meet)|not going to meet"
                        r"|no (?:meeting|call|class|review|demo|session|standup)|cancel\w*|called off|is off"
                        r"|not happening|(?:isn'?t|is not|aren'?t|are not) (?:happening|on))\b", re.I)

# Turning a spoken sentence into a short entry
LEAD = re.compile(r"^(?:(?:okay|ok|so|alright|right|and|also|um|uh|well|great)[,\s]+)*"
                  r"(?:(?:team|guys|everyone|folks|all)[,\s]+)?", re.I)
NAME_LEAD = re.compile(r"^([A-Z][a-z]+),\s+")                     # "Arjun, please update ..." -> owner Arjun
OWNER = re.compile(r"^([A-Z][a-z]+)\s+(?:will|'ll|needs to|has to|should)\s+")
PRONOUNS = {"I", "We", "You", "They", "He", "She", "It", "That", "This", "There"}
ASK = re.compile(r"^(?:let'?s|let us|(?:we|i|you)(?:'ll| will| need to| have to| should| are going to|'re going to)"
                 r"|can you|could you|please|remind (?:me|us) to|don'?t forget to|make sure (?:to|you|we)"
                 r"|there(?:'s| is) (?:a|an|the))\s+", re.I)
TIME_PHRASE = re.compile(r"\s*\b(?:at\s+|by\s+|around\s+)?(?:\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)(?!\w)"
                         r"|(?:half past|quarter past|quarter to)\s+\w+|\d{1,2}:\d{2})", re.I)
DATE_PHRASE = re.compile(r"\s*\b(?:(?:on|by|before|until|till|at|for|from|due|this|next)\s+)*(?:the\s+)?(?:"
                         + WHEN.pattern + r")", re.I)
WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
           "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}

_active = False
_started = 0.0
_found = []          # [{"kind": "event" | "task", "text": ..., "when": ...}]: only what looked useful
_seen = set()
_review = []         # items still to ask about after the meeting


def _say(text, tone="calm"):
    return ("say", text, tone)


def meeting_active():
    return _active


def _line(f):
    return ("📅 " if f["kind"] == "event" else "✅ ") + f["text"] + (f" · {f['when']}" if f["when"] else "")


def meeting_screen():
    if _active:
        lines = ["Listening for plans and tasks · nothing is recorded"]
        lines += [_line(f) for f in _found[-4:]]
        return {"title": "🔴 Meeting mode", "lines": lines}
    if _review:
        return {"title": "🗂️ After the meeting", "lines": [_line(_review[0]), f"{len(_review)} left to check"]}
    return None


def _clock(sentence):
    """'at 3 PM' -> '3 PM', 'half past four' -> '4:30', 'at 4' -> '4:00', or '' if no time was said."""
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)(?!\w)", sentence, re.I)
    if m:
        return f"{int(m.group(1))}{':' + m.group(2) if m.group(2) else ''} {'AM' if m.group(3)[0].lower() == 'a' else 'PM'}"
    m = re.search(r"\b(half past|quarter past|quarter to)\s+(\d{1,2}|" + "|".join(WORDNUM) + r")\b", sentence, re.I)
    if m:
        h = int(m.group(2)) if m.group(2).isdigit() else WORDNUM[m.group(2).lower()]
        kind = m.group(1).lower()
        if kind == "half past":
            return f"{h}:30"
        if kind == "quarter past":
            return f"{h}:15"
        return f"{h - 1 or 12}:45"
    m = re.search(r"\bat (\d{1,2})(?::(\d{2}))?\b", sentence, re.I)
    if m:
        return f"{int(m.group(1))}:{m.group(2) or '00'}"
    return ""


def _title(sentence):
    """'Okay team, let's review the slides on Friday at 3 PM.' -> ('Review the slides', None)
    'Arjun, please update the budget sheet before the 15th' -> ('Update the budget sheet', 'Arjun')"""
    s = LEAD.sub("", sentence.strip().rstrip(".!?"))
    owner = None
    m = NAME_LEAD.match(s)
    if m and m.group(1) not in PRONOUNS:
        owner, s = m.group(1), s[m.end():]
    m = OWNER.match(s)
    if m and m.group(1) not in PRONOUNS:
        owner, s = m.group(1), s[m.end():]
    for _ in range(2):                                   # "please can you ..." -> both go
        s = ASK.sub("", s)
    s = DATE_PHRASE.sub("", TIME_PHRASE.sub("", s))
    s = re.sub(r"(?:\s+(?:on|by|before|until|till|at|for|from|the|this|next|and))+$", "", s.strip(), flags=re.I)
    s = " ".join(s.split()[:8]).strip(" ,;:-")
    return (s[:1].upper() + s[1:]) if s else "", owner


def _extract(text):
    """Find plans and tasks in one piece of the meeting. Returns how many new ones were found."""
    new = 0
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        sentence = sentence.strip()
        if len(sentence.split()) < 3 or is_unsafe(sentence):
            continue
        if PAST.search(sentence) or CALLED_OFF.search(sentence):
            print(f"   (skipped, not a plan: {sentence})")
            continue
        is_event = bool(EVENT.search(sentence) and WHEN.search(sentence))
        if not is_event and not TASK.search(sentence):
            continue
        title, owner = _title(sentence)
        if len(title) < 3:                               # nothing useful left: keep the short sentence instead
            title = " ".join(sentence.split()[:12]).rstrip(".!?")
        m = re.search(r"\[date: (.*?)\]", resolve_dates(sentence)) if WHEN.search(sentence) else None
        when = m.group(1) if m else ""
        clock = _clock(sentence)
        entry = title + (f" at {clock}" if clock else "")
        if owner:
            entry = f"{owner}: {entry[:1].lower() + entry[1:]}"
        key = (entry.lower(), when)
        if key in _seen:
            continue
        _seen.add(key)
        _found.append({"kind": "event" if is_event else "task", "text": entry, "when": when})
        new += 1
        print(f"   📌 Noticed ({_found[-1]['kind']}): {entry}" + (f" | {when}" if when else ""))
    return new


def _ask_next(memory):
    global _found
    if not _review:
        _found = []
        return [("screen", None), _say("That's everything. The rest of the meeting wasn't kept.")]
    item = _review[0]
    if item["kind"] == "event":
        saved_as = f"{item['text']} [date: {item['when']}]" if item["when"] else item["text"]
        if saved_as in memory.list_get(SCHEDULE_LIST):              # exactly this is saved already
            _review.pop(0)
            return [_say(f"{item['text']} is already on your schedule, so I'll skip it.")] + _ask_next(memory)
        question = f"I noted: {item['text']}" + (f", on {item['when']}" if item["when"] else "") + "."
        if item["when"]:
            clash = [i for i in memory.list_get(SCHEDULE_LIST) if f"[date: {item['when']}]" in i]
            if clash:
                question += f" You already have {clash[0].split(' [date:')[0]} that day."
        question += " Should I add it to your schedule?"
    else:
        question = (f"I noted a task: {item['text']}" + (f", by {item['when']}" if item["when"] else "")
                    + ". Should I save it to your tasks?")
    return [("screen", meeting_screen()), _say(question)]


def _save(item, memory):
    if item["kind"] == "event":
        memory.list_add(SCHEDULE_LIST, f"{item['text']} [date: {item['when']}]" if item["when"] else item["text"])
        return "your schedule"
    memory.list_add(TASKS_LIST, item["text"] + (f" (by {item['when']})" if item["when"] else ""))
    return "your tasks"


def meeting_chunk(text, memory):
    """One piece of the meeting (while meeting mode is on). Returns a plan, or None to stay quiet."""
    global _active, _review
    if STOP.search(text.lower()):
        _active = False
        minutes = max(1, round((time.time() - _started) / 60))
        if not _found:
            return [("screen", None), _say(f"Meeting mode is off. That was about {minutes} minutes, "
                                            "and I didn't notice any plans or tasks. Nothing was kept.")]
        _review = list(_found)
        n = len(_review)
        return [_say(f"Meeting mode is off. I noticed {n} thing{'s' if n > 1 else ''}.")] + _ask_next(memory)
    if _extract(text):
        return [("screen", meeting_screen())]                # show it on screen, but stay quiet
    return None


_unclear = 0


def stop_all():
    """The ✕ on the screen: meeting mode off, no questions, nothing saved."""
    global _active, _found, _review, _unclear
    _active, _found, _review, _unclear = False, [], [], 0


def handle_meeting(text, memory):
    """Starting meeting mode, and the yes/no questions afterwards. Returns a plan, or None."""
    global _active, _found, _seen, _started, _review, _unclear
    lower = text.lower().strip(" .!?,")

    if _review:
        item = _review[0]
        if END_REVIEW.search(lower):
            _review, _found = [], []
            return [("screen", None), _say("Okay, I won't save the rest. Nothing else was kept.")]
        if YES.search(lower) and not NO.search(lower):
            _unclear = 0  # a clear answer
            where = _save(item, memory)
            _review.pop(0)
            return [_say(f"Added to {where}.")] + _ask_next(memory)
        if NO.search(lower):
            _review.pop(0)
            return [_say("Okay, skipped.")] + _ask_next(memory)
        _unclear += 1
        if _unclear >= 2:                                 # still unclear: stop asking, keep nothing more
            _review, _found, _unclear = [], [], 0
            return [("screen", None), _say("I'll leave the rest unsaved. Nothing else was kept.")]
        return [_say("Should I save it? Please say yes or no.")]

    if START.search(lower):
        _active, _found, _seen, _started = True, [], set(), time.time()
        return [("screen", meeting_screen()),
                _say("Meeting mode is on. I'll quietly listen for plans and tasks, and nothing is recorded. "
                     "When you're done, just say: meeting mode off.")]
    return None