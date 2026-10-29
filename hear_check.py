"""Fix 88/90/91 check: your real log lines, exactly as speech-to-text wrote them.   python hear_check.py
No microphone, no AI, no internet, and your real memory is never opened (a fake to-do list is used).
Every bad line from a log can be added at the bottom, so that mistake is checked forever."""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hear  # noqa: E402

TASKS = ["water the plants", "pay rent"]
hear._open_tasks = lambda memory: TASKS
ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


def run(text):
    r = hear.process(text, None)
    return r.get("say") or "→ " + r["text"]


print("— What you said (as speech-to-text heard it) —")
r = run("what is in my tubular list")
check('"tubular list" is your to-do list', r == "→ what is in my to-do list", r)
r = run("2-do list, It is 2-do list, Not tubular list")
check('"It is 2-do list, not tubular list" does the last request again', r == "→ what is in my to-do list", r)
r = run("Take water plans")
check('"Take water plans" asks first: tick off water the plants?', "tick off water the plants" in r, r)
r = run("yes")
check('...and "yes" ticks it', r == "→ tick water the plants", r)
r = run("in my to-do list, take water the plant")
check('"in my to-do list, take water the plant" asks first', "tick off water the plants" in r, r)
r = run("no")
check('...and "no" changes nothing', "didn't change anything" in r, r)
r = run("tick water plans")
check('"tick water plans" ticks water the plants', r == "→ tick water the plants", r)
r = run("and, Beac, what are the plans?")
check('"what are the plans?" reads your day', r == "→ what's my day", r)
r = run("take a deep breath")
check('"take a deep breath" is left alone (not a task)', r == "→ take a deep breath", r)
r = run("check the weather")
check('"check the weather" is left alone', r == "→ check the weather", r)

print("\n— What the AI said (it must never claim an action) —")
for heard, said in [("in my to-do list, take water the plant",
                     "Got it, I'll take care of that. Watering the plant sounds like a good plan for today."),
                    ("Take water plans", "I'm sorry, I can't assist with that."),
                    ("remind me to call mom", "Sure, I'll remind you to call mom at 5."),
                    ("add eggs", "Done! I've added eggs.")]:
    hear.PENDING = None
    new, why = hear.check_reply(heard, said, None)
    check(f'"{said[:45]}…" replaced with the truth', why and "didn't change anything" in new, new)
    print(f"      → {new}")
for heard, said in [("how are you", "I'm doing well, thanks for asking! How about you?"),
                    ("tell me a fun fact", "Octopuses have three hearts.")]:
    new, why = hear.check_reply(heard, said, None)
    check(f'normal chat kept: "{said[:40]}"', not why and new == said, new)

print("\n— Fix 90: the 28 Oct evening log —")
r = run(". Pick off water the plants.")
check('". Pick off water the plants." asks: tick off water the plants?', "tick off water the plants" in r, r)
r = run("I said no")
check('"I said no" = no, nothing changed', "didn't change anything" in r, r)
r = run("tick, pay rent")
check('"tick, pay rent" (comma) ticks pay rent', r == "→ tick pay rent", r)
r = run("tick, pay rent, tick, pay rent.")
check('"tick, pay rent, tick, pay rent." ticks pay rent once', r == "→ tick pay rent", r)
r = run("to-do list")
check('"to-do list" alone reads it', r == "→ what's on my to-do list", r)
r = run("Jarvis, when will it reign next?")
check('"when will it reign next" = rain', "rain next" in r, r)
r = run("Remove the mother's name from your memory")
check('"Remove the mother\'s name from your memory" forgets it', r == "→ forget my mother's name", r)
r = run("Delete my mother's name from your memory, remove it.")
check('"Delete my mother\'s name from your memory, remove it." forgets it', r == "→ forget my mother's name", r)
r = hear.process("It is wrong, forget that, forget it", None, "Your mother is Say.")
r = r.get("say") or "→ " + r["text"]
check('"It is wrong, forget that" after "Your mother is Say." forgets it', r == "→ forget my mother's name", r)
r = hear.process("and the name is not safe, forget that", None, "I have 1 thing saved. My mother's name is Say.")
r = r.get("say") or "→ " + r["text"]
check('"the name is not safe, forget that" forgets it too', r == "→ forget my mother's name", r)
for heard, said in [("tick, pay rent", "Got it."), ("tick, pay rent.", "Got it. I'll help you with that."),
                    ("tick, pay rent, tick, pay rent.", "Got it. I'll make sure to pay the rent tonight."),
                    ("It is wrong, forget that, forget it", "Understood. Let's try again.")]:
    hear.PENDING = None
    new, why = hear.check_reply(heard, said, None)
    check(f'"{said}" replaced with the truth', bool(why) and "didn't change anything" in new, new)
