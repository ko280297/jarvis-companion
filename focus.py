"""Focus & Energy: focus sessions with real breaks, so you can work well without burning out.
Timings, break ideas and nudges come from focus.json. Nothing here uses the LLM or the internet.
Focus history (minutes, and how each session felt) stays on this device, in memories.db."""
import json
import random
import re
import sqlite3
import time
from datetime import date, datetime
from pathlib import Path

from lists import STARTER_LISTS
from safety import is_unsafe, REFUSAL

HERE = Path(__file__).parent
DATA = json.loads((HERE / "focus.json").read_text(encoding="utf-8-sig"))
S = DATA["settings"]
IDEAS_LIST = next((n for n in STARTER_LISTS if n.startswith("idea")), "ideas")

START = re.compile(r"\b(?:start|begin)(?: a| the| my)? focus\b|\bfocus (?:mode|session)\b|\blet'?s focus\b"
                   r"|\bfocus for\b|\bpomodoro\b")
LEFT = re.compile(r"\b(?:how (?:much|many) (?:time|minutes?)|time) (?:is )?left\b|\bhow long (?:is )?left\b")
PAUSE = re.compile(r"\bpause\b")
RESUME = re.compile(r"\b(?:resume|unpause)\b|^continue\b")
STOP = re.compile(r"\b(?:stop|end|cancel|finish|quit)(?: the| my)? (?:focus|session|timer|pomodoro)\b")
SKIP_BREAK = re.compile(r"\b(?:skip|end|stop)(?: the| my)? break\b")
NOTE = re.compile(r"^(?:note|park|jot down|write down)\b[:,]?\s*(?:that\s+)?(.+)$")
STATS = re.compile(r"\bhow (?:did i do|much did i (?:focus|work)|long did i (?:focus|work))\b"
                   r"|\bfocus (?:stats|summary|report)\b")
ENERGY = [   # checked in this order: "not focused" must not count as "focused"
    ("distracted", re.compile(r"\b(?:distracted|scattered|couldn'?t focus|not focused|not (?:good|great)|bad)\b")),
    ("tired", re.compile(r"\b(?:tired|exhausted|sleepy|drained|burnt out|burned out|sluggish)\b")),
    ("focused", re.compile(r"\b(?:focused|good|great|productive|amazing|awesome|in the zone)\b")),
    ("okay", re.compile(r"\b(?:okay|ok|fine|alright|so so|not bad|average)\b")),
]

ONES = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
        "ten": 10, "fifteen": 15}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90}

_state = None          # {"kind": "focus" | "break", "task", "minutes", "ends", "left" (only while paused)}
_last_tip = None
_ask_energy = False    # waiting for "focused / okay / tired" after a session
_next_minutes = None   # a shorter next session after a tiring one
_skips = 0             # breaks skipped in a row
_nudged_day = None     # the "long day" nudge is said at most once a day


# ---------- Small helpers ----------
def _say(text, tone="calm"):
    return ("say", text, tone)


def _mins(n):
    return "1 minute" if n == 1 else f"{n} minutes"


def _hm(minutes):
    """95 -> '1 hour 35 minutes'."""
    h, m = divmod(int(minutes), 60)
    parts = ([f"{h} hour{'s' if h > 1 else ''}"] if h else []) + ([_mins(m)] if m else [])
    return " ".join(parts) or "0 minutes"


def _minutes(lower):
    """'25 minutes' / 'twenty five minutes' / 'an hour' -> 25 / 25 / 60, or None."""
    m = re.search(r"\b(\d{1,3})\s*(?:minutes?|mins?)\b", lower)
    if m:
        return int(m.group(1))
    m = re.search(r"\b((?:" + "|".join(TENS) + "|" + "|".join(ONES) + r")(?:[\s-](?:" + "|".join(ONES) + r"))?)"
                  r"\s*(?:minutes?|mins?)\b", lower)
    if m:
        return sum(ONES.get(w, 0) + TENS.get(w, 0) for w in re.split(r"[\s-]", m.group(1))) or None
    if re.search(r"\bhalf an hour\b", lower):
        return 30
    if re.search(r"\b(?:an|one) hour\b", lower):
        return 60
    return None


