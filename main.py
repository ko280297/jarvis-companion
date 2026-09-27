import re
import time
from datetime import date
import numpy as np
import requests
import sounddevice as sd
import openwakeword
import math
from openwakeword.model import Model as WakeModel
from pywhispercpp.model import Model as SttModel
from tts import speak
from memory import MemoryStore
from tools import answer_time_question
from online import answer_online_question

SAMPLE_RATE = 16000
CHUNK = 1280            # 80 ms
RECORD_SECONDS = 5
WAKE_WORD = "hey_jarvis"   # ya "hey_mycroft"
WAKE_THRESHOLD = 0.5
CONFIDENCE_THRESHOLD = 0.85   # tune after testing

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"
SYSTEM_PROMPT = (
    "You are a private voice assistant running fully offline on a small device. "
    "Answer in one or two short sentences. "
    "If the user's saved memories answer the question, use them. "
    "If the user asks about their own life and it is not in their memories, "
    "say you don't have that saved. "
    "If you are not sure about something, say you don't know instead of guessing. "
    "You cannot perform actions or change settings. "
    "Never say you saved, set, or changed anything."
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


def transcribe(stt, audio, memory):
    # Hint whisper with names the user has saved themselves (nothing hardcoded)
    words = set()
    for t in memory.all():
        t = t.split(" (saved on")[0]          # dates are not names, keep them out of the hint
        words.update(re.findall(r"\b[A-Z][a-z]+", t))
    city = memory.get_setting("home_city")
    if city:
        words.add(city)
    hint = ", ".join(sorted(words))
    segments = stt.transcribe(audio, initial_prompt=hint)
    return " ".join(s.text.strip() for s in segments).strip()


def think(messages):
    """Returns (reply, confidence). Confidence comes from the model's own token probabilities."""
    response = requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "30m",
        "logprobs": True,
        "options": {"temperature": 0, "num_predict": 80},
    }, timeout=120)
    if response.status_code != 200:
        print("⚠️ Ollama says:", response.text)
    response.raise_for_status()
    data = response.json()
    reply = data["message"]["content"].strip()
    lps = data.get("logprobs") or []
    confidence = math.exp(sum(t["logprob"] for t in lps) / len(lps)) if lps else 1.0
    return reply, confidence


def handle_command(text, memory, history):
    """Handle city / remember / forget / list commands. Returns True if handled."""
    lower = text.lower().strip(" .!?,")

    m = re.match(
        r"(?:my (?:current |home )?city is"
        r"|(?:set|change) my (?:current |home )?city (?:to|as)"
        r"|i live in)\s+(.+)", lower)
    if m:
        city = m.group(1).strip().title()
        memory.set_setting("home_city", city)
        print(f"🏠 Home city set: {city}")
        speak(f"Got it, your city is {city}.")
        return True

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
        speak(f"Okay, I'll remember: {fact.split(' (saved on')[0]}")
        return True
    if lower.startswith(("forget everything", "delete everything", "erase everything")):
        count = memory.forget_all()
        del history[1:]
        print(f"🧹 Erased {count} memories")
        speak(f"Done. I've erased all {count} memories.")
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

    if re.search(r"remember|saved|memor", lower) and lower.startswith(("what", "tell me", "list")):
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
        text = transcribe(stt, audio, memory)
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

        online_answer = answer_online_question(text, memory.get_setting("home_city"))
        if online_answer:
            print(f"AI:  {online_answer}")
            speak(online_answer)
            continue

        found = memory.search(text)
        messages = list(history)
        now = time.strftime("%A, %d %B %Y, %I:%M %p")
        messages.append({"role": "system", "content":
            f"Current local date and time (reference only, this is NOT the user's plans): {now}"})
        if found:
            facts = "\n".join(f"- {t}" for _, t in found)
            print(f"📚 Using memory:\n{facts}")
            messages.append({"role": "system",
                             "content": f"The user's saved memories:\n{facts}"})
        messages.append({"role": "user", "content": text})

        start = time.time()
        reply, confidence = think(messages)
        print(f"🤔 Confidence: {confidence:.2f}")

        # "I'm not sure" mode: low confidence on general knowledge = likely a guess
        if not found and confidence < CONFIDENCE_THRESHOLD:
            print(f"   (withheld guess: {reply})")
            reply = "I'm not sure about that, so I'd rather not guess."
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