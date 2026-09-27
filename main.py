import time
import numpy as np
import requests
import sounddevice as sd
import openwakeword
from openwakeword.model import Model as WakeModel
from pywhispercpp.model import Model as SttModel
from tts import speak
from memory import MemoryStore
from datetime import date, timedelta
from tools import answer_time_question
from online import answer_online_question

SAMPLE_RATE = 16000
CHUNK = 1280            # 80 ms
RECORD_SECONDS = 5
WAKE_WORD = "hey_jarvis"   # ya "hey_mycroft"
WAKE_THRESHOLD = 0.5

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"
SYSTEM_PROMPT = (
    "You are a private voice assistant running fully offline on a small device. "
    "Answer in one or two short sentences. "
    "If the user's saved memories answer the question, use them. "
    "If the user asks about their own life and it is not in their memories, "
    "say you don't have that saved. "
    "If you are not sure about something, say you don't know instead of guessing."
)


def wait_for_wake_word_and_record(wake):
    """Listen for the wake word, then record the question from the same mic stream."""
    wake.reset()
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=CHUNK) as stream:
        print("\n💤 Waiting for wake word...")
        while True:
            frame, _ = stream.read(CHUNK)
            scores = wake.predict(frame.flatten())
            if max(scores.values()) > WAKE_THRESHOLD:
                break

        print("👂 Listening...")
        frames = []
        for _ in range(int(RECORD_SECONDS * SAMPLE_RATE / CHUNK)):
            frame, _ = stream.read(CHUNK)
            frames.append(frame.flatten())

    return np.concatenate(frames).astype(np.float32) / 32768.0


def transcribe(stt, audio):
    segments = stt.transcribe(audio)
    return " ".join(s.text.strip() for s in segments).strip()

def upcoming_days(n=14):
    """Python does the date maths, so the LLM only has to look things up."""
    today = date.today()
    lines = []
    for d in range(n):
        day = today + timedelta(days=d)
        label = " (today)" if d == 0 else " (tomorrow)" if d == 1 else ""
        lines.append(day.strftime("%A, %d %B %Y") + label)
    return "\n".join(lines)
  
def think(messages):
    response = requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0},
    }, timeout=120)
    response.raise_for_status()
    return response.json()["message"]["content"].strip()


def handle_command(text, memory, history):
    """Handle remember / forget / list commands. Returns True if handled."""
    lower = text.lower().strip(" .!?,")

    if lower.startswith("remember"):
        fact = text[len("remember"):].strip(" ,.")
        if fact.lower().startswith("that "):
            fact = fact[5:]
        if not fact:
            speak("What should I remember?")
            return True
        fact = f"{fact} (saved on {date.today():%A, %d %B %Y})"
        memory.add(fact)
        print(f"💾 Saved: {fact}")
        speak("Okay, I'll remember that.")
        return True

    if lower.startswith("forget that") or lower == "forget it":
        removed = memory.forget_last()
        del history[1:]          # also wipe chat history so it truly forgets
        if removed:
            print(f"🗑️ Forgot: {removed}")
            speak("Done, I've forgotten it.")
        else:
            speak("There's nothing to forget.")
        return True

    if ("remember" in lower or "saved" in lower) and lower.startswith(("what", "tell me", "list")):
        items = memory.all()
        if not items:
            speak("I don't have anything saved about you.")
        else:
            print("📋 Memories:\n" + "\n".join(f"- {t}" for t in items))
            speak(f"I have {len(items)} things saved. " + ". ".join(items))
        return True

    return False


def main():
    print("Loading models...")
    openwakeword.utils.download_models()
    wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    stt = SttModel("base.en")
    memory = MemoryStore()
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Ready!")

    while True:
        audio = wait_for_wake_word_and_record(wake)
        text = transcribe(stt, audio)
        if not text or text.startswith("[") or text.startswith("("):
            print("Didn't catch anything.")
            continue
        print(f"You: {text}")

        if handle_command(text, memory, history):
            continue
        tool_answer = answer_time_question(text)
        if tool_answer:
            print(f"🕐 {tool_answer}")
            speak(tool_answer)
            continue
        online_answer = answer_online_question(text)
        if online_answer:
            print(f"AI:  {online_answer}")
            speak(online_answer)
            continue
        found = memory.search(text)
        messages = list(history)
        now = time.strftime("%A, %d %B %Y, %I:%M %p")
        messages.append({"role": "system", "content":
            f"Current local date and time: {now}\n"
            f"'Next <day>' means the first upcoming date with that name in this calendar:\n"
            f"{upcoming_days()}"})
        if found:
            facts = "\n".join(f"- {t}" for _, t in found)
            print(f"📚 Using memory:\n{facts}")
            messages.append({"role": "system",
                             "content": f"The user's saved memories:\n{facts}"})
        messages.append({"role": "user", "content": text})

        start = time.time()
        reply = think(messages)
        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        history = history[:1] + history[-6:]   # keep only recent turns, stays fast
        print(f"AI:  {reply}   ({time.time() - start:.1f}s)")
        speak(reply)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")