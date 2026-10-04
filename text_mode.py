"""Type instead of speaking: runs the real Jarvis, with the microphone and speech-to-text
replaced by the keyboard. Great for quick regression tests of every feature.

  python text_mode.py                               type commands yourself
  python text_mode.py --speak                       ...and hear the replies
  python text_mode.py --fast < regression.txt       run a whole test script, no waiting
"""
import sys
import time

import numpy as np

import main

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")          # emojis also work when the output goes to a file

_typed = {"text": ""}


def fake_listen(wake, reminders, follow_up=False, max_seconds=8, silence=1.5, timeout=7, level=None):
    prompt = "📝 meeting> " if main.meeting_active() else "⌨️  You> "
    try:
        text = input("\n" + prompt).strip()
    except EOFError:                                 # end of a test script
        raise KeyboardInterrupt
    if not sys.stdin.isatty():
        print(text)                                  # show the line when it comes from a file
    if not text:
        return None
    _typed["text"] = text
    seconds = max(3.0, min(len(text.split()) * 0.4, max_seconds - 0.5))   # a believable recording length
    return np.zeros(int(seconds * main.SAMPLE_RATE), dtype=np.float32)


def fake_transcribe(stt, audio, memory):
    return _typed["text"]


main.listen = fake_listen
main.transcribe = fake_transcribe
main.ding = lambda: None
main.sleep_chime = lambda: None
main.MEETING_STT_MODEL = main.STT_MODEL              # no big speech model needed when typing
if "--speak" not in sys.argv:
    main.speak = lambda text, tone="calm": None      # print only, much faster
if "--fast" in sys.argv:
    time.sleep = lambda seconds: None                # breathing exercises etc. don't wait

print("⌨️  Text mode: type what you'd say. Ctrl + C to stop.")
print("   (timers and reminders are checked after each line you type)")
try:
    main.main()
except KeyboardInterrupt:
    print("\n✅ Test script finished.")