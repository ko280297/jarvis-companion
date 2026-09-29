import json
import math
import re
import time
from collections import deque
from datetime import date

import numpy as np
import requests
import sounddevice as sd
import openwakeword
from openwakeword.model import Model as WakeModel
from pywhispercpp.model import Model as SttModel

import dashboard
from tts import speak
from memory import MemoryStore, _embed
from tools import answer_time_question, resolve_dates
from online import answer_online_question, run_tool_call

# ---------- Audio ----------
SAMPLE_RATE = 16000
CHUNK = 1280                # 80 ms
MAX_RECORD_SECONDS = 8      # longest question allowed
SILENCE_TO_STOP = 1.0       # seconds of quiet that ends the question
NO_SPEECH_TIMEOUT = 3.0     # give up if nothing is said
WAKE_WORD = "hey_jarvis"    # or "hey_mycroft"
WAKE_THRESHOLD = 0.5

# ---------- Reasoning ----------
CONFIDENCE_THRESHOLD = 0.85   # from testing: facts 0.9+, guesses below 0.75
GATE_THRESHOLD = 0.45         # below this: clearly offline, skip the router
STRONG_GATE = 0.70            # above this: trust the gate even if the router hesitates

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"
SYSTEM_PROMPT = (
    "You are a private voice assistant. All your thinking happens on this device. "
    "Answer in one or two short sentences. "
    "If a memory contains [date: ...], use exactly that date and never calculate dates yourself. "
    "If the user's saved memories answer the question, use them. "
    "If the user asks about their own life and it is not in their memories, "
    "say you don't have that saved. "
    "If you are not sure about something, say you don't know instead of guessing. "
    "You cannot change settings. Never claim you saved, set, or changed anything."
)

TOOLS = [
    {"type": "function", "function": {
        "name": "get_weather",
        "description": "Get the current weather or tomorrow's forecast. "
                       "Use when the user asks about weather, temperature, hot, cold, rain, "
                       "an umbrella, or what to wear outside. "
                       "If no city is mentioned, still call it with an empty city: "
                       "the device already knows the user's city.",
        "parameters": {"type": "object", "properties": {
            "city": {"type": "string", "description": "City name only, e.g. Pune. Leave empty if not mentioned."},
            "day": {"type": "string", "enum": ["today", "tomorrow"]},
        }},
    }},
    {"type": "function", "function": {
        "name": "get_news",
        "description": "Get today's top world news headlines. Use ONLY when the user asks for news.",
        "parameters": {"type": "object", "properties": {}},
    }},
]

# Examples that define what "needs the internet" means. The gate compares meaning, not keywords.
ONLINE_EXAMPLES = [
    ("What's the weather like today?", "get_weather"),
    ("Will it rain tomorrow?", "get_weather"),
    ("Is it hot or cold outside?", "get_weather"),
    ("Do I need an umbrella?", "get_weather"),
    ("What should I wear outside today?", "get_weather"),
    ("What's the latest news?", "get_news"),
    ("What is happening in the world today?", "get_news"),
    ("Tell me today's headlines.", "get_news"),
]


# ---------- Output ----------
def say(text, route, confidence=None):
    """Speak a reply and show it on the dashboard."""
    dashboard.update(status="speaking", last_reply=text, route=route, confidence=confidence)
    speak(text)


# ---------- Listening ----------
def ding():
    """Short soft beep so the user knows when to start speaking."""
    rate = 22050
    t = np.linspace(0, 0.15, int(0.15 * rate), False)
    tone = 0.3 * np.sin(2 * np.pi * 880 * t) * np.linspace(1, 0, t.size)
    sd.play(tone.astype(np.float32), rate)


