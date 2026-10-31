"""Fix 113 check: the follow-up tracker, including the sentences where it could get confused.   jarvis followup_check.py
A throw-away database is used: your real memory is never opened. No microphone, no AI, no internet."""
import os
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import memory as M  # noqa: E402
import followup as F  # noqa: E402

ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


def fresh():
    F._pending = None
    F._touched.update(id=None, at=0.0)
    return M.MemoryStore(os.path.join(tempfile.mkdtemp(), "test.db"))


mem = fresh()
LAST = {"said": ""}


def H(t):
    """Like main.py: Jarvis's last reply is passed along (anything else Jarvis said counts as "something else")."""
    r = F.handle(t, mem, last_said=LAST["said"])
    LAST["said"] = r or "(another feature answered)"
    return r


NOON = datetime.combine(date.today(), datetime.min.time()).replace(hour=12)

print("— Saving, both directions —")
r = H("I'm waiting for Arjun to send the report")
check('"waiting for Arjun to send the report" is saved', r and "waiting for Arjun: the report" in r, r)
check("…and it says when it will ask (in 2 days)", r and ("on " in r or "in 2" in r or "tomorrow" in r), r)
r = H("Rahul owes me 500 rupees")
check('"Rahul owes me 500 rupees" is saved', r and "Rahul: 500 rupees" in r, r)
r = H("I told Riya I'd send the file by Friday")
check('"I told Riya I\'d send the file by Friday" is a promise, due Friday', r and "send the file for Riya" in r, r)
r = H("Riya will call me back")
check('"Riya will call me back" is saved', r and "Riya" in r and "calling you back" in r, r)
r = H("I'm waiting for my sister to return my charger")
check('"waiting for my sister …" -> said back as "your sister"', r and "your sister" in r and "your charger" in r, r)
r = H("I'm waiting for Arjun to send the report")
check("the same one again is not saved twice", r and "already" in r, r)

print("\n— Sentences that must NOT become a follow-up —")
for said in ["I'm waiting for the bus", "I'm waiting for the results", "I'm waiting for you to answer",
             "I'm waiting for my order", "remind me to follow up with Arjun on Monday", "what is Arjun waiting for",
             "Arjun doesn't owe me anything", "should I wait for Riya to call", "I'm not waiting for anyone",
             "let me know when it rains", "my brother's name is Arjun", "I owe you one", "waiting for the weekend",
             "add call Arjun to my to-do list"]:
    r = H(said)
    check(f'"{said}" is left alone', r is None, r)

print("\n— Reading the list —")
r = H("what am I waiting for")
check('"what am I waiting for" lists the people', r and "Arjun" in r and "Rahul" in r and "Riya" in r, r)
check("…but not your own promises", r and "send the file" not in r, r)
r = H("what did I promise")
check('"what did I promise" lists only your promises', r and "send the file" in r and "Rahul" not in r, r)
r = H("show my follow-ups")
check('"show my follow-ups" lists both', r and "waiting for" in r and "promised" in r, r)

print("\n— Ticking off, and the traps —")
r = H("Arjun sent me a funny meme")
check('"Arjun sent me a funny meme" does NOT tick off the report', r is None, r)
r = H("Arjun hasn't sent the report yet")
check('"Arjun hasn\'t sent the report yet" does NOT tick it off', r is None, r)
r = H("did Arjun send the report?")
check('"did Arjun send the report?" (a question) does NOT tick it off', r is None, r)
r = H("Arjun sent the report")
check('"Arjun sent the report" ticks it off', r and "ticked off" in r and "Arjun" in r, r)
r = H("Rahul paid me")
check('"Rahul paid me" ticks off the 500 rupees', r and "Rahul" in r and "ticked" in r, r)
r = H("I sent the file to Riya")
check('"I sent the file to Riya" ticks off YOUR promise (not "Riya will call me back")',
      r and "send the file" in r and "calling" not in r, r)
r = H("what am I waiting for")
check("…and Riya's call back is still there", r and "Riya" in r and "Arjun" not in r and "Rahul" not in r, r)

