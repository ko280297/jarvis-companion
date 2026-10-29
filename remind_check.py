r"""Fix 93/94 check: reminders said the way people really say them.   .venv\Scripts\python.exe remind_check.py
Nothing is saved: a pretend reminder list is used, and your real memory is never opened.
  remind_check.py           checks the rewrites and the code checks (uses a pretend AI, no Ollama needed)
  remind_check.py --real    also asks the real on-device AI (start Ollama first) and shows what it understood
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import remindai  # noqa: E402
import reminders as R  # noqa: E402

ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


class FakeList:                      # stands in for the reminder list: nothing is saved anywhere
    def __init__(self):
        self.items = []
    def add(self, due, text, kind, repeat=""):
        self.items.append((due, text, repeat))
    def duplicate(self, due, text):
        return False


print("— 1. Everyday phrasings -> the exact rules (no AI needed) —")
for said, want in [("ping me around half past six to call didi", "remind me at 6:30 to call didi"),
                   ("don't let me forget to pay the electricity bill tomorrow evening", "tomorrow at 6 pm"),
                   ("nudge me in an hour and a half about the laundry", "in 90 minutes"),
                   ("I have to call the bank at 4, remind me 15 minutes before", "at 3:45 PM"),
                   ("remind me when it's 7 in the evening to call dad", "remind me at 7 pm to call dad"),
                   ("remind me at quarter to seven to leave", "at 6:45"),
                   ("remind me tonight to lock the door", "at 9 pm"),
                   ("make sure I remember to buy milk at 7 pm", "remind me buy milk at 7 pm"),
                   ("remind me 20 past 5 to leave", "at 5:20"), ("remind me at 10 to 6 to call papa", "at 5:50")]:
    got = remindai.rewrite(said)
    check(f'"{said}"', want in got, got)
for said in ["tell me a joke", "what's the weather tonight", "remind me every morning to drink water"]:
    check(f'"{said}" is left alone', remindai.rewrite(said) == said, remindai.rewrite(said))

print("\n— 2. Only unclear reminders go to the AI —")
check('"take 2 tablets" is a normal task (no AI)', not remindai.suspicious("take 2 tablets"))
check('"20 past 5 to leave" has a time left inside: AI', remindai.suspicious("20 past 5 to leave"))

print("\n— 3. The code checks the AI's form (a pretend AI here) —")
now = datetime(2026, 10, 29, 10, 0)
said = "remind me 20 past 5 to leave"
t, due, q = remindai.check({"task": "leave", "time": "17:20"}, said, now)
check("a good form: leave, at 5:20 PM today", t == "leave" and due == now.replace(hour=17, minute=20), (t, due, q))
t, due, q = remindai.check({"task": "go to the gym", "time": "17:20"}, said, now)
check("the AI invents a task you never said -> asks what", q.startswith("What should"), (t, due, q))
t, due, q = remindai.check({"task": "leave", "time": "18:20"}, said, now)
check("the AI changes the hour (6 instead of 5) -> asks the time", q.startswith("At what time"), (t, due, q))
t, due, q = remindai.check({"task": "leave", "time": "05:20"}, said, now)
check("the AI says 5:20 AM, but it's already 10 AM -> 5:20 PM today", due == now.replace(hour=17, minute=20), due)
t, due, q = remindai.check({"task": "leave", "time": "17:20", "date": "2026-11-05"}, said + " tomorrow", now)
check('"tomorrow" comes from your words, not the AI\'s date', due and due.date() == (now + timedelta(days=1)).date(), due)
t, due, q = remindai.check({"task": "leave", "time": "17:20"}, "remind me 20 past 5 in the morning to leave", now)
check('"in the morning" wins over the AI\'s PM -> 5:20 AM tomorrow', due == (now + timedelta(days=1)).replace(hour=5, minute=20), due)
t, due, q = remindai.check({"task": "leave", "in_minutes": 45}, "remind me to leave soon", now)
check("in_minutes with no number said -> asks the time", q.startswith("At what time"), (t, due, q))

print("\n— 4. Jarvis reads it back, and you decide —")
lst = FakeList()
remindai.ASK = lambda text, now: {"task": "leave", "time": "17:20"}
r = remindai.understand(said, lst)
check("read back first: Remind you to leave … Should I set it?", "Should I set it?" in r and not lst.items, r)
r = remindai.answer("yes", lst)
check('"yes" sets it', lst.items and lst.items[-1][1] == "leave", r)
remindai.understand(said, lst)
r = remindai.answer("no", lst)
check('"no" sets nothing', len(lst.items) == 1 and "didn't set" in r, r)
remindai.understand(said, lst)
r = remindai.answer("make it 6", lst)
check('"make it 6" sets it at 6 instead', len(lst.items) == 2 and lst.items[-1][0].hour in (6, 18), r)
remindai.understand(said, lst)
r = remindai.answer("what's the weather", lst)
check("something else entirely: nothing set, carry on normally", r is None and len(lst.items) == 2, r)
remindai.ASK = None
print("\n— 5. Fix 94: what the real AI did on 29 Oct (its exact answers, replayed) —")
now = datetime(2026, 10, 29, 0, 35)
s1 = "remind me 7 something in the evening to cook"
check('"7 something in the evening" is NOT turned into 6 PM', "6 pm" not in remindai.rewrite(s1), remindai.rewrite(s1))
t, due, q = remindai.check({"task": "cook", "date": "2026-10-29", "time": "18:00", "in_minutes": 0}, s1, now)
check("the AI's 6 PM for '7 something' is refused (you never said 6) -> asks", q.startswith("At what time"), (t, due, q))
s2 = "remind me 10 minutes after 6 to check the oven"
check('"10 minutes after 6" = at 6:10 (exact rule)', "at 6:10" in remindai.rewrite(s2), remindai.rewrite(s2))
t, due, q = remindai.check({"task": "check the oven", "date": "2026-10-29", "time": "06:10", "in_minutes": 10}, s2, now)
check("...and if the AI says 'in 10 minutes' too, the clock time wins", due and due.minute == 10 and due.hour in (6, 18),
      (t, due, q))
check('"about 5ish" = at 5 (exact rule)', "at 5 " in remindai.rewrite("remind me at about 5ish to stretch") + " ",
      remindai.rewrite("remind me at about 5ish to stretch"))
remindai.ASK = lambda text, now: {"task": "call mom", "date": "", "time": "05:00", "in_minutes": 0}
r = remindai.understand("remind me at 5 to call mom and 3 hours later to sleep", FakeList())
check("two reminders in one sentence: it says it only understood the first", "only understood the first" in r, r)
remindai.ASK = None
remindai.PENDING = None
print(f"\n{ok} passed, {bad} failed")

if "--real" in sys.argv:
    print("\n— The real on-device AI (nothing is saved) —")
    for said in ["remind me at about 5ish to stretch", "remind me 7 something in the evening to cook",
                 "remind me after lunch around 2ish to call the plumber",
                 "remind me 10 minutes after 6 to check the oven", "remind me at 5 to call mom and 3 hours later to sleep"]:
        print(f"\nYou: {said}")
        text = remindai.rewrite(said)
        print("Jarvis:", remindai.understand(text, FakeList()))
        remindai.PENDING = None