"""Wellbeing mode: gentle, well-known self-help exercises matched to how you say you feel.
Exercises, advice and numbers come from wellbeing.json (checked, with sources), never from the LLM.
The LLM only adds one warm, checked sentence that shows Jarvis understood (main.py checks it).
Jarvis is not a therapist: it offers self-help tools and, when feelings last, points to real help.
Crisis words are handled earlier, by emergency.py."""
import json
import random
import re
import sqlite3
import time
from collections import deque
from pathlib import Path

from emergency import _numbers, _spoken

HERE = Path(__file__).parent
DATA = json.loads((HERE / "wellbeing.json").read_text(encoding="utf-8-sig"))
EX = DATA["exercises"]
REPEAT_WINDOW = 600          # seconds: within 10 minutes, don't repeat the same full response
BACKCHANNEL_GAP = 30         # seconds: during a thought dump, never say "I'm listening" more often than this
SESSION_MINUTES = 30

FEEL = re.compile(r"\b(?:i(?:'m| am)|i feel|i'?m feeling|feeling|i'?ve been|i have been)\b")
NEEDS_HELP_NOTE = re.compile(
    r"\b(?:for|from|since) (?:many |a few )?(?:weeks|months|years|a long time)\b|\bevery ?day\b"
    r"|\balways\b|\ball the time\b|\blately\b|\bhopeless\b|\bweeks\b|\bmonths\b")
YES = re.compile(r"\b(?:yes|yeah|yep|sure|ok|okay|please|alright|go ahead|give me|why not|let'?s do)\b")
NO = re.compile(r"^(?:no|nope|not now|maybe later|i'?m good)\b")
DONE = re.compile(
    r"\b(?:i'?m|i am) (?:done|dun|dull|finish\w*)\b|\bthat'?s (?:all|it)\b"
    r"|\bstop venting\b|\benough\b|\bi'?m finished\b")
KEEP = re.compile(r"\b(?:keep|save)\b")
LET_GO = re.compile(r"\b(?:let (?:it )?go|delete|don'?t (?:keep|save)|forget)\b")
CHECK_IN = re.compile(r"\b(?:check in with me|ask me how i(?:'m| am)|wellbeing (?:mode|check))\b")

DIRECT = [   # asking for an exercise by name
    (r"\bbox breathing\b", "box"),
    (r"\b4[\s-]?7[\s-]?8\b|\bfour seven eight\b", "breath_478"),
    (r"\bphysiological sigh\b", "sigh"),
    (r"\bbutterfly hug\b", "butterfly_hug"),
    (r"\bgrounding\b|\b5[\s-]?4[\s-]?3[\s-]?2[\s-]?1\b", "grounding"),
    (r"\bscribbl\w*", "scribble"),
    (r"\bjournal\w*|\bprompt\b|\bwrite (?:it|things) down\b", "journal"),
    (r"\b(?:dump|vent)\b.*\b(?:thoughts|mind|feelings)\b|\bthought dump\b|\bi (?:just )?need to vent\b", "dump"),
    (r"\bbreathing exercise\b|\bhelp me (?:calm down|relax|breathe)\b|\bi need to calm down\b", "breath_446"),
]

_mode = None              # None | "dump" | "dump_end" | "ask_mood" | ("offer", exercise_key)
_dump = []                # thought-dump words: RAM only
_silences = 0             # quiet turns in a row during a thought dump
_last_backchannel = 0.0   # when Jarvis last said "I'm listening" during a thought dump
_last_phrase = None
_last_mood = (None, 0.0)  # (mood, time) to avoid repeating ourselves
_last_note = 0.0          # when we last suggested professional help
_session = deque(maxlen=5)   # recent feelings the user shared: RAM only, never saved, thought dumps never included


# ---------- Small helpers ----------
def _say(text, tone="gentle"):
    return ("say", text, tone)


def _remember(text):
    _session.append((time.time(), text))


def _earlier():
    """What the user shared about their feelings earlier in this session (not counting right now)."""
    now = time.time()
    recent = [t for ts, t in _session if now - ts < SESSION_MINUTES * 60]
    return recent[:-1]


def clear_session():
    global _last_mood
    _session.clear()
    _last_mood = (None, 0.0)


