"""Listen to the female voices side by side. Changes nothing in Jarvis.
   python try_voices.py"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import os, tts

LINE = "Hi Krati! Your meeting with Rahul is at 5 PM. The air is good today, so it's a nice time for a walk."
CANDIDATES = [
    ("1", "en_US-ryan-medium",       "male (now)"),
    ("2", "en_US-amy-medium",        "female: Amy (now)"),
    ("3", "en_US-lessac-medium",     "female: Lessac"),
    ("4", "en_US-hfc_female-medium", "female: HFC"),
]
for num, name, label in CANDIDATES:
    if not os.path.exists(name + ".onnx"):
        print(f"{num}. {label}: ⚠️ not downloaded, skipped")
        continue
    tts.VOICES["test"] = name
    tts._loaded.pop("test", None)
    tts.set_voice("test")
    for tone in ("calm", "bright"):
        print(f"🔊 {num}. {label}, {tone} tone")
        tts.speak(LINE, tone)
        time.sleep(0.4)
print("\nTell Claude the number you like best.")