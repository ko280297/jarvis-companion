"""How much sooner does Jarvis start speaking with fix 63? Silent: nothing is played.
   python speed_test.py"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import tts

REPLIES = {
    "short": "It is 4:15 PM.",
    "medium": "Okay, I'll remind you: drink water, in 5 minutes. You also have a meeting at 6 PM.",
    "long": ("Good evening, Krati! You have 2 reminders left today: drink water at 6 PM, and call mom at 8 PM. "
             "On your to-do list: 3 tasks left, including pay rent. No rain expected today. "
             "The air is moderate, so a short walk is fine."),
}
for kind in ("male", "female"):
    tts.set_voice(kind)
    voice = tts._voice()
    list(voice.synthesize("Warm up.", syn_config=tts.TONES["calm"]))      # first use loads the model
    print(f"\n{kind} voice")
    for name, text in REPLIES.items():
        t0 = time.perf_counter()
        first = None
        for _ in voice.synthesize(text, syn_config=tts.TONES["calm"]):
            first = first or time.perf_counter() - t0
        whole = time.perf_counter() - t0
        print(f"  {name:6}  before: starts after {whole:.2f}s   now: starts after {first:.2f}s   "
              f"(saved {whole - first:.2f}s)")