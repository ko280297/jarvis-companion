"""Listen to the Hindi voice (rohan) in 4 styles and pick one.     .venv\\Scripts\\python.exe hindi_voice_try.py
Nothing changes in Jarvis: this only plays sound. Tell Claude the letter you liked (A, B, C or D).
  A = as now      B = a little livelier      C = livelier      D = a little livelier and a little faster"""
import sys
from pathlib import Path

import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

HERE = Path(__file__).resolve().parent
VOICE = HERE / "models_hi" / "hi_IN-rohan-medium.onnx"
TEXT = ("नमस्ते कृति! आज हरदोई में अभी 24 डिग्री है, बादल छाए हैं। "
        "ठीक है, मैं आपको याद दिलाऊँगा: मम्मी को फ़ोन करना, आज शाम 6 बजे।")
BASE = dict(length_scale=1.0, noise_scale=0.667, noise_w_scale=0.8)     # Jarvis's normal "calm" tone
STYLES = {"A": (1.0, 1.0), "B": (1.0, 1.2), "C": (1.0, 1.4), "D": (0.92, 1.2)}   # (speed, expression)

if not VOICE.exists():
    sys.exit("The Hindi voice isn't there. Run: .venv\\Scripts\\python.exe hindi_bench.py --setup")
voice = PiperVoice.load(str(VOICE))
for name, (speed, expr) in STYLES.items():
    cfg = SynthesisConfig(length_scale=BASE["length_scale"] * speed, noise_scale=BASE["noise_scale"] * expr,
                          noise_w_scale=BASE["noise_w_scale"] * expr)
    chunks = list(voice.synthesize(TEXT, syn_config=cfg))
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    input(f"\nStyle {name}  (speed {speed}, expression {expr}). Press Enter to listen...")
    sd.play(audio, chunks[0].sample_rate)
    sd.wait()
print("\nWhich one sounded most natural? Tell Claude: A, B, C or D. (You can run this again any time.)")