def _backchannel():
    """A short, varied 'I'm listening' for a natural pause, at most once every 30 seconds. None = stay quiet."""
    global _last_backchannel, _last_phrase
    if time.time() - _last_backchannel < BACKCHANNEL_GAP:
        return None
    options = [p for p in DATA["vent_backchannels"] if p != _last_phrase] or DATA["vent_backchannels"]
    _last_phrase = random.choice(options)
    _last_backchannel = time.time()
    return _say(_last_phrase)


def _help_note(memory):
    mh = _numbers(memory)["mental_health"]
    return ("If this feeling stays for more than two weeks, or makes everyday things hard, "
            f"please talk to a doctor or a counsellor. {mh['label']}, on {_spoken(mh['number'])}, "
            "is free and there any time.")


# ---------- Building plans ----------
def _exercise_plan(key):
    ex = EX[key]
    plan = [("screen", {"title": f"🌿 {ex['name']}", "lines": [ex.get("short", "")], "anim": ex.get("anim")}),
            _say(ex["intro"])]
    if ex.get("caution"):
        plan.append(_say(ex["caution"]))
    if ex["kind"] == "breath":
        for _ in range(ex["rounds"]):
            for phase, secs, cue in ex["steps"]:
                plan.append(("cue", cue, phase, secs, ex["name"]))
        plan.append(_say("Well done. Notice how you feel now."))
    elif ex["kind"] == "guided":
        acks = DATA.get("listening_acks", ["Well noticed."])
        for line, secs, *rest in ex["steps"]:             # optional 3rd value: how many things to name
            count = rest[0] if rest else None
            screen = {"title": f"🌿 {ex['name']}", "lines": [line], "anim": ex.get("anim")}
            plan += [("screen", screen), _say(line)]
            # interactive exercises listen for the answer (shown on screen, never saved)
            plan.append(("ask", secs, ex["name"], line, acks, count) if ex.get("interactive") else ("wait", secs))
        plan.append(_say("Well done. Take a moment to notice how you feel."))
    plan.append(("screen", None))
    return plan


def _activity_plan(key):
    """An exercise, a journaling prompt, or the thought dump."""
    global _mode, _dump, _silences, _last_backchannel
    if key == "dump":
        _mode, _dump, _silences = "dump", [], 0
        _last_backchannel = time.time()                    # no "I'm listening" in the first 30 seconds
        return [("screen", {"title": "🗣️ Thought dump",
                            "lines": ["I'm listening. Say \"I'm done\" when you're finished.",
                                      "Nothing is saved unless you ask."]}),
                _say("I'm here, and I'm listening. Take your time, I won't interrupt. "
                     "When you're finished, just say: I'm done.")]
    if key == "journal":
        prompt = random.choice(DATA["journal_prompts"])
        return [("screen", {"title": "📝 Journaling", "lines": [prompt]}),
                _say(f"Grab a paper, or just talk to me. Here's a prompt: {prompt} Take your time.")]
    return _exercise_plan(key)


def _match_mood(lower, any_mood=False):
    for name, mood in DATA["moods"].items():
        if (any_mood or mood.get("no_feel_needed") or FEEL.search(lower)) and \
                any(re.search(rf"\b{t}\b", lower) for t in mood["triggers"]):
            return name
    return None


def _mood_plan(name, text, lower, memory):
    """First time: a warm line, the exercise, a small suggestion, an offer.
    Same feeling again soon: just a warm line (and the help note if it has lasted), no repeats."""
    global _mode, _last_mood, _last_note
    mood = DATA["moods"][name]
    _remember(text)
    earlier = _earlier()
    now = time.time()
    repeat = _last_mood[0] == name and now - _last_mood[1] < REPEAT_WINDOW
    _last_mood = (name, now)

    if repeat:
        plan = [("empathy", text, "I hear you. Thank you for telling me.", "", earlier)]
    else:
        lead = "Let's try something gentle together." if mood.get("do") else ""
        plan = [("empathy", text, mood["say"], lead, earlier)]
        for key in mood.get("do", []):
            plan += _exercise_plan(key)
        if mood.get("suggest"):
            plan.append(_say(mood["suggest"]))

    if NEEDS_HELP_NOTE.search(lower) and now - _last_note > REPEAT_WINDOW:
        _last_note = now
        plan.append(_say(_help_note(memory)))

    if repeat:
        plan.append(_say("I'm right here with you."))
    elif mood.get("offer"):
        _mode = ("offer", mood["offer"])
        plan.append(_say(mood["offer_say"]))
    return plan


