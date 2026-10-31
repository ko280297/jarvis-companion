"""Fix 115 check: speaking practice (time, pace, filler words), including where it could go wrong.
   jarvis speaking_check.py      No microphone, no AI: pretend sound is made here. Nothing is saved anywhere."""
import os
import random
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import speaking as S  # noqa: E402

random.seed(2)
ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


def sound(silence_before, speech, silence_after, rate=16000):
    """Pretend recording: quiet room, then a voice, then quiet."""
    rng = np.random.default_rng(0)
    q = lambda s: rng.normal(0, 0.003, int(s * rate))
    v = rng.normal(0, 0.15, int(speech * rate))
    return np.concatenate([q(silence_before), v, q(silence_after)]).astype(np.float32)


print("— Timing the sound (only the voice counts, not the silence around it) —")
t = S.voiced_seconds(sound(1.0, 30.0, 4.0))
check("30 s of voice with silence around it -> about 30 s", 29.5 <= t <= 30.5, t)
t = S.voiced_seconds(sound(0.5, 8.0, 4.0))
check("8 s of voice -> about 8 s", 7.6 <= t <= 8.4, t)
t = S.voiced_seconds(sound(0.0, 20.0, 0.0))
check("voice from start to end, no silence at all -> about 20 s", 19.6 <= t <= 20.4, t)
quiet_voice = sound(1.0, 10.0, 4.0) * 0.15
t = S.voiced_seconds(quiet_voice)
check("a soft voice (far from the mic) is still timed", 9.5 <= t <= 10.5, t)
check("silence only -> 0", S.voiced_seconds(sound(3, 0, 3)) == 0.0, S.voiced_seconds(sound(3, 0, 3)))
check("no sound at all (typed) -> 0", S.voiced_seconds(np.zeros(48000, dtype=np.float32)) == 0.0)

print("\n— Filler words (and the normal words that look like them) —")
f = S.count_fillers("Um, so, I basically worked on, like, a data pipeline, you know, um, for sales.")
check('"um ×2, basically, like, you know" are counted', f.get("um") == 2 and f.get("basically") == 1
      and f.get("like") == 1 and f.get("you know") == 1, f)
f = S.count_fillers("I like Python. You know what, I mean it. I would like to join. It was kind of a big project.")
check('"I like Python", "you know what", "I mean it", "kind of a" are NOT fillers', not f, f)
f = S.count_fillers("मतलब, मैंने एक प्रोजेक्ट बनाया, यानी डेटा का।", hindi=True)
check("Hindi: मतलब and यानी are counted", f.get("मतलब") == 1 and f.get("यानी") == 1, f)

print("\n— The numbers Jarvis says —")
ans = " ".join(["word"] * 70)
r = S.feedback(ans, 30)
check("70 words in 30 s = 140 words a minute: a calm pace", "140 words a minute" in r and "calm" in r, r)
r = S.feedback(" ".join(["word"] * 100), 30)
check("200 a minute: a bit fast", "a bit fast" in r, r)
r = S.feedback(" ".join(["word"] * 40), 30)
check("80 a minute: a bit slow", "a bit slow" in r, r)
r = S.feedback(ans, 75)
check("75 s is said as 1 minute 15 seconds", "1 minute 15 seconds" in r, r)
r = S.feedback("yes I think so", 2)
check("a very short answer: no numbers, asks to try again", "very short" in r, r)
r = S.feedback("Um I worked on a data pipeline for sales", 0)
check("typed (no sound): says it can't time it, still counts fillers", "can't time it" in r and "1" in r, r)
check("no judgement of what you said (only numbers)", not any(w in r.lower() for w in ("good answer", "bad", "wrong")))

print("\n— A practice session —")
r = S.handle("interview practice")
check('"interview practice" asks an interview question', r and "Here's the question:" in r and "Nothing is recorded" in r, r)
check("…and waits for a long answer", S.waiting_answer())
q1 = S._s["q"]
r = S.handle(ans, seconds=30)
check("the answer gets only the numbers", r and "140 words a minute" in r, r)
check("…and nothing of what you said is kept", "word word" not in str(S._s))
r = S.handle("same question again")
check('"same question again" repeats it', r and q1 in r, r)
S.handle(ans, seconds=30)
r = S.handle("another question")
check('"another question" gives a different one', r and q1 not in r, r)
r = S.handle("practise with my own question: why did you leave your last job")
check("your own question works", r and "Why did you leave your last job?" in r, r)
r = S.handle("stop practice")
check('"stop practice" ends it', r and not S.active(), r)
r = S.handle("let's practise for my viva")
check('"let\'s practise for my viva" asks a viva question', r and "viva" in r, r)
r = S.handle("I have chest pain")
check("an emergency in the middle: practice steps aside (emergency help comes first)", r is None and not S.active(), r)
r = S.handle("practise my presentation")
check('"practise my presentation": start whenever ready', r and "Start your presentation" in r, r)
S.handle(ans, seconds=30)
r = S.handle("what's the weather")
check("after the numbers, anything else is answered normally", r is None and not S.active(), r)

S.handle("interview practice")
r = S.handle("what's the weather today", seconds=2.0)
check("waiting for an answer, but \"what's the weather today\" comes: not taken as an answer", r is None and not S.active(), r)
S.handle("interview practice")
r = S.handle("Let's see, I think my biggest strength is that I stay calm and finish what I start", seconds=6.0)
check("…but a real answer that starts with \"let's see\" is counted", r and "words a minute" in r, r)
print("\n— Sentences that must NOT start practice —")
for said in ["remind me to practise for my interview at 6", "my interview went well", "how was the presentation",
             "I don't want interview practice", "add interview prep to my to-do list", "practise yoga"]:
    r = S.handle(said)
    check(f'"{said}" is left alone', r is None and not S.active(), r)
S.handle("interview practice")
S._s["at"] = time.time() - 700
check("practice left alone for 10 minutes is over", not S.active())
print(f"\n{ok} passed, {bad} failed")