def _task(lower):
    """'focus for 25 minutes on my report' -> 'report'."""
    m = re.search(r"\bon (?:my |the |a )?(.+?)(?:\s+for\s+.*)?$", lower)
    return m.group(1).strip() if m else ""


def _paused():
    return bool(_state) and "left" in _state


# ---------- Focus history (on this device) ----------
def _db():
    db = sqlite3.connect(HERE / "memories.db")
    db.execute("CREATE TABLE IF NOT EXISTS focus_log "
               "(id INTEGER PRIMARY KEY, day TEXT, minutes INTEGER, task TEXT, energy TEXT, ended REAL)")
    return db


def _log(minutes, task):
    db = _db()
    db.execute("INSERT INTO focus_log (day, minutes, task, energy, ended) VALUES (?, ?, ?, ?, ?)",
               (date.today().isoformat(), int(minutes), task, None, time.time()))
    db.commit()
    db.close()


def _set_energy(energy):
    db = _db()
    db.execute("UPDATE focus_log SET energy = ? WHERE id = (SELECT MAX(id) FROM focus_log WHERE day = ?)",
               (energy, date.today().isoformat()))
    db.commit()
    db.close()


def _today():
    db = _db()
    rows = db.execute("SELECT minutes, energy FROM focus_log WHERE day = ?", (date.today().isoformat(),)).fetchall()
    db.close()
    return rows


def clear_focus_log():
    db = _db()
    db.execute("DELETE FROM focus_log")
    db.commit()
    db.close()


def _stats_text():
    rows = _today()
    if not rows:
        return "You haven't done any focus sessions today yet. Say start focus whenever you're ready."
    n, total = len(rows), sum(r[0] for r in rows)
    text = f"Today you did {n} focus session{'s' if n > 1 else ''}, {_hm(total)} in total."
    felt = [e for _, e in rows if e]
    if felt:
        text += f" You felt mostly {max(set(felt), key=felt.count)}."
    return text


def focus_summary_line():
    """One line for the daily summary, or '' if no focus today."""
    rows = _today()
    if not rows:
        return ""
    n, total = len(rows), sum(r[0] for r in rows)
    return f"You've focused for {_hm(total)} today, across {n} session{'s' if n > 1 else ''}."


# ---------- State for main.py ----------
def is_focusing():
    """True during a running focus session (reminders wait for the break; emergencies never wait)."""
    return bool(_state) and _state["kind"] == "focus" and not _paused()


def focus_due():
    """True when the current focus or break time is up."""
    return bool(_state) and not _paused() and time.time() >= _state["ends"]


def focus_screen():
    """What the screen shows: title, a line or two, and a countdown."""
    if not _state:
        return None
    if _state["kind"] == "focus":
        title = "🎯 Focus" + (f": {_state['task']}" if _state["task"] else "")
        lines = [f"Session {len(_today()) + 1} today",
                 "Paused" if _paused() else "Reminders will wait for your break · say \"note ...\" to park a thought"]
    else:
        title = "☕ Break"
        lines = ["Stretch, drink water, rest your eyes", "Paused" if _paused() else ""]
    return {"title": title, "lines": [l for l in lines if l],
            "timer": {"ends": _state.get("ends"), "left": _state.get("left")}}


# ---------- Timer ticks ----------
def focus_tick():
    """Called often by main.py: when focus or a break ends, returns what to say. Otherwise None."""
    global _state, _last_tip, _ask_energy, _skips, _nudged_day
    if not focus_due():
        return None
    if _state["kind"] == "focus":
        done, task = _state["minutes"], _state["task"]
        _log(done, task)
        rows = _today()
        total = sum(r[0] for r in rows)
        long_break = len(rows) % S["long_break_every"] == 0
        minutes = S["long_break_minutes"] if long_break else S["break_minutes"]
        _last_tip = random.choice([t for t in DATA["break_tips"] if t != _last_tip] or DATA["break_tips"])
        _state = {"kind": "break", "task": task, "minutes": minutes, "ends": time.time() + minutes * 60}
        msg = f"Great work! That was {_mins(done)}{' on ' + task if task else ''}. "
        msg += (f"You've earned a longer break: {_mins(minutes)}. " if long_break
                else f"Time for a {_mins(minutes)} break. ")
        msg += _last_tip
        if total >= S["daily_limit_minutes"] and _nudged_day != date.today():
            _nudged_day = date.today()
            msg += " " + DATA["nudges"]["long_day"].format(total=_hm(total))
        _ask_energy = True
        return [("screen", focus_screen()), _say(msg, "bright"), _say(DATA["energy_ask"], "gentle")]
    _state, _ask_energy, _skips = None, False, 0          # a break taken fully resets the skip count
    return [("screen", None), _say("Break's over. Ready for another focus session? Just say, start focus.", "bright")]


