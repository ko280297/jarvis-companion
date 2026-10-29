"""Listen to different spellings of your name, to find the one closest to "कृति" (त as in tabla).
Changes nothing.   python try_name.py"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import tts

CANDIDATES = ["Krati", "Kruthi", "Krithi", "Kruti", "Kruthee", "Krathi", "Krutthi", "Kri-thi"]
for kind in ("male", "female"):
    tts.set_voice(kind)
    print(f"\n--- {kind} voice ---")
    for i, name in enumerate(CANDIDATES, 1):
        print(f"🔊 {i}. {name}")
        tts.speak(f"Hi {name}, how are you?")
        time.sleep(0.4)
print("\nTell Claude the number (and voice) that sounds most like your name.")