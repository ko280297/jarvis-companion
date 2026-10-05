"""Type instead of speaking: runs the real Jarvis, with the microphone and speech-to-text
replaced by the keyboard. Also a small test runner for scenario files.

  python text_mode.py                                 type commands yourself
  python text_mode.py --speak                         ...and hear the replies
  python text_mode.py --fast < regression.txt         run a script, no waiting
                                                      (your real memories are backed up and put back after)

Script lines:
  what time is it || It is          the reply must match this pattern (regex, case-insensitive)
  I'm killing it || !hurt someone   the reply must NOT match this pattern
  # Section name                    a heading, not sent to Jarvis
"""
import gc
import re
import shutil
import sys
import time
from pathlib import Path

import numpy as np

import main

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")          # emojis also work when the output goes to a file

DB = Path(main.__file__).with_name("memories.db")
BACKUP = DB.with_name("memories.db.testbackup")
SANDBOX = "--fast" in sys.argv                     # a test script must never change the real memories
if SANDBOX and DB.exists():
    shutil.copy(DB, BACKUP)
    print("   (real memories backed up; the test will not change them)")

_typed = {"text": ""}
_turn = {"line": None, "expect": None, "said": []}
_results = []                                      # (section, line, expect, passed, reply)
_section = {"name": ""}

# Record everything Jarvis says, so each line can be checked
_real_say = main.say


def _recording_say(text, route, confidence=None, tone="calm"):
    _turn["said"].append(text)
    _real_say(text, route, confidence, tone)


main.say = _recording_say


def _check_last_turn():
    expect = _turn["expect"]
    if not expect:
        return
    reply = " ".join(_turn["said"])
    negate = expect.startswith("!")
    pattern = expect[1:] if negate else expect
    found = re.search(pattern, reply, re.I) is not None
    passed = (not found) if negate else found
    _results.append((_section["name"], _turn["line"], expect, passed, reply))
    print(f"   {'✅ PASS' if passed else '❌ FAIL'}  (expected {'no ' if negate else ''}'{pattern}')")
    _turn["expect"] = None


def fake_listen(wake, reminders, follow_up=False, max_seconds=8, silence=1.5, timeout=7, level=None):
    _check_last_turn()
    prompt = "📝 meeting> " if main.meeting_active() else "⌨️  You> "
    while True:
        try:
            raw = input("\n" + prompt).strip()
        except EOFError:                           # end of a test script
            raise KeyboardInterrupt
        if raw.startswith("#"):                    # a section heading
            _section["name"] = raw.lstrip("# ").strip()
            print(f"\n========== {_section['name']} ==========")
            continue
        break
    text, _, expect = raw.partition("||")
    text, expect = text.strip(), expect.strip() or None
    if not sys.stdin.isatty():
        print(text)                                # show the line when it comes from a file
    if not text:
        return None
    _typed["text"] = text
    _turn.update(line=text, expect=expect, said=[])
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
    main._tts_speak = main.speak
if "--fast" in sys.argv:
    time.sleep = lambda seconds: None                # breathing exercises etc. don't wait

print("⌨️  Text mode: type what you'd say. Ctrl + C to stop.")
print("   (timers and reminders are checked after each line you type)")

try:
    main.main()
except KeyboardInterrupt:
    _check_last_turn()
    print("\n✅ Test script finished.")

if _results:
    passed = sum(1 for r in _results if r[3])
    print(f"\n📊 SCORE: {passed} / {len(_results)} passed")
    for section, line, expect, ok, reply in _results:
        if not ok:
            print(f"   ❌ [{section}] \"{line}\"  expected {expect!r}\n      got: {reply[:160]}")

if SANDBOX and BACKUP.exists():
    gc.collect()                                     # make sure the database is closed first
    shutil.copy(BACKUP, DB)
    BACKUP.unlink()
    print("   (your real memories were restored: the test changed nothing)")