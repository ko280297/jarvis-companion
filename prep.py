"""Prepare me before things (fix 108, desk idea #3). Pure code on this device: no AI, no internet.

A few minutes before a reminder or a schedule item with a time (10 minutes, or what you set), Jarvis looks through
YOUR notes, memories and open to-do tasks for anything that shares its words, and tells you:
    "In 10 minutes: call the bank. Your note "Loan" says: ask about the loan form.
     Also on your to-do list: send the bank papers."
If nothing matches, it says nothing (no empty "you have a reminder" twice).
Never while a guest is talking, never in kid mode, and it waits during focus and meetings.

Edit options:  "stop preparing me" / "start preparing me again"     "prepare me 20 minutes before"
               "don't prepare me for the dentist"                     "what will you prepare me for?"
"""
import re
import time
from datetime import datetime, timedelta

MEMORY = None                      # set by main.py
DEFAULT_LEAD = 10                  # minutes
_done = set()                      # things already prepared (or skipped) this session
GENERIC = {"call", "meet", "meeting", "with", "the", "and", "for", "to", "at", "go", "going", "visit", "pay",
           "send", "get", "take", "make", "check", "about", "reminder", "remind", "today", "tomorrow", "tonight",
           "my", "mom", "dad", "a", "an", "of", "in", "on", "do", "it", "this", "that", "some", "your", "have",
           "has", "need", "needs", "start", "join", "attend", "finish", "submit", "drink", "water"}
_ON = re.compile(r"\b(?:start|turn on|resume|switch on)\s+(?:preparing|briefing)\s+me\b"
                 r"|\bprepare me (?:again|before things)\b", re.I)
_OFF = re.compile(r"^(?:please\s+)?(?:stop|don'?t|do not|no more)\s+(?:preparing|briefing|prepare|brief)\s+me\s*"
                  r"(?:before things|any ?more)?[.!]*$", re.I)
_LEAD = re.compile(r"\bprepare me (\d{1,2}) ?(?:minutes?|mins?) (?:before|earlier|ahead)\b", re.I)
_SKIP = re.compile(r"\b(?:don'?t|do not|no need to) prepare me for (?:the |my )?(.+?)[.!]*$", re.I)
_WHAT = re.compile(r"\bwhat will you prepare me for\b|\bwhat(?:'s| is) coming up that you(?:'ll| will) prepare\b", re.I)


def _lead(memory):
    try:
        return max(1, min(60, int(memory.get_setting("prep_minutes") or DEFAULT_LEAD)))
    except ValueError:
        return DEFAULT_LEAD


def _on(memory):
    return (memory.get_setting("prep") or "on") != "off"


def _key(when, text):
    return f"{when:%Y-%m-%d %H:%M}|{text.lower()}"


def _upcoming(memory, reminders, minutes):
    """(when, text) of reminders / schedule items with a time, starting within the next `minutes`."""
    import todo
    now = datetime.now()
    out = []
    for when, text, _event in todo.timeline(memory, reminders):
        if when and now < when <= now + timedelta(minutes=minutes):
            out.append((when, text))
    return out


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in GENERIC}


def _hit(words, text):
    have = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return any(w in have or any(h.startswith(w) and len(w) >= 5 for h in have) for w in words)


def related(memory, text):
    """Lines from your notes, memories and open tasks that share a real word with `text` (code, not AI)."""
    import notes
    import todo
    words = _words(text)
    if not words:
        return []
    found = []
    for n in notes._all(memory):
        if _hit(words, n["title"]) or _hit(words, n["body"]):
            body = " ".join((n["body"] or "").split())
            words_ = body.split()
            short = " ".join(words_[:30]) + ("…" if len(words_) > 30 else "")    # the note itself, kept short
            if short:
                found.append(f'Your note "{n["title"]}" says: {short.rstrip(".")}.')
        if len(found) >= 2:
            break
    for m in memory.all():
        clean = re.sub(r"\s*\[date:.*?\]", "", m.split(" (saved on")[0]).strip()
        if _hit(words, clean):
            found.append(f"You told me: {clean[:1].lower() + clean[1:]}.")
            break
    tasks = [r["text"] for r in todo._rows(memory, "done = 0")
             if _hit(words, r["text"]) and r["text"].strip().lower() != text.strip().lower()][:2]
    if tasks:
        found.append("Also on your to-do list: " + "; ".join(tasks) + ".")
    return found


def pending(reminders):
    """Cheap check for the wake-word loop: is there something to prepare right now?"""
    memory = MEMORY
    if memory is None or not _on(memory):
        return False
    try:
        return any(_key(w, t) not in _done for w, t in _upcoming(memory, reminders, _lead(memory)))
    except Exception:
        return False


def due(memory, reminders):
    """What to say now, or None. Each thing is prepared once."""
    if not _on(memory):
        return None
    lines = []
    for when, text in _upcoming(memory, reminders, _lead(memory)):
        k = _key(when, text)
        if k in _done:
            continue
        _done.add(k)
        found = related(memory, text)
        if not found:
            continue                                   # nothing useful to add: stay quiet
        mins = max(1, round((when - datetime.now()).total_seconds() / 60))
        lines.append(f"In {mins} minute{'s' if mins != 1 else ''}: {text}. " + " ".join(found))
    return " ".join(lines) or None


def command(text, memory, reminders):
    """The edit options. Returns what to say, or None."""
    low = text.lower().strip(" .!?")
    m = _LEAD.search(low)
    if m:
        memory.set_setting("prep_minutes", m.group(1))
        memory.set_setting("prep", "on")
        return f"Okay, I'll prepare you {int(m.group(1))} minutes before things."
    if _OFF.match(low):
        memory.set_setting("prep", "off")
        return "Okay, I won't prepare you before things any more. Say: start preparing me, to turn it back on."
    if _ON.search(low):
        memory.set_setting("prep", "on")
        return (f"Okay, I'll prepare you {_lead(memory)} minutes before reminders and plans, "
                "with anything from your notes, memories and to-do list.")
    m = _SKIP.search(low)
    if m:
        words = _words(m.group(1))
        hits = [(w, t) for w, t in _upcoming(memory, reminders, 24 * 60) if _hit(words, t)]
        for w, t in hits:
            _done.add(_key(w, t))
        return (f"Okay, I won't prepare you for {hits[0][1]}." if hits
                else "I couldn't find that in your plans for today. Nothing changed.")
    if _WHAT.search(low):
        items = _upcoming(memory, reminders, 24 * 60)
        with_info = [(w, t) for w, t in items if related(memory, t)]
        if not with_info:
            return "Nothing today has notes or tasks to go with it, so I'll stay quiet before your plans."
        return "I'll prepare you for: " + "; ".join(f"{t} at {w:%I:%M %p}".replace(" 0", " ") for w, t in with_info) + "."
    return None
