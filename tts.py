import numpy as np
import os, time   # fix 58
import sounddevice as sd
from piper import PiperVoice, SynthesisConfig

# Jarvis's voices. The user can switch by saying "use a female voice" / "use a male voice".
VOICES = {
    "male": "en_US-ryan-medium",
    "female": "en_US-amy-medium",   # fix 58
}
import os as _os                                                    # fix 61: brighter female voice first
for _f in ("en_US-lessac-medium", "en_US-hfc_female-medium", "en_US-amy-medium"):
    if _os.path.exists(_f + ".onnx"):
        VOICES["female"] = _f
        break
DEFAULT_VOICE = "male"
LAST_SYNTH = 0.0        # fix 58: seconds the last reply took to turn into sound
LAST_SYNTH_TOTAL = 0.0  # fix 63: the whole reply (LAST_SYNTH is now: until the first sound)

# Tone presets. length_scale: >1 = slower, <1 = faster.
# noise_scale / noise_w_scale: higher = more expressive, lower = flatter and steadier.
TONES = {
    "calm":   SynthesisConfig(length_scale=1.0,  noise_scale=0.667, noise_w_scale=0.8),   # normal chat
    "gentle": SynthesisConfig(length_scale=1.3,  noise_scale=0.4,   noise_w_scale=0.5),   # not sure, sorry
    "urgent": SynthesisConfig(length_scale=0.8,  noise_scale=0.3,   noise_w_scale=0.4),   # emergency
    "bright": SynthesisConfig(length_scale=0.9,  noise_scale=0.9,   noise_w_scale=1.0),   # greetings, goodbye
    "story":  SynthesisConfig(length_scale=1.3,  noise_scale=0.7,   noise_w_scale=0.8),   # fix 78: stories, slowly
}

# How the voice should pronounce tricky words (display text stays the same).
PRONOUNCE = {}

_loaded = {}            # voices are loaded once, when first used
_current = DEFAULT_VOICE


def set_voice(kind):
    """Switch between 'male' and 'female'. Returns True if it worked."""
    global _current
    if kind in VOICES and os.path.exists(f"{VOICES[kind]}.onnx"):   # fix 58: only if the file is there
        _current = kind
        return True
    return False


def _voice():
    if _current not in _loaded:
        _loaded[_current] = PiperVoice.load(f"{VOICES[_current]}.onnx")
    return _loaded[_current]

def speak(text, tone="calm"):
    text = __import__("re").sub(r"\bAQI\b", "A Q I", text)    # fix 90: say AQI as letters, not 'aki'
    for word, sound in PRONOUNCE.items():
        text = text.replace(word, sound)
    text = text.replace("; ", ". ")                # list items become separate sentences
    if tone == "gentle":
        text = text.replace(", ", "... ")          # small pauses sound softer and more thoughtful
    # fix 63 + fix 65: sentence 1 plays while Piper makes sentence 2, through ONE continuous sound stream
    # (opening a new stream per sentence clipped words on some laptops and Bluetooth speakers)
    global LAST_SYNTH, LAST_SYNTH_TOTAL
    import queue, threading
    _t0 = time.perf_counter()
    cfg = TONES.get(tone, TONES["calm"])
    voice = _voice()
    made = queue.Queue()

    def _make():                                          # Piper gives one chunk per sentence
        try:
            for c in voice.synthesize(text, syn_config=cfg):
                made.put(c)
        except Exception as e:                            # shown below, in the main thread
            made.put(e)
        made.put(None)

    threading.Thread(target=_make, daemon=True).start()
    out, rate = None, 22050
    try:
        while True:
            c = made.get()
            if c is None:
                break
            if isinstance(c, Exception):
                raise c
            if out is None:
                rate = c.sample_rate
                LAST_SYNTH = time.perf_counter() - _t0    # time until the first sound
                sd.stop()                                 # a chime still playing ends here
                out = sd.OutputStream(samplerate=rate, channels=1, dtype="int16")
                out.start()
                out.write(np.zeros((int(0.15 * rate), 1), dtype=np.int16))    # lead-in: first word not swallowed
            out.write(np.concatenate([c.audio_int16_array,
                                      np.zeros(int(0.3 * rate), dtype=np.int16)]).reshape(-1, 1))   # 0.3 s pause
        if out is not None:
            out.write(np.zeros((int(0.4 * rate), 1), dtype=np.int16))          # so the end isn't cut off
    finally:
        if out is not None:
            out.stop()                                    # waits until everything has been played
            out.close()
    LAST_SYNTH_TOTAL = time.perf_counter() - _t0

   
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