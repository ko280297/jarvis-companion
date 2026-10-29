"""Reminders the way people really say them (fix 93): the AI understands, the code checks, you confirm.

1. Rewrites (exact, instant): everyday ways of saying a reminder become the plain form the rules know.
     "ping me / nudge me / alert me / don't let me forget / make sure I"  -> "remind me"
     "half past six" -> at 6:30   "quarter to seven" -> at 6:45   "around 6" -> at 6
     "an hour and a half" -> 90 minutes   "two and a half hours" -> 150 minutes
     "I have to call the bank at 4, remind me 15 minutes before" -> remind me at 3:45 to call the bank
2. Only when the rules still can't be sure (time words left in the task, or no time found), the on-device AI
   fills in a small form: what, which date, what time, or in how many minutes.
3. The code checks that form: the task must be words you said, the hour must be a number you said,
   "tomorrow" / "Friday" / "evening" / am-pm are taken from YOUR words, not the AI's, and the time must be ahead.
4. Jarvis reads it back before saving: "Remind you to call didi at 6:30 PM today. Should I set it?"
   yes -> set   no -> nothing set   "make it 7" -> set for 7 instead
Nothing goes online: the AI is the same small model that runs on this device.
"""
import json
import re
import time
from datetime import datetime, timedelta

import requests

import reminders as R

URL = "http://127.0.0.1:11434/api/chat"       # set by main.py (the same local Ollama)
MODEL = "qwen2.5:1.5b"
PENDING = None          # {"task", "due", "rep", "t"} waiting for yes / no / a new time
ASK = None              # tests can put a fake AI here: ASK(text, now) -> dict

_W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
      "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45,
      "a": 1, "an": 1}
_NUMW = r"\d{1,2}|" + "|".join(sorted((k for k in _W if k not in ("a", "an")), key=len, reverse=True))
_YES = re.compile(r"^(?:yes|yeah|yep|yup|haan|ha|han|sure|ok(?:ay)?|correct|right|do it|set it|please do|go ahead)\b", re.I)
_NO = re.compile(r"^(?:no|nope|nahi|nah|don'?t|cancel|never ?mind|leave it)\b(?!.*\b(?:at|make it|instead)\b)", re.I)
REMINDY = re.compile(
    r"^(?P<lead>(?:(?:please|hey|jarvis|okay|ok|and|so|can you|could you)[,\s]+)*)"
    r"(?:(?:ping|nudge|alert|buzz|notify|poke) me\b|(?:don'?t|do not) let me forget(?: to| about)?\b"
    r"|make sure (?:that )?i (?:remember to|don'?t forget to)?\b|i need a reminder(?: to| for| about)?\b"
    r"|give me a (?:reminder|nudge|ping)(?: to| for| about)?\b)", re.I)
_TIMEWORDS = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\b|p\.?m\b|o'?clock\b|minutes?\b|mins?\b|hours?\b)"
                        r"|\bat \d|\bhalf past\b|\bquarter (?:past|to)\b|\b(?:minutes?|hours?) (?:before|after|later|early)\b"
                        r"|\bwhen it'?s \d|\b(?:noon|midnight)\b|\band a half\b|\b\d{1,2} (?:past|to) \d"
                        r"|\b\d{1,2}\s*(?:something|ish|or so)\b|\bin the (?:morning|afternoon|evening|night)\b"
                        r"|\b\d{1,2}\b(?=.*\b(?:morning|afternoon|evening|night|tonight)\b)", re.I)   # fix 95


def _num(w):
    w = w.lower().strip()
    return int(w) if w.isdigit() else _W.get(w)


def _clock(h, m):
    return f"at {h}:{m:02d}" if m else f"at {h}"


