import numpy as np
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

# Jarvis's voices. The user can switch by saying "use a female voice" / "use a male voice".
VOICES = {
    "male": "en_US-ryan-medium"
}
DEFAULT_VOICE = "male"

# Tone presets. length_scale: >1 = slower, <1 = faster.
# noise_scale / noise_w_scale: higher = more expressive, lower = flatter and steadier.
TONES = {
    "calm":   SynthesisConfig(length_scale=1.0,  noise_scale=0.667, noise_w_scale=0.8),   # normal chat
    "gentle": SynthesisConfig(length_scale=1.3,  noise_scale=0.4,   noise_w_scale=0.5),   # not sure, sorry
    "urgent": SynthesisConfig(length_scale=0.8,  noise_scale=0.3,   noise_w_scale=0.4),   # emergency
    "bright": SynthesisConfig(length_scale=0.9,  noise_scale=0.9,   noise_w_scale=1.0),   # greetings, goodbye
}

# How the voice should pronounce tricky words (display text stays the same).
PRONOUNCE = {}

_loaded = {}            # voices are loaded once, when first used
_current = DEFAULT_VOICE


def set_voice(kind):
    """Switch between 'male' and 'female'. Returns True if it worked."""
    global _current
    if kind in VOICES:
        _current = kind
        return True
    return False


def _voice():
    if _current not in _loaded:
        _loaded[_current] = PiperVoice.load(f"{VOICES[_current]}.onnx")
    return _loaded[_current]


def speak(text, tone="calm"):
    for word, sound in PRONOUNCE.items():
        text = text.replace(word, sound)
    if tone == "gentle":
        text = text.replace(", ", "... ")      # small pauses sound softer and more thoughtful
    chunks = list(_voice().synthesize(text, syn_config=TONES.get(tone, TONES["calm"])))
    if not chunks:
        return
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    audio = np.concatenate([audio, np.zeros(int(0.4 * chunks[0].sample_rate), dtype=np.int16)])  # 0.4 s of silence so the end isn't cut off
    
    sd.play(audio, chunks[0].sample_rate)
    sd.wait()

   
if __name__ == "__main__":
    samples = {
        "calm":   "Your meeting with Rahul is on Friday, 2 October.",
        "gentle": "Hmm, I'm not sure about that one, and I'd rather not guess wrong.",
        "urgent": "Emergency. Call 112 now. Stay calm, help is on the way.",
        "bright": "Hi Krati! Great to hear from you. Talk soon!",
    }
    for kind in VOICES:
        set_voice(kind)
        for tone, line in samples.items():
            print(f"{kind} / {tone}")
            speak(line, tone)