"""Fix 112 check: interrupting Jarvis with "Hey Jarvis" while it speaks.   jarvis barge_check.py
No microphone, no speaker, no AI: a pretend microphone, a pretend wake-word model and a pretend speaker are used,
and your real memory is never opened. Every case where it could go wrong is tested, not only the happy one."""
import os
import sys
import threading
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ---- a pretend speaker and voice (so nothing is played) ----
PLAYED = {"seconds": 0.0, "abort": 0, "stop": 0}


class FakeOut:
    def __init__(self, samplerate, **k):
        self.rate = samplerate
    def start(self): pass
    def write(self, arr):
        PLAYED["seconds"] += len(arr) / self.rate
        time.sleep(len(arr) / self.rate / 4)            # 4x faster than real time
    def stop(self): PLAYED["stop"] += 1
    def abort(self): PLAYED["abort"] += 1
    def close(self): pass


class Chunk:
    sample_rate = 22050
    def __init__(self, secs):
        self.audio_int16_array = np.ones(int(secs * 22050), dtype=np.int16)


class FakeVoice:
    def synthesize(self, text, syn_config=None):
        for _ in range(4):                               # 4 sentences of 2 s = 8 s of speech
            yield Chunk(2.0)


sd = types.ModuleType("sounddevice")
sd.OutputStream, sd.stop, sd.PortAudioError = FakeOut, (lambda: None), Exception
piper = types.ModuleType("piper")
piper.PiperVoice, piper.SynthesisConfig = object, (lambda **k: None)
sys.modules["sounddevice"], sys.modules["piper"] = sd, piper
import tts  # noqa: E402
import barge  # noqa: E402
tts._loaded[tts._current] = FakeVoice()


# ---- a pretend microphone and wake-word model ----
class FakeWake:
    def __init__(self, scores_at):                       # [(from_second, to_second, score)]
        self.scores_at, self.t0, self.resets = scores_at, time.time(), 0
    def reset(self): self.resets += 1
    def predict(self, frame):
        t = time.time() - self.t0
        return {"hey_jarvis": next((s for a, b, s in self.scores_at if a <= t < b), 0.01)}


class FakeMic:
    opened = 0
    def __enter__(self):
        FakeMic.opened += 1
        return self
    def __exit__(self, *a): return False
    def read(self, n):
        time.sleep(0.02)                                 # frames come 4x faster too
        return np.zeros((n, 1), dtype=np.int16), False


class Settings:
    def __init__(self): self.s = {}
    def get_setting(self, k): return self.s.get(k)
    def set_setting(self, k, v): self.s[k] = v


ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


def say(text, wake, can_listen=True, opener=FakeMic, memory=None):
    """Like main.speak: listen for "Hey Jarvis" while speaking. Returns (seconds played, wall seconds)."""
    PLAYED.update(seconds=0.0, abort=0, stop=0)
    barge.take()
    barge.WAKE, barge.OPEN, barge.MEMORY = wake, opener, memory
    barge.CAN_LISTEN = lambda: can_listen
    wake.t0 = time.time()
    t = time.time()
    with barge.watch(text):
        tts.speak(text)
    return PLAYED["seconds"], time.time() - t


print("— The normal case —")
FakeMic.opened = 0
secs, wall = say("Here is a long answer about the weather this week.", FakeWake([(0.6, 0.7, 0.9)]))
check("“Hey Jarvis” (2 frames, sure) stops the voice early", secs < 4 and PLAYED["abort"] == 1, (secs, PLAYED))
check("…the rest is dropped at once (abort), not played to the end", PLAYED["stop"] == 0, PLAYED)
check("…and Jarvis knows it was interrupted", barge.interrupted())
check("the rest of the turn stays quiet (main.speak skips while interrupted)", barge.interrupted() is True)
check("the main loop takes it once, then it's reset", barge.take() and not barge.take() and not barge.STOP.is_set())

print("\n— Where it could go wrong —")
secs, _ = say("A long answer.", FakeWake([(0.6, 0.62, 0.95)]))
check("one loud frame (a cough, a door) does NOT stop Jarvis", secs >= 8 and not barge.interrupted(), secs)
secs, _ = say("A long answer.", FakeWake([(0.6, 1.0, 0.55)]))
check("a weak match (0.55, under 0.6) does NOT stop Jarvis", secs >= 8 and not barge.interrupted(), secs)
secs, _ = say("A long answer.", FakeWake([(0.0, 0.05, 0.99)]))
check("the speaker starting up (first 0.25 s) is ignored", secs >= 8 and not barge.interrupted(), secs)
FakeMic.opened = 0
secs, _ = say("To wake me, say: Hey Jarvis.", FakeWake([(0.3, 2.0, 0.99)]))
check("an answer that says “Jarvis” is never watched (it can't stop itself)",
      secs >= 8 and FakeMic.opened == 0 and not barge.interrupted(), (secs, FakeMic.opened))