def rms(frame):
    """Loudness of one audio chunk."""
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def wait_for_wake_word_and_record(wake):
    """Wait for the wake word, then record until the user stops talking."""
    wake.reset()
    noise = deque(maxlen=50)    # background loudness over the last ~4 seconds
    chunk_sec = CHUNK / SAMPLE_RATE

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=CHUNK) as stream:
        print("\n💤 Waiting for wake word...")
        dashboard.update(status="waiting")
        while True:
            frame, _ = stream.read(CHUNK)
            frame = frame.flatten()
            noise.append(rms(frame))
            if max(wake.predict(frame).values()) > WAKE_THRESHOLD:
                break

        ding()
        for _ in range(3):              # skip the ding itself (~240 ms)
            stream.read(CHUNK)

        threshold = max(3 * float(np.median(noise)), 300)   # learned from the room
        print("👂 Listening...")
        dashboard.update(status="listening")

        frames, heard_speech, quiet = [], False, 0.0
        for _ in range(int(MAX_RECORD_SECONDS / chunk_sec)):
            frame, _ = stream.read(CHUNK)
            frame = frame.flatten()
            frames.append(frame)
            if rms(frame) > threshold:
                heard_speech, quiet = True, 0.0
            else:
                quiet += chunk_sec
            if heard_speech and quiet >= SILENCE_TO_STOP:
                break
            if not heard_speech and len(frames) * chunk_sec >= NO_SPEECH_TIMEOUT:
                break

    print(f"   (recorded {len(frames) * chunk_sec:.1f}s)")
    return np.concatenate(frames).astype(np.float32) / 32768.0


def transcribe(stt, audio, memory):
    # Hint whisper with names the user has saved themselves (nothing hardcoded)
    words = set()
    for t in memory.all():
        t = re.sub(r"\[date:.*?\]", "", t.split(" (saved on")[0])   # dates are not names
        words.update(re.findall(r"\b[A-Z][a-z]+", t))
    city = memory.get_setting("home_city")
    if city:
        words.add(city)
    hint = ", ".join(sorted(words))
    segments = stt.transcribe(audio, initial_prompt=hint)
    return " ".join(s.text.strip() for s in segments).strip()


# ---------- Thinking ----------
def gate(text, example_vecs):
    """Returns (similarity to 'needs the internet' examples, which tool it looks like)."""
    sims = example_vecs @ _embed(text)
    i = int(np.argmax(sims))
    return float(sims[i]), ONLINE_EXAMPLES[i][1]


def route(text):
    """Router: sees ONLY the question (no memories), so it can never leak personal data into a tool.
    Streams the reply and stops as soon as the model starts writing text (= no tool needed)."""
    with requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "stream": True,
        "keep_alive": "30m",
        "tools": TOOLS,
        "messages": [{"role": "user", "content": text}],
        "options": {"temperature": 0, "num_predict": 40},
    }, stream=True, timeout=60) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            msg = chunk.get("message", {})
            if msg.get("tool_calls"):
                return msg["tool_calls"][0]["function"]
            if msg.get("content", "").strip():
                return None      # started answering in words: no tool needed
            if chunk.get("done"):
                return None
    return None


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
    reply = (data["message"].get("content") or "").strip()
    lps = data.get("logprobs") or []
    confidence = math.exp(sum(t["logprob"] for t in lps) / len(lps)) if lps else 1.0
    return reply, confidence


# ---------- Commands ----------
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
        say(f"Got it, your city is {city}.", "⚙️ setting saved on device")
        return True

    if lower.startswith("remember"):
        fact = text[len("remember"):].strip(" ,.")
        if fact.lower().startswith("that "):
            fact = fact[5:]
        if not fact:
            say("What should I remember?", "💾 memory")
            return True
        fact = resolve_dates(fact)                                   # 1. real date first
        fact = f"{fact} (saved on {date.today():%A, %d %B %Y})"     # 2. then when it was saved
        memory.add(fact)
        print(f"💾 Saved: {fact}")
        say("Okay, I'll remember that.", "💾 saved to memory")
        return True

    if lower.startswith(("forget everything", "delete everything", "erase everything")):
        count = memory.forget_all()
        del history[1:]
        print(f"🧹 Erased {count} memories")
        say(f"Done. I've erased all {count} memories.", "🧹 all memories erased")
        return True

    if lower.startswith("forget that") or lower == "forget it":
        removed = memory.forget_last()
        del history[1:]          # also wipe chat history so it truly forgets
        if removed:
            print(f"🗑️ Forgot: {removed}")
            say("Done, I've forgotten it.", "🗑️ memory deleted")
        else:
            say("There's nothing to forget.", "🗑️ memory")
        return True

    if re.search(r"remember|saved|memor", lower) and lower.startswith(("what", "tell me", "list")):
        items = memory.all()
        if not items:
            say("I don't have anything saved about you.", "📋 memory list")
        else:
            print("📋 Memories:\n" + "\n".join(f"- {t}" for t in items))
            say(f"I have {len(items)} things saved. " + ". ".join(items), "📋 memory list")
        return True

    return False