def rewrite(text):
    """Everyday reminder phrasing -> the plain form the rules already handle exactly. Nothing else changes."""
    t = text.strip()
    m = REMINDY.match(t)
    if m:
        t = "remind me " + t[m.end():].lstrip(" ,")
    if not re.search(r"\bremind", t, re.I):
        return text                                   # not a reminder: leave it alone
    low = t
    # "an hour and a half" / "one and a half hours" / "1.5 hours" / "two and a half hours"
    low = re.sub(r"\b(?:an|one|1) hour and a half\b|\b(?:one|1) and a half hours?\b|\b1\.5 hours?\b", "90 minutes",
                 low, flags=re.I)
    low = re.sub(rf"\b({_NUMW}) and a half hours?\b|\b({_NUMW}) hours? and a half\b",
                 lambda m: f"{_num(m.group(1) or m.group(2)) * 60 + 30} minutes", low, flags=re.I)
    # "half past six" / "quarter past 6" / "quarter to seven"
    low = re.sub(rf"\b(?:at\s+|around\s+|about\s+|by\s+)?half past ({_NUMW})\b",
                 lambda m: _clock(_num(m.group(1)), 30), low, flags=re.I)
    low = re.sub(rf"\b(?:at\s+|around\s+|about\s+|by\s+)?(?:a )?quarter past ({_NUMW})\b",
                 lambda m: _clock(_num(m.group(1)), 15), low, flags=re.I)
    low = re.sub(rf"\b(?:at\s+|around\s+|about\s+|by\s+)?(?:a )?quarter to ({_NUMW})\b",
                 lambda m: _clock((_num(m.group(1)) - 2) % 12 + 1, 45), low, flags=re.I)
    # fix 94: "5ish" / "around 5-ish" -> at 5;  "10 minutes after 6" / "10 minutes past 6" -> at 6:10
    low = re.sub(r"\b(?:at\s+)?(?:around\s+|about\s+)?(\d{1,2})\s*-?\s*ish\b", r"at \1", low, flags=re.I)
    low = re.sub(rf"\b(?:at\s+)?({_NUMW}) minutes? (?:after|past) ({_NUMW})\b(?!\s*(?:minutes?|hours?))",
                 lambda m: _clock(_num(m.group(2)), _num(m.group(1))) if _num(m.group(1)) < 60 else m.group(0),
                 low, flags=re.I)
    low = re.sub(rf"\b(?:at\s+)?({_NUMW}) minutes? (?:before|to) ({_NUMW})\b(?!\s*(?:minutes?|hours?))",
                 lambda m: _clock((_num(m.group(2)) - 2) % 12 + 1, 60 - _num(m.group(1))) if 0 < _num(m.group(1)) < 60
                 else m.group(0), low, flags=re.I)
    # "20 past 5" / "10 to 6" -> at 5:20 / at 5:50
    low = re.sub(rf"\b(?:at\s+|around\s+|about\s+)?(\d{{1,2}}|ten|twenty|five|twenty five) (past|to) ({_NUMW})\b(?!\s*(?:minutes?|hours?))",
                 lambda m: (_clock(_num(m.group(3)), _num(m.group(1))) if m.group(2).lower() == "past"
                            else _clock((_num(m.group(3)) - 2) % 12 + 1, 60 - _num(m.group(1))))
                 if 0 < _num(m.group(1)) < 60 and _num(m.group(3)) else m.group(0), low, flags=re.I)
    # "around 6" / "by 6" / "6 o'clock" -> "at 6"
    low = re.sub(r"\b(?:around|about|by)\s+(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)(?=\s|$|[,.])", r"at \1", low,
                 flags=re.I)
    low = re.sub(rf"\b(?:at\s+)?({_NUMW}) o'?clock\b", lambda m: _clock(_num(m.group(1)), 0), low, flags=re.I)
    low = re.sub(r"\bat at\b", "at", low, flags=re.I)
    # "7 in the evening" / "when it's 7 in the evening" -> at 7 pm
    low = re.sub(rf"\b(?:when it'?s\s+|at\s+)?({_NUMW})(?::(\d{{2}}))?\s+in the (morning|afternoon|evening|night)\b",
                 lambda m: f"at {_num(m.group(1))}{':' + m.group(2) if m.group(2) else ''} "
                           + ("am" if m.group(3).lower() == "morning" else "pm"), low, flags=re.I)
    low = re.sub(rf"\bwhen it'?s\s+({_NUMW})(?::(\d{{2}}))?\b",
                 lambda m: f"at {_num(m.group(1))}{':' + m.group(2) if m.group(2) else ''}", low, flags=re.I)
    # "tomorrow evening" / "tonight" / "this morning" with NO number said at all -> that part of the day's usual hour
    # (fix 94: "7 something in the evening" has a number, so it's never turned into 6 PM)
    if not re.search(rf"\d|\b(?:{_NUMW})\b|\bhalf\b|\bquarter\b", low, re.I):
        def _part(m):
            h = {"morning": "8 am", "afternoon": "2 pm", "evening": "6 pm", "night": "9 pm", "tonight": "9 pm"}[
                m.group(2).lower()]
            return f"{m.group(1) or ''}at {h}"
        low = re.sub(r"(?<!every )\b(?:this |in the )?(tomorrow |today )?(morning|afternoon|evening|night)\b"
                     r"|(?<!every )\b()(tonight)\b",
                     lambda m: _part(m) if m.group(2) else f"at 9 pm", low, count=1, flags=re.I)
    # "I have to call the bank at 4, remind me 15 minutes before"
    m = re.search(rf"^(?:i (?:have to|need to|must|will|'ll|am going to|'m going to)\s+|i have (?:a |an )?)?(?P<task>.+?)\s+"
                  rf"(?P<clock>at \d{{1,2}}(?::\d{{2}})?\s*(?:a\.?m\.?|p\.?m\.?)?)(?P<day>\s+(?:today|tomorrow|tonight))?"
                  rf"[,.]?\s+(?:so\s+|and\s+)?remind me\s+(?P<n>{_NUMW}|half an?)\s*(?P<u>minutes?|hours?|hour)?\s+"
                  rf"(?:before|early|earlier|ahead)\b", low, flags=re.I)
    if m:
        clock = m.group("clock").lower()
        if not re.search(r"[ap]\.?m", clock) and 1 <= int(re.search(r"\d+", clock).group()) <= 7:
            clock += " pm"                      # "call the bank at 4": 4 PM, not 4 in the morning
        due, _ = R.parse_when(f"{clock}{m.group('day') or ''}".lower())
        n = 30 if m.group("n").lower().startswith("half") else _num(m.group("n"))
        if due and n:
            due -= timedelta(minutes=n * (60 if (m.group("u") or "").lower().startswith("hour") else 1))
            if due > datetime.now():
                day = "" if due.date() == datetime.now().date() else " tomorrow" \
                    if due.date() == (datetime.now() + timedelta(days=1)).date() else f" on {due:%A}"
                low = f"remind me at {due:%I:%M %p}{day} to {m.group('task')}".replace(" 0", " ", 1)
    if low != text:
        print(f"   (reminder said as: {low})")
    return low


