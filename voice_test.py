import numpy as np
import sounddevice as sd
from piper import PiperVoice

VOICES = ["en_GB-alan-medium", "en_US-ryan-medium", "en_US-joe-medium", "en_GB-cori-medium"]
LINE = "Hi Krati, I'm Jarvis. Your meeting with Rahul is on Friday. Shall I add anything to your list?"

for i, name in enumerate(VOICES, 1):
    print(f"{i}. {name}")
    voice = PiperVoice.load(f"{name}.onnx")
    chunks = list(voice.synthesize(LINE))
    audio = np.concatenate([c.audio_int16_array for c in chunks])
    sd.play(audio, chunks[0].sample_rate)
    sd.wait()