import sounddevice as sd
import openwakeword
from openwakeword.model import Model

openwakeword.utils.download_models()   # sirf pehli baar internet chahiye
model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

CHUNK = 1280   # 80 ms of audio at 16 kHz
print("Listening... say 'Hey Jarvis' (Ctrl+C to quit)")

with sd.InputStream(samplerate=16000, channels=1, dtype="int16", blocksize=CHUNK) as stream:
    while True:
        frame, _ = stream.read(CHUNK)
        scores = model.predict(frame.flatten())
        if any(score > 0.5 for score in scores.values()):
            print("Wake word detected! 🎉")
            model.reset()