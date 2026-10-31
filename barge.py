"""Interrupt Jarvis while it speaks: say "Hey Jarvis" (fix 112, desk idea #17). On this device, nothing recorded.

While Jarvis talks, the microphone listens ONLY for the wake word (the same on-device model as always).
When it hears "Hey Jarvis", the voice stops at once (within about a tenth of a second), the rest of that answer
stays quiet (a long list, a story, an exercise), and Jarvis listens for what you want to say. Nothing is saved.

Safety checks (all code, no AI):
  - an answer that itself says "Jarvis" (like "say: Hey Jarvis") is never watched, so Jarvis can't stop itself
  - the wake word must be heard in 2 frames in a row, a bit more surely than usual (one loud noise isn't enough)
  - never while the mic is muted, in a meeting, with a Bluetooth mic (Whisper wake), or in the typing test
  - if the microphone can't be opened while speaking, Jarvis just speaks normally

Edit options:  "don't let me interrupt you" (off)   "let me interrupt you" (on)   "how do I interrupt you?"
"""
import re
import threading
import time

WAKE = None              # set by main.py: the openWakeWord model (None = interrupting is off)
OPEN = None              # set by main.py: opens the microphone -> a stream with .read(n) (and with-statement)
CAN_LISTEN = None        # set by main.py: -> True if the mic may be used right now (not muted, no meeting)
MEMORY = None            # set by main.py: for the on/off setting
CHUNK = 1280             # 80 ms at 16 kHz, like the wake-word loop
THRESHOLD = 0.6          # a little surer than the normal 0.5: Jarvis's own voice is in the room
FRAMES_NEEDED = 2        # in a row
SKIP_START = 0.25        # seconds: the first sound of the reply is not checked (the speaker starting up)

STOP = threading.Event()             # tts.py checks this between tiny pieces of sound
_state = {"pending": False, "score": 0.0, "at": 0.0}
SAYS_NAME = re.compile(r"jarvis|जार्विस|जारविस|जर्विस", re.I)

OFF = re.compile(r"\b(?:don'?t|do not|never)\s+let me interrupt you$|\bstop (?:letting me interrupt|listening while you"
                 r" (?:talk|speak))\b|\b(?:turn|switch) off (?:the )?interrupt(?:ing|ion)?\b|^interrupt(?:ing|ion)? off$",
                 re.I)
ON = re.compile(r"^(?:please\s+)?(?:you can )?let me interrupt you(?: again)?$|\b(?:turn|switch) on (?:the )?interrupt(?:ing|ion)?$"
                r"|^interrupt(?:ing|ion)? on$", re.I)
HOW = re.compile(r"\bhow (?:do|can) i (?:interrupt|stop) you\b|\bcan i interrupt you\b"
                 r"|\bhow (?:do|can) i stop you (?:while|when) you(?:'re| are)? (?:talking|speaking)\b", re.I)


def enabled():
    try:
        return MEMORY is None or (MEMORY.get_setting("interrupt") or "on") != "off"
    except Exception:
        return True


def interrupted():
    """True after you said "Hey Jarvis" over me, until the main loop has started listening (the rest stays quiet)."""
    return _state["pending"]


def take():
    """The main loop: was I interrupted? Then it listens for your request (and this resets everything)."""
    if not _state["pending"]:
        return False
    _state["pending"] = False
    STOP.clear()
    return True


class watch:
    """with barge.watch(text): ... speak ...   -> listens for "Hey Jarvis" while the text is spoken."""

    def __init__(self, text):
        self.text = text or ""
        self.thread = None
        self.run = threading.Event()

    def _ok(self):
        if WAKE is None or OPEN is None or not hasattr(WAKE, "predict") or not enabled():
            return False
        if SAYS_NAME.search(self.text):                      # it would hear its own "Jarvis"
            return False
        try:
            return CAN_LISTEN is None or bool(CAN_LISTEN())
        except Exception:
            return False

    def __enter__(self):
        STOP.clear()
        if self._ok():
            self.run.set()
            self.thread = threading.Thread(target=self._listen, daemon=True)
            self.thread.start()
        return self

    def _listen(self):
        try:
            WAKE.reset()
            started, hits = time.time(), 0
            with OPEN() as stream:
                while self.run.is_set():
                    frame, _ = stream.read(CHUNK)
                    frame = frame.flatten() if hasattr(frame, "flatten") else frame
                    if time.time() - started < SKIP_START:
                        continue
                    score = max(WAKE.predict(frame).values())
                    hits = hits + 1 if score > THRESHOLD else 0
                    if hits >= FRAMES_NEEDED:
                        _state.update(pending=True, score=score, at=time.time())
                        STOP.set()
                        try:
                            import dashboard
                            dashboard.STOP.set()                 # a story or an exercise stops too
                        except Exception:
                            pass
                        return
        except Exception as e:                                   # no mic now: Jarvis just speaks normally
            print(f"   (interrupting not available right now: {type(e).__name__})")
        finally:
            try:
                WAKE.reset()
            except Exception:
                pass

    def __exit__(self, *exc):
        self.run.clear()
        if self.thread is not None:
            self.thread.join(timeout=1.0)                        # the wake model is free again for the main loop
        return False


def command(text, memory):
    """The edit options. Returns what to say, or None."""
    low = (text or "").lower().strip(" .!?")
    if OFF.search(low):
        memory.set_setting("interrupt", "off")
        return "Okay, I won't listen while I'm talking. Say: let me interrupt you, to turn it back on."
    if ON.search(low):
        memory.set_setting("interrupt", "on")
        return "Okay. While I'm talking, just say Hey Jarvis, and I'll stop and listen."
    if HOW.search(low):
        on = (memory.get_setting("interrupt") or "on") != "off"
        return ("Just say Hey Jarvis while I'm talking. I'll stop at once and listen to you." if on else
                "Interrupting is off right now. Say: let me interrupt you, to turn it on.")
    return None
