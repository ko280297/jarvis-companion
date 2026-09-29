import numpy as np
import sounddevice as sd
from piper import PiperVoice

voice = PiperVoice.load("en_US-lessac-medium.onnx")

# How the voice should pronounce tricky words (display text stays the same).
# Example: "Hardoi": "Hardoee"
PRONOUNCE = {}


def speak(text):
    for word, sound in PRONOUNCE.items():
        text = text.replace(word, sound)
    chunks = list(voice.synthesize(text))
    if not chunks:
        return
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    sd.play(audio, chunks[0].sample_rate)
    sd.wait()


if __name__ == "__main__":
    speak("Hi! I'm Nia, your private companion. Everything I think stays on this device.")