# ---------- Commands ----------
def _energy_reply(label):
    global _next_minutes
    _set_energy(label)
    brk = S["tired_break_minutes"]
    if label in ("tired", "distracted"):
        _next_minutes = S["tired_focus_minutes"]
        if _state and _state["kind"] == "break" and not _paused():   # stretch the current break
            _state["ends"] = max(_state["ends"], time.time() + brk * 60)
    reply = DATA["energy_replies"][label].format(brk=_mins(brk), next=_mins(S["tired_focus_minutes"]))
    return [("screen", focus_screen()), _say(reply, "gentle")]


def handle_focus(text, memory):
    """Focus commands. Returns a plan (steps for main.py's run_plan), or None."""
    global _state, _ask_energy, _next_minutes, _skips
    lower = text.lower().strip(" .!?,")

    # "How was that session?" -> focused / okay / tired / distracted
    if _ask_energy:
        for label, pattern in ENERGY:
            if pattern.search(lower):
                _ask_energy = False
                return _energy_reply(label)

    if STATS.search(lower):
        return [_say(_stats_text())]

    if _state:
        # Parking lot: park a thought without breaking focus
        m = NOTE.match(lower) if _state["kind"] == "focus" else None
        if m:
            item = m.group(1).strip()
            if is_unsafe(item):
                return [_say(REFUSAL, "gentle")]
            memory.list_add(IDEAS_LIST, item)
            print(f"🅿️ Parked: {item}")
            return [_say("Noted. Back to your focus.")]
        if LEFT.search(lower):
            left = _state["left"] if _paused() else max(0, _state["ends"] - time.time())
            what = "focus" if _state["kind"] == "focus" else "your break"
            amount = "Less than a minute" if left < 60 else _mins(round(left / 60))
            return [_say(f"{amount} left in {what}.")]
        if PAUSE.search(lower) and not _paused():
            _state["left"] = max(0, _state["ends"] - time.time())
            return [("screen", focus_screen()), _say("Paused. Say resume when you're ready.")]
        if RESUME.search(lower) and _paused():
            _state["ends"] = time.time() + _state.pop("left")
            return [("screen", focus_screen()), _say("Resumed. You've got this.")]
        if SKIP_BREAK.search(lower) and _state["kind"] == "break":
            _state, _ask_energy = None, False
            _skips += 1
            msg = "Okay, skipping the break. Say start focus when you're ready."
            if _skips >= S["max_skips"]:
                msg += " " + DATA["nudges"]["skips"]
            return [("screen", None), _say(msg)]
        if STOP.search(lower):
            if _state["kind"] == "focus":
                left = _state["left"] if _paused() else max(0, _state["ends"] - time.time())
                worked = round((_state["minutes"] * 60 - left) / 60)
                if worked >= 1:
                    _log(worked, _state["task"])           # count what was actually done
                _state = None
                return [("screen", None), _say(f"Okay, focus stopped. You worked for {_mins(max(worked, 0))}."
                                              if worked >= 1 else "Okay, focus stopped.")]
            _state = None
            return [("screen", None), _say("Okay, break ended.")]

    if START.search(lower):
        minutes = max(1, min(_minutes(lower) or _next_minutes or S["focus_minutes"], 120))
        _next_minutes, _ask_energy = None, False
        task = _task(lower)
        _state = {"kind": "focus", "task": task, "minutes": minutes, "ends": time.time() + minutes * 60}
        msg = (f"Focus started{' on ' + task if task else ''}: {_mins(minutes)}. "
               "I'll keep things quiet until your break.")
        hour = datetime.now().hour
        if hour >= S["late_hour"] or hour < 5:
            msg += " " + DATA["nudges"]["late"]
        return [("screen", focus_screen()), _say(msg)]
    return None