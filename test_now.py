"""Test without speaking. Uses a throw-away database: your real memories.db is never touched.
   python test_now.py           reminder "done" tests + hear the female voice
   python test_now.py --novoice only the tests"""
import os, sys, tempfile, time
from datetime import datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import reminders as R
import memory as M
import todo

tmp = os.path.join(tempfile.mkdtemp(), "test.db")
rem = R.Reminders(tmp)
mem = M.MemoryStore(tmp)
ok = bad = 0

def ring(text):
    """A reminder for `text` that rings right now (what Jarvis does when the time comes)."""
    rid = rem.add(datetime.now() - timedelta(seconds=1), text, "reminder")
    rem.mark_done(rid)
    return rid

def ticked(text):
    return any(r[1] == text and r[4] for r in rem.log_day())

def check(name, reply, must=None, must_not=None, tick=None, text=None):
    global ok, bad
    r = (reply or "") if isinstance(reply, str) else ((reply or {}).get("say") or "")
    good = True
    if must and must.lower() not in r.lower(): good = False
    if must_not and must_not.lower() in r.lower(): good = False
    if tick is not None and ticked(text) != tick: good = False
    ok += good; bad += not good
    print(f"{'✅' if good else '❌'} {name}\n     Jarvis: {r or '(not a reminder reply)'}")

def say_rem(t):  return R.handle_reminder_command(t, rem)
def say_todo(t): return todo._did_or_done(t, mem, rem)

print("\n--- Reminder rings, then you answer ---")
ring("submit the report")
check('"I have submitted the report, done"', say_rem("I have submitted the report, done"),
      must="well done", tick=True, text="submit the report")

ring("call the bank")
check('"done"', say_rem("done"), must="cleared", tick=True, text="call the bank")

ring("take medicine")
check('"okay" does NOT mark it done', say_rem("okay"), must_not="cleared", tick=False, text="take medicine")
check('"is the report done?" is a question, not done', say_rem("is the report done?"),
      must_not="cleared", tick=False, text="take medicine")

print("\n--- Did I ...? ---")
check('"Did I take my medicine?" (rang, not ticked)', say_todo("Did I take my medicine?"), must="isn't ticked")
check('"I took my medicine"', say_todo("I took my medicine"), must="Done", tick=True, text="take medicine")
check('"Did I take my medicine?" (now ticked)', say_todo("Did I take my medicine?"), must="Yes")
check('"Did I submit the report?"', say_todo("Did I submit the report?"), must="Yes")

print(f"\nResult: {ok} passed, {bad} failed")

if "--novoice" not in sys.argv:
    print("\n--- Voices (turn your volume up) ---")
    try:
        import tts
        for kind, line in (("male", "Hi Krati, this is my male voice."),
                           ("female", "Hi Krati, this is my female voice. Your meeting is at 5 PM.")):
            if tts.set_voice(kind):
                print(f"🔊 {kind} voice playing..."); tts.speak(line); time.sleep(0.3)
            else:
                print(f"⚠️ {kind} voice file not found ({tts.VOICES[kind]}.onnx)")
    except Exception as e:
        print("⚠️ could not play the voice:", e)