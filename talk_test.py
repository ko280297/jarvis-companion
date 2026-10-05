"""Test: speak -> transcribe (whisper.cpp) -> local LLM (Ollama) -> print reply.

Run:  python talk_test.py
Needs Ollama running in the background with qwen2.5:1.5b pulled.
"""
import time
import requests
import sounddevice as sd
from pywhispercpp.model import Model
from tts import speak

SAMPLE_RATE = 16000
SECONDS = 5
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"

SYSTEM_PROMPT = (
    "You are a private voice assistant running fully offline on a small device. "
    "Answer in one or two short sentences. "
    "If you are not sure about something, say you don't know instead of guessing."
)


def listen(stt):
    input("\nPress Enter, then speak for 5 seconds (Ctrl+C to quit)...")
    audio = sd.rec(int(SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE,
                   channels=1, dtype="float32")
    sd.wait()
    segments = stt.transcribe(audio.flatten())
    return " ".join(s.text.strip() for s in segments).strip()


def think(history):
    response = requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "messages": history,
        "stream": False,
        "keep_alive": "30m",
    }, timeout=120)
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def main():
    print("Loading speech-to-text model...")
    stt = Model("base.en")
    history = [{"role": "system", "content": SYSTEM_PROMPT}]

    while True:
        text = listen(stt)
        if not text or "[BLANK_AUDIO]" in text:
            print("Didn't catch anything, try again.")
            continue
        print(f"You: {text}")

        history.append({"role": "user", "content": text})
        start = time.time()
        reply = think(history)
        history.append({"role": "assistant", "content": reply})
        print(f"AI:  {reply}   ({time.time() - start:.1f}s)")
        speak(reply)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")