import numpy as np
import sounddevice as sd
from piper import PiperVoice

voice = PiperVoice.load("en_US-lessac-medium.onnx")


def speak(text):
    chunks = list(voice.synthesize(text))
    if not chunks:
        return
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    sd.play(audio, chunks[0].sample_rate)
    sd.wait()


if __name__ == "__main__":
    speak("Hello! I am your private assistant, running fully offline.")