def suspicious(task):
    """Time words left inside the task text mean the rules didn't fully understand the time."""
    return bool(task) and bool(_TIMEWORDS.search(task))


def _ask_ai(text, now):
    if ASK:
        return ASK(text, now)
    sys_prompt = (
        f"Now it is {now:%A, %d %B %Y, %H:%M}. Read a reminder request and fill this JSON, nothing else: "
        '{"task": "what to be reminded about, in the user\'s own words", "date": "YYYY-MM-DD or empty", '
        '"time": "HH:MM in 24-hour time, or empty", "in_minutes": 0}. '
        "Use in_minutes only for 'in 20 minutes' style requests. Leave anything not said empty. Never guess.")
    ex_u = "remind me when it's 7 in the evening to call dad"
    ex_a = '{"task": "call dad", "date": "", "time": "19:00", "in_minutes": 0}'
    r = requests.post(URL, json={
        "model": MODEL, "stream": False, "keep_alive": "24h", "format": "json",
        "options": {"temperature": 0, "num_predict": 80},
        "messages": [{"role": "system", "content": sys_prompt}, {"role": "user", "content": ex_u},
                     {"role": "assistant", "content": ex_a}, {"role": "user", "content": text}]}, timeout=30)
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def _said(words, text):
    stop = {"the", "a", "an", "to", "my", "of", "for", "about", "and", "me", "i", "it"}
    w = [x for x in re.findall(r"[a-z0-9']+", words.lower()) if x not in stop]
    have = set(re.findall(r"[a-z0-9']+", text.lower()))
    return not w or sum(1 for x in w if x in have or x.rstrip("s") in have) / len(w) >= 0.75