# ---------- Main loop ----------
def main():
    dashboard.start()
    print("Loading models...")
    openwakeword.utils.download_models()
    wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    stt = SttModel("base.en")
    memory = MemoryStore()
    example_vecs = np.stack([_embed(e) for e, _ in ONLINE_EXAMPLES])
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    print("Ready!")

    while True:
        dashboard.update(memories=len(memory.all()))
        audio = wait_for_wake_word_and_record(wake)
        dashboard.update(status="thinking")
        text = transcribe(stt, audio, memory)
        if not text or text.startswith("[") or text.startswith("("):
            print("Didn't catch anything.")
            continue
        print(f"You: {text}")
        dashboard.update(last_heard=text, last_reply="", route="", confidence=None)

        if handle_command(text, memory, history):
            continue

        tool_answer = answer_time_question(text)
        if tool_answer:
            print(f"🕐 {tool_answer}")
            say(tool_answer, "🕐 device clock (offline)")
            continue

        online_answer = answer_online_question(text, memory.get_setting("home_city"))
        if online_answer:
            print(f"AI:  {online_answer}")
            say(online_answer, "🌐 internet (rule)")
            continue

        found = memory.search(text)
        score, likely_tool = gate(text, example_vecs)
        print(f"🚦 Gate: {score:.2f} ({likely_tool})")
        if not found and score >= GATE_THRESHOLD:
            t0 = time.time()
            call = route(text)
            if call is None and score >= STRONG_GATE:
                call = {"name": likely_tool, "arguments": {}}
                print(f"🚦 Router hesitated, gate is confident → {likely_tool}")
            print(f"🧭 Router: {call['name'] + ' ' + str(call.get('arguments')) if call else 'no tool'}  ({time.time() - t0:.1f}s)")
            if call:
                args = call.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                tool_reply = run_tool_call(call["name"], args, memory.get_setting("home_city"))
                if tool_reply:
                    print(f"AI:  {tool_reply}")
                    say(tool_reply, "🌐 internet (LLM router)")
                    continue

        messages = list(history)
        if found:
            facts = "\n".join(f"- {t}" for _, t in found)
            print(f"📚 Using memory:\n{facts}")
            messages.append({"role": "system",
                             "content": f"The user's saved memories:\n{facts}"})
        messages.append({"role": "user", "content": text})

        start = time.time()
        reply, confidence = think(messages)
        print(f"🤔 Confidence: {confidence:.2f}")
        route_name = "📚 memory (on device)" if found else "🧠 local LLM"

        # "I'm not sure" mode: low confidence on general knowledge = likely a guess
        declined = re.search(r"don't (know|have)|not sure|no access", reply.lower())
        is_question = text.strip().endswith("?") or re.match(
            r"(what|who|when|where|why|how|which|is|are|do|does|did|can|tell me)\b", text.lower())
        if not found and not declined and is_question and confidence < CONFIDENCE_THRESHOLD:
            print(f"   (withheld guess: {reply})")
            reply = "I'm not sure about that, so I'd rather not guess."
            route_name = "🤔 not sure (guess withheld)"

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        history = history[:1] + history[-6:]   # keep only recent turns, stays fast
        print(f"AI:  {reply}   ({time.time() - start:.1f}s)")
        say(reply, route_name, round(confidence, 2))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")