# ---------- Journal (only when the user asks to keep a thought dump) ----------
def _journal_db():
    db = sqlite3.connect(HERE / "memories.db")
    db.execute("CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, created REAL, text TEXT)")
    return db


def _save_journal(text):
    db = _journal_db()
    db.execute("INSERT INTO journal (created, text) VALUES (?, ?)", (time.time(), text))
    db.commit()
    db.close()


def clear_journal():
    db = _journal_db()
    db.execute("DELETE FROM journal")
    db.commit()
    db.close()


# ---------- Main entry points ----------
def handle_wellbeing(text, memory, paused=True):
    """Returns a plan (list of steps for main.py to carry out), or None if this isn't about wellbeing.
    Steps: ("say", text, tone) | ("empathy", user_text, fallback, lead, earlier)
           | ("cue", text, phase, seconds, name) | ("ask", seconds, name, line, acks, count)
           | ("wait", seconds) | ("screen", dict or None) | ("silent",)"""
    global _mode, _dump, _silences
    lower = text.lower().strip(" .!?,")

    # Thought dump: listen quietly; a short nod only after a real pause; nothing saved unless asked
    if _mode == "dump" and len(lower.split()) <= 6 and (
            any(re.search(p, lower) for p, k in DIRECT if k != "dump")
            or any(re.search(rf"\b{t}\b", lower) for m in DATA["moods"].values() if m.get("no_feel_needed")
                   for t in m["triggers"])):
        _mode, _dump, _silences = None, [], 0      # a new request ("I can't sleep"), not part of the thought dump
    if _mode == "dump":
        _silences = 0
        _dump.append(text)
        if DONE.search(lower):
            _mode = "dump_end"
            return [_say("Thank you for sharing that with me. Do you want me to keep it, or let it go?")]
        if paused:
            nod = _backchannel()
            if nod:
                return [nod]
        return [("silent",)]

    if _mode == "dump_end":
        _mode = None
        if KEEP.search(lower) and not LET_GO.search(lower):
            _save_journal(" ".join(_dump))
            _dump = []
            return [_say("Okay, I've kept it privately on this device."), ("screen", None)]
        _dump = []
        return [_say("Okay, it's gone. I hope you feel a little lighter."), ("screen", None)]

    # Answer to an offer ("Would you like a journaling prompt?")
    after_offer = False
    if isinstance(_mode, tuple):
        key = _mode[1]
        _mode = None
        if YES.search(lower) and not NO.search(lower) and len(lower.split()) <= 6:
            return _activity_plan(key)
        if NO.search(lower) and len(lower.split()) <= 3:      # just "no" / "no thanks"
            return [_say("Okay. I'm here whenever you need me.")]
        after_offer = True                                    # "No, but..." -> listen to the rest

    asking = _mode == "ask_mood"
    if asking:
        _mode = None

    if CHECK_IN.search(lower):
        _mode = "ask_mood"
        return [_say("Of course. How are you feeling right now?")]

    for pattern, key in DIRECT:
        if re.search(pattern, lower):
            return _activity_plan(key)

    mood = _match_mood(lower, any_mood=asking or after_offer)
    if not mood and after_offer and _last_mood[0] and NEEDS_HELP_NOTE.search(lower):
        mood = _last_mood[0]          # "No, but it's been every day for weeks" -> same feeling, lasting
    if mood:
        return _mood_plan(mood, text, lower, memory)
    return None


def stop_all():
    """The ✕ on the screen: end any thought dump or offer. Nothing is saved."""
    global _mode, _dump, _silences
    _mode, _dump, _silences = None, [], 0


def vent_mode():
    """True while a thought dump is open: main.py then listens longer and stays quiet."""
    return _mode == "dump"


def vent_silence():
    """Nothing heard during a thought dump: check in once, then gently close (nothing saved)."""
    global _mode, _dump, _silences
    if _mode != "dump":
        return None
    _silences += 1
    if _silences == 1:
        return [_say("I'm still here. Take your time.")]
    _mode, _dump, _silences = None, [], 0
    return [_say("I'll be here whenever you want to talk. Nothing was saved."), ("screen", None)]