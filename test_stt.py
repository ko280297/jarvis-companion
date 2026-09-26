import time
import sounddevice as sd
from pywhispercpp.model import Model

SAMPLE_RATE = 16000
SECONDS = 5

print("Loading model...")
model = Model("base.en")

input("Press Enter, then speak for 5 seconds...")
audio = sd.rec(int(SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="float32")
sd.wait()

start = time.time()
segments = model.transcribe(audio.flatten())
text = " ".join(s.text.strip() for s in segments)
print(f"\nYou said: {text}")
print(f"Took {time.time() - start:.2f} seconds")