new, why = hear.check_reply("how are you", "Got it.", None)
check('"Got it." in normal chat is kept', not why, new)

print("\n— Fix 91: the 28 Oct night log —")
class _Mem:                     # a pretend settings store, so your real memory is never touched
    s = {}
    def get_setting(self, k): return self.s.get(k)
    def set_setting(self, k, v): self.s[k] = v
mem = _Mem()
TASKS[:] = ["pay rent", "water the plants"]
def run2(text, said=""):
    r = hear.process(text, mem, said)
    return r.get("say") or "→ " + r["text"]
hear.PENDING = None
for line in ["Pick on the parents, pick on the parents", ". Pick parent from my to-do list.",
             "Jarvis, can you pick parent from my to-do list?", "and then, pick, pay rent from my to-do list.",
             "and pick off pay rent from mine to-do list."]:
    hear.PENDING = None
    r = run2(line)
    check(f'"{line}" -> tick off pay rent?', "tick off pay rent" in r, r)
r = run2("tick the task, parent.")
check('"tick the task, parent." ticks pay rent', r == "→ tick pay rent", r)
run2(". Pick parent from my to-do list.")
r = run2("- Jarvis, yes. - Yes.")
check('"- Jarvis, yes. - Yes." counts as yes', r == "→ tick pay rent", r)
r = run2("pick, pay rent.")
check('...and next time "pick, pay rent." just ticks (learned)', r == "→ tick pay rent", r)
r = run2("which words mean tick?")
check('"which words mean tick?" shows what it learned', "pick" in r, r)
r = run2("forget pick means tick")
check('"forget pick means tick" undoes it', "ask first again" in r, r)
r = run2("pick, pay rent.")
check('...and then it asks again', "tick off pay rent" in r, r)
hear.PENDING = None
check('"what is done and what is planned for later" is understood', bool(hear.DONE_LATER.search(
      "I am asking what is done and what is planned for later")))
check('"what\'s planned for this weekend" is left for the schedule', not hear.DONE_LATER.search("what's planned for this weekend"))
for heard, said in [("- Jarvis, yes. - Yes.", "Okay, I'll look it up for you."),
                    ("and notice plant for later.", "Sure, I'll keep an eye on that plant for you."),
                    ("Jarvis, can you pick parent from my to-do list?",
                     "I'm sorry, I can't do that. You can add or remove items from your to-do list.")]:
    hear.PENDING = None
    new, why = hear.check_reply(heard, said, mem)
    check(f'"{said[:50]}" replaced', bool(why), new)
import lists
check('"list," can\'t add "grocery items for the week"', not lists._said("grocery items for the week", "list,"))
check('"pick, pay rent" can\'t add "Pick up rent payment"',
      not lists._said("Pick up rent payment", "and then, pick, pay rent from my to-do list."))
check('"add two litres of milk" can add "2 litres of milk"', lists._said("2 litres of milk", "add two litres of milk"))

print("\n— Rain: when will it rain next? —")
import environment as env  # noqa: E402
now = datetime(2026, 10, 28, 22, 0)
times = [(now + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(0, 168)]
def data(probs):
    return {"label": "Hardoi", "offset": 0, "wx": {"hourly": {"time": times, "precipitation_probability": probs,
                                                            "precipitation": [0] * 168}}}
env._local_now = lambda d: now
check('"when will the rain happen again?" is a rain question', bool(env.RAIN_Q.search("when will the rain happen again")))
a = env.rain_answer(data([5] * 72 + [70] * 6 + [5] * 90), "when will the rain happen again")
check("rain on Saturday: says Saturday", "next likely rain is on Saturday" in a, a)
print("      →", a)
a = env.rain_answer(data([5] * 100 + [25] * 3 + [5] * 65), "when will the rain happen again")
check("no rain all week: says so, with the best chance", "No rain is expected in the next 7 days" in a, a)
print("      →", a)
print(f"\n{ok} passed, {bad} failed")