secs, _ = say("कहिए: हे जार्विस।", FakeWake([(0.3, 2.0, 0.99)]))
check("…in Hindi too (जार्विस)", secs >= 8 and not barge.interrupted(), secs)
FakeMic.opened = 0
secs, _ = say("A long answer.", FakeWake([(0.6, 2.0, 0.99)]), can_listen=False)
check("mic muted / in a meeting: the mic is not even opened", secs >= 8 and FakeMic.opened == 0, FakeMic.opened)


def broken():
    raise OSError("no microphone")


secs, _ = say("A long answer.", FakeWake([(0.6, 2.0, 0.99)]), opener=broken)
check("the mic can't be opened: Jarvis just speaks normally (no crash)", secs >= 8 and PLAYED["stop"] == 1, secs)
mem = Settings()
mem.set_setting("interrupt", "off")
FakeMic.opened = 0
secs, _ = say("A long answer.", FakeWake([(0.6, 2.0, 0.99)]), memory=mem)
check("interrupting turned off: not watched", secs >= 8 and FakeMic.opened == 0, FakeMic.opened)
barge.WAKE = None
FakeMic.opened = 0
PLAYED.update(seconds=0.0)
with barge.watch("A long answer."):
    tts.speak("A long answer.")
check("Bluetooth mic / typing test (no wake model): not watched", FakeMic.opened == 0 and PLAYED["seconds"] >= 8)
w = FakeWake([(0.6, 0.7, 0.9)])
say("A long answer.", w)
barge.take()
check("the wake model is reset afterwards (the next “Hey Jarvis” starts clean)", w.resets >= 2, w.resets)
check("no listening thread is left running", not any(t.name.startswith("Thread") and t.is_alive() and t is not
                                                     threading.main_thread() for t in threading.enumerate()))

print("\n— Inside Jarvis: after an interruption, nothing more is said in that turn —")
for name in ["qrcode", "qrcode.image", "qrcode.image.svg", "openwakeword", "openwakeword.model", "pywhispercpp",
             "pywhispercpp.model"]:
    sys.modules.setdefault(name, types.ModuleType(name))
sys.modules["openwakeword.model"].Model = lambda *a, **k: None
sys.modules["pywhispercpp.model"].Model = lambda *a, **k: None
sd.play, sd.wait = (lambda *a, **k: None), (lambda *a, **k: None)
import main  # noqa: E402
spoken = []
main._tts_speak = lambda text, tone="calm": spoken.append(text)
barge.WAKE = None
main.speak("This one is spoken.")
check("normally main.speak speaks", spoken == ["This one is spoken."], spoken)
barge._state["pending"] = True
main.speak("This one must stay quiet.")
check("after “Hey Jarvis” over me, the rest of the answer is not spoken", spoken == ["This one is spoken."], spoken)
main._run_plan_steps([("say", "Breathe in slowly.", "gentle"), ("wait", 0.1), ("say", "And out.", "gentle")])
check("a breathing exercise stops quietly (no “we'll stop here” either)", spoken == ["This one is spoken."], spoken)
main.play_sequence(["red", "blue"], {"title": "game"})
check("the colour game stops too", spoken == ["This one is spoken."], spoken)
barge.take()
main.speak("After listening, I talk normally again.")
check("after the main loop listened, Jarvis talks normally again", spoken[-1].startswith("After listening"), spoken)
main.dashboard.STOP.clear()

print("\n— Edit options —")
mem = Settings()
for said, want, setting in [("don't let me interrupt you", "won't listen", "off"),
                            ("let me interrupt you", "say Hey Jarvis", "on"),
                            ("turn off interrupting", "won't listen", "off"),
                            ("turn on interruption", "say Hey Jarvis", "on")]:
    r = barge.command(said, mem)
    check(f'"{said}" -> {setting}', r and want in r and mem.get_setting("interrupt") == setting, r)
r = barge.command("how do I interrupt you?", mem)
check('"how do I interrupt you?" explains it', r and "Hey Jarvis" in r, r)
for said in ["let me interrupt you for a second, what's the time", "don't interrupt me", "I was interrupted at work",
             "stop", "let me tell you something"]:
    r = barge.command(said, mem)
    check(f'"{said}" is NOT an interrupt setting', r is None, r)
check('"don\'t interrupt me" leaves the setting alone', mem.get_setting("interrupt") == "on")
print(f"\n{ok} passed, {bad} failed")