print("\n— Edit options —")
r = H("I'm waiting for Meera to send the photos")
r = H("change it to the wedding photos")
check('"change it to the wedding photos" right after adding', r and "the wedding photos" in r, r)
r = H("Rahul paid me back")
r = H("change it to 600 rupees")
check('"change it" right after ticking one off does NOT change another follow-up', r is None, r)
H("I'm waiting for Kavya to send the slides")
LAST["said"] = "Added to your to-do list: call the bank."
r = H("change it to call the bank manager")
check('"change it" after a to-do reply is left for the to-do list (not the follow-up)', r is None, r)
r = H("ask me about the Meera one on Monday")
check('"ask me about the Meera one on Monday" moves the question', r and "ask you about it" in r, r)
r = H("forget the Meera one")
check('"forget the Meera one" removes it', r and "removed" in r and "Meera" in r, r)
r = H("forget the Kabir one")
check('"forget the Kabir one" (no such follow-up) says so, changes nothing', r and "couldn't find" in r, r)
r = H("stop asking me about follow-ups")
check('"stop asking me about follow-ups" turns the questions off', r and "won't ask" in r, r)
check("…then nothing is asked, even when due", F.due(mem, NOON + timedelta(days=9)) is None)
r = H("start asking me about follow-ups again")
check("…and back on", r and "again" in r, r)

print("\n— Jarvis asks, at the right time only —")
mem = fresh()
H("I'm waiting for Arjun to send the report")
check("not asked on the same day", F.due(mem, NOON) is None)
later = NOON + timedelta(days=2)
check("not asked at night (11 PM)", F.due(mem, later.replace(hour=23)) is None)
check("not asked early morning (7 AM)", F.due(mem, later.replace(hour=7)) is None)
q = F.due(mem, later)
check("asked in 2 days, at a normal hour", q and q.startswith("Any news from Arjun about the report?"), q)
check("asked only once that day", F.due(mem, later + timedelta(hours=1)) is None)
r = H("not yet")
check('"not yet" -> asks again in 2 days', r and "ask you about it" in r, r)
F._pending = None
q = F.due(mem, later + timedelta(days=2))
r = H("ask me on Monday")
check('"ask me on Monday" (after the question) moves it', r and "ask you about it" in r, r)
q = F.due(mem, later + timedelta(days=9))
r = H("yes")
check('"yes" -> ticked off', r and "ticked off" in r, r)
check("nothing left to ask", F.due(mem, later + timedelta(days=30)) is None)
H("I'm waiting for Riya to call me back")
F.due(mem, NOON + timedelta(days=3))
r = H("what's the weather")
check("something else after the question: carried on normally (not taken as an answer)", r is None, r)
check("…and it's still saved", "Riya" in (H("what am I waiting for") or ""))
F._pending = None
H("I promised Arjun I'll call him tomorrow")
q = F.due(mem, NOON + timedelta(days=1))
check("your own promise is asked as: did you …? (on its day)", q and q.startswith("Did you call him?"), q)

print("\n— Hindi (what the translator gives) —")
r = H("I am waiting for Arjun's report.")
check('"मैं अर्जुन की रिपोर्ट का इंतज़ार कर रही हूँ" -> "I am waiting for Arjun\'s report." is saved',
      r and "Arjun: the report" in r, r)
F._pending = None
F.due(mem, NOON + timedelta(days=20))
r = H("Not now.")
check('"अभी नहीं" -> "Not now." counts as not yet', r and "ask you about it" in r, r)
F._pending = None
F.due(mem, NOON + timedelta(days=25))
r = H("Ask on Monday.")
check('"सोमवार को पूछना" -> "Ask on Monday." moves it', r and "ask you about it on" in r, r)
F.MEMORY = mem
check("the wake-word loop's quick check has no side effects", F.waiting(NOON + timedelta(days=5)) and
      F.waiting(NOON + timedelta(days=5)))

print("\n— Privacy —")
check("names in follow-ups are kept private (never sent online)", {"Arjun", "Riya"} <= F.names(mem), F.names(mem))
print(f"\n{ok} passed, {bad} failed")