def _hours_said(text):
    """Every hour number the user said (digits or words), to check the AI's time against."""
    nums = {int(n) for n in re.findall(r"\b(\d{1,2})(?::\d{2})?\b", text)}
    nums |= {v for k, v in _W.items() if k not in ("a", "an") and re.search(rf"\b{k}\b", text, re.I)}
    return {n for n in nums if 0 <= n <= 24}


def check(form, text, now=None):
    """The code checks the AI's form. Returns (task, due, problem): problem is a question to ask, or ''."""
    now = now or datetime.now()
    low = text.lower()
    task = re.sub(r"^(?:to|about|that)\s+", "", str(form.get("task") or "").strip(" .,!?"), flags=re.I)
    if not task or not _said(task, text) or suspicious(task):
        return None, None, "What should I remind you about?"
    # 1) "in N minutes": the number must really be in the sentence
    mins = form.get("in_minutes") or 0
    try:
        mins = int(mins)
    except (TypeError, ValueError):
        mins = 0
    if 0 < mins <= 7 * 24 * 60:
        # fix 94: only "in 10 minutes" / "after 2 hours" style; "10 minutes after 6" is a clock time, not a wait
        if re.search(rf"\b(?:in|after|within)\s+(?:about\s+)?(?:{_NUMW}|\d+|an?|half an?)\s*(?:and a half\s+)?"
                     rf"(?:minutes?|mins?|hours?|hrs?)\b", low) and not re.search(
                     rf"\b(?:minutes?|hours?)\s+(?:after|past|before|to)\s+(?:{_NUMW}|\d)", low):
            return task, now + timedelta(minutes=mins), ""
        mins = 0
    # 2) the clock time: the hour must be a number the user said, or a part of the day they said
    hhmm = re.fullmatch(r"(\d{1,2}):(\d{2})", str(form.get("time") or "").strip())
    part = next((p for p in R.PART_OF_DAY if re.search(rf"\b{p}\b", low)), None)
    if "tonight" in low:
        part = part or "night"
    if hhmm:
        h, m = int(hhmm.group(1)), int(hhmm.group(2))
        if h > 23 or m > 59 or (h % 12 or 12) not in {(x % 12 or 12) for x in _hours_said(low)} | (
                {R.PART_OF_DAY[part] % 12 or 12} if part and not _hours_said(low) else set()):
            return task, None, "At what time should I remind you?"
    elif part:
        h, m = R.PART_OF_DAY[part], 0
    else:
        return task, None, "At what time should I remind you?"
    # 3) morning / evening / am / pm come from the user's words, never from the AI
    if re.search(r"\b(?:p\.?m\.?|evening|night|tonight|afternoon)\b", low) and h < 12:
        h += 12
    elif re.search(r"\b(?:a\.?m\.?|morning)\b", low) and h >= 12:
        h -= 12
    ampm_said = bool(re.search(r"\b(?:a\.?m\.?|p\.?m\.?|morning|evening|night|tonight|afternoon|noon|midnight)\b", low))
    # 4) the day comes from the user's words too
    days = 0
    wd = re.search(r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", low)
    if "day after tomorrow" in low:
        days = 2
    elif "tomorrow" in low:
        days = 1
    elif wd:
        days = (["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(wd.group(1))
                - now.weekday()) % 7
    due = (now + timedelta(days=days)).replace(hour=h, minute=m, second=0, microsecond=0)
    if due <= now:
        if not ampm_said and h < 12 and days == 0 and due + timedelta(hours=12) > now:
            due += timedelta(hours=12)          # "at 4" said in the afternoon = 4 PM
        else:
            due += timedelta(days=7 if wd else 1)
    return task, due, ""


def understand(text, rem):
    """The AI path: fill the form, check it, read it back. Always returns something to say."""
    global PENDING
    now = datetime.now()
    try:
        form = _ask_ai(text, now)
        print(f"   (AI understood: {form})")
    except (requests.RequestException, ValueError, KeyError, TypeError) as e:
        print(f"   (AI couldn't read the reminder: {type(e).__name__})")
        return "I couldn't work out the time. Say it like: remind me at 6:30 PM to call didi."
    task, due, problem = check(form if isinstance(form, dict) else {}, text, now)
    print(f"   (code checked: task={task!r}, time={due}, {problem or 'ok'})")
    if problem:
        if task and not due:
            R._pending_task, R._pending_repeat = task, ""   # the answer to "at what time?" sets it, like before
        return problem
    rep, _ = R._repeat_of(text.lower())
    PENDING = {"task": task, "due": due, "rep": rep, "t": time.time()}
    when = R.repeat_words(rep, due) if rep else R.when_words(due)
    # fix 94: two reminders in one sentence: never drop the second one silently
    rest = re.split(rf"\b{re.escape(task.split()[-1])}\b", text, maxsplit=1, flags=re.I)
    extra = ""
    if len(rest) == 2 and re.search(r"\band\b.*\b(?:later|then|after|at|in|to)\b", rest[1], re.I) \
            and re.search(r"[a-z]{3,}", re.sub(r"\b(?:and|then|later|after|at|in|to|me|remind|hours?|minutes?|\d+)\b",
                                              "", rest[1], flags=re.I)):
        extra = " I only understood the first reminder. Tell me the second one on its own after this."
    return f"Remind you to {task}, {when}. Should I set it?{extra}"


def answer(text, rem):
    """The reply to "Should I set it?": yes, no, or a new time ("make it 7"). None if it isn't one."""
    global PENDING
    if not PENDING or time.time() - PENDING["t"] > 120:
        PENDING = None
        return None
    p = PENDING
    t = re.sub(r"^[\s\-.,!?]*(?:(?:hey|ok|okay)\s+)?(?:jarvis)?[\s\-.,!?]*", "", text.lower()).strip(" .!?")
    if _YES.match(t) and not re.search(r"\d", t):
        PENDING = None
        rem.add(p["due"], p["task"], "reminder", p["rep"])
        return R._set_reply(p["task"], p["due"], p["rep"])
    m = re.search(r"\b(?:make it|at|change it to|instead)\s+(.+)$|^(\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)?)$", t)
    if m:
        said = (m.group(1) or m.group(2)).replace("instead", "").strip()
        due, _ = R.parse_when(said if said.startswith(("in ", "at ")) else "at " + said)
        if due:
            if not re.search(r"\b(?:today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", said):
                due = due.replace(year=p["due"].year, month=p["due"].month, day=p["due"].day)
                if due <= datetime.now():
                    due += timedelta(days=1)
            PENDING = None
            rem.add(due, p["task"], "reminder", p["rep"])
            return R._set_reply(p["task"], due, p["rep"])
    if _NO.match(t):
        PENDING = None
        return "Okay, I didn't set it. You can say it again, like: remind me at 6:30 PM to call didi."
    PENDING = None                       # something else entirely: carry on normally
    return None
