import json
import math
import re
import time
import threading
from collections import deque
from datetime import date, datetime
from difflib import SequenceMatcher

import numpy as np
import requests
import sounddevice as sd
import openwakeword
from openwakeword.model import Model as WakeModel
from pywhispercpp.model import Model as SttModel

import dashboard
from tts import speak, set_voice
from memory import MemoryStore, _embed, embed_many
from tools import answer_time_question, resolve_dates
from online import answer_online_question, run_tool_call, get_weather
from lists import handle_list_command, run_list_tool, LIST_TOOLS, LIST_TOOL_NAMES, STARTER_LISTS
from reminders import Reminders, handle_reminder_command
from safety import is_unsafe, REFUSAL
from summary import SUMMARY_Q, build_summary

# ---------- Audio ----------
SAMPLE_RATE = 16000
CHUNK = 1280                # 80 ms
MAX_RECORD_SECONDS = 8      # longest question allowed
SILENCE_TO_STOP = 1.5     # seconds of quiet that ends the question
NO_SPEECH_TIMEOUT = 4.0     # after the wake word: give up if nothing is said
FOLLOW_UP_SECONDS = 7.0     # conversation mode: how long to wait for a reply without the wake word
WAKE_WORD = "hey_jarvis"    # or "hey_mycroft"
WAKE_THRESHOLD = 0.5
REMINDER_CHECK_EVERY = 12   # chunks (~1 second) between reminder checks while waiting
REMINDER_DUE = "reminder_due"
MAX_FOLLOW_UPS = 6  

# ---------- Reasoning ----------
CONFIDENCE_THRESHOLD = 0.85   # from testing: facts 0.9+, guesses below 0.75
GATE_THRESHOLD = 0.45         # below this: no tool needed, skip the router
STRONG_GATE = 0.70            # above this: trust the gate even if the router hesitates (online only)

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"

# ---------- Personality ----------
ASSISTANT_NAME = "Jarvis"
INTRO = (f"I'm {ASSISTANT_NAME}, your private companion. I live right here on this device, "
         "so everything you tell me stays with you.")
CAPABILITIES = ("I can remember things for you, keep lists like groceries, ideas and your schedule, "
                "set reminders and timers, give you a summary of your day, tell you the date and time, "
                "and check the weather or news. "
                "And if I'm not sure about something, I'll tell you instead of guessing.")
END_CONVERSATION = re.compile(
    r"\b(bye|goodbye|good night|that's all|thats all|that is all|nothing else|stop listening"
    r"|talk to you later|ttyl|see you)\b")

SYSTEM_PROMPT = (
    f"Your name is {ASSISTANT_NAME}. You are a warm, friendly companion, like a helpful friend. "
    "Speak casually and kindly, and keep answers to one or two short sentences. "
    "Your replies are spoken aloud, so never use emojis, lists, or special symbols. "
    "You can add a light friendly touch, but never make up facts: being honest matters more than being fun. "
    "If someone asks how you are, answer warmly like a friend would, and ask about them too. "
    "Never describe yourself as 'just a computer program'. "
    "All your thinking happens on this device. "
    "If a memory contains [date: ...], use exactly that date and never calculate dates yourself. "
    "If the user's saved memories answer the question, answer only what was asked, "
    "using just the relevant part of the memory. Do not repeat the whole memory. "
    "If the user asks about their own life and it is not in their memories, "
    "say you don't have that saved. "
    "If you are not sure about something, say you don't know instead of guessing. "
    "You cannot change settings. Never claim you saved, set, or changed anything. "
    "You cannot see or change the user's lists. Never say you added, removed, or saved anything."
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

# Examples that define each kind of request. The gate compares meaning, not keywords.
INTENT_EXAMPLES = [
    ("What's the weather like today?", "get_weather"),
    ("Will it rain tomorrow?", "get_weather"),
    ("Is it hot or cold outside?", "get_weather"),
    ("Do I need an umbrella?", "get_weather"),
    ("What should I wear outside today?", "get_weather"),
    ("What's the latest news?", "get_news"),
    ("What is happening in the world today?", "get_news"),
    ("Tell me today's headlines.", "get_news"),
    ("Add milk to my grocery list.", "lists"),
    ("Please put eggs on my shopping list.", "lists"),
    ("Add this idea to my ideas.", "lists"),
    ("Add a meeting on Friday to my schedule.", "lists"),
    ("What's on my grocery list?", "lists"),
    ("What lists do I have?", "lists"),
    ("Remove bread from my list.", "lists"),
    ("Capture this thought for later.", "lists"),
    ("Clear my shopping list.", "lists"),
    ("Delete my packing list, I don't need it.", "lists"),
]


# ---------- Output ----------
_greeting = ""    # e.g. "Nice to meet you, Krati!" to put in front of the next reply
_pending_name = None   # waiting for "yes/no": should I really change the user's name?
_last_reminder = None   # (text, due time) of the last reminder spoken, for "when was that reminder?"

def say(text, route, confidence=None, tone="calm"):
    """Speak a reply (with any pending greeting in front) and show it on the dashboard."""
    global _greeting
    text = (_greeting + " " + text).strip()
    _greeting = ""
    print(f"🔊 Jarvis: {text}")
    dashboard.update(status="speaking", last_reply=text, route=route, confidence=confidence)
    speak(text, tone)


# ---------- Listening ----------
_threshold = 300.0    # speech loudness threshold, learned from the room while waiting for the wake word


def ding():
    """Short two-tone chime so the user knows when to start speaking."""
    rate = 44100
    t = np.linspace(0, 0.12, int(0.12 * rate), False)
    fade = np.linspace(1, 0, t.size)
    tone = np.concatenate([np.sin(2 * np.pi * 660 * t), np.sin(2 * np.pi * 990 * t)])
    tone = 0.6 * tone * np.concatenate([fade, fade])
    sd.play(tone.astype(np.float32), rate)
    sd.wait()

def sleep_chime():
    """Falling two-tone chime: 'I've stopped listening, say Hey Jarvis to wake me again.'"""
    rate = 44100
    t = np.linspace(0, 0.12, int(0.12 * rate), False)
    fade = np.linspace(1, 0, t.size)
    tone = np.concatenate([np.sin(2 * np.pi * 990 * t), np.sin(2 * np.pi * 660 * t)])
    tone = 0.4 * tone * np.concatenate([fade, fade])
    sd.play(tone.astype(np.float32), rate)
    sd.wait()

def rms(frame):
    """Loudness of one audio chunk."""
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def listen(wake, reminders, follow_up=False):
    """Wake-word mode: wait for 'Hey Jarvis', then record (and keep an eye on due reminders).
    Follow-up mode: skip the wake word and just listen briefly for a reply.
    Returns the audio, REMINDER_DUE if a reminder needs announcing, or None if nobody spoke."""
    global _threshold
    chunk_sec = CHUNK / SAMPLE_RATE

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=CHUNK) as stream:
        if follow_up:
            print("\n💬 Still listening, no wake word needed...")
            no_speech_timeout = FOLLOW_UP_SECONDS
        else:
            wake.reset()
            noise = deque(maxlen=50)    # background loudness over the last ~4 seconds
            print("\n💤 Waiting for wake word...")
            dashboard.update(status="waiting")
            checks = 0
            while True:
                frame, _ = stream.read(CHUNK)
                frame = frame.flatten()
                noise.append(rms(frame))
                if max(wake.predict(frame).values()) > WAKE_THRESHOLD:
                    prewarm()
                    break
                checks += 1
                if checks % REMINDER_CHECK_EVERY == 0 and reminders.due_now():
                    return REMINDER_DUE
            ding()
            for _ in range(3):              # skip the ding itself (~240 ms)
                stream.read(CHUNK)
            _threshold = max(3 * float(np.median(noise)), 300)
            print("👂 Listening...")
            no_speech_timeout = NO_SPEECH_TIMEOUT

        dashboard.update(status="listening")
        frames, heard_speech, quiet = [], False, 0.0
        for _ in range(int(MAX_RECORD_SECONDS / chunk_sec)):
            frame, _ = stream.read(CHUNK)
            frame = frame.flatten()
            frames.append(frame)
            if rms(frame) > _threshold:
                heard_speech, quiet = True, 0.0
            else:
                quiet += chunk_sec
            if heard_speech and quiet >= SILENCE_TO_STOP:
                break
            if not heard_speech and len(frames) * chunk_sec >= no_speech_timeout:
                break

    if not heard_speech:
        return None
    print(f"   (recorded {len(frames) * chunk_sec:.1f}s)")
    return np.concatenate(frames).astype(np.float32) / 32768.0


def announce_due(reminders):
    """Speak every reminder or timer whose time has come, together, with one chime.
    If one was missed (e.g. the device was off), say when it was meant for."""
    global _last_reminder
    lines = []
    for rid, text, kind, due_ts in reminders.due_now():
        reminders.mark_done(rid)
        what = "your timer is done" if kind == "timer" else text
        if time.time() - due_ts > 120:                       # more than 2 minutes late
            what += f", which was for {datetime.fromtimestamp(due_ts):%I:%M %p}".replace(" 0", " ")
        
        _last_reminder = (what, datetime.fromtimestamp(due_ts))
        lines.append(what)
    if not lines:
        return
    if len(lines) == 1:
        message = f"Reminder: {lines[0]}."
    else:
        message = f"You have {len(lines)} reminders. " + ". ".join(lines) + "."
    print(f"⏰ {message}")
    ding()
    say(message, "⏰ reminder (on device)", tone="bright")

def summary_weather(memory):
    """A short weather line for the daily summary. The only online part."""
    city = memory.get_setting("home_city")
    if not city:
        return None
    try:
        full = get_weather(city)
    except requests.RequestException:
        return "I couldn't check the weather right now, but everything else is up to date."
    # the summary only needs "now" and rain; skip the high/low sentence
    return " ".join(s for s in re.split(r"(?<=\.)\s+", full) if not s.startswith("Today's high"))


def transcribe(stt, audio, memory):
    """Speech to text, hinted with words the user has used themselves (nothing hardcoded)."""
    words = set()
    for t in memory.all():
        t = re.sub(r"\[date:.*?\]", "", t.split(" (saved on")[0])   # dates are not names
        words.update(re.findall(r"\b[A-Z][a-z]+", t))
    for key in ("home_city", "user_name"):
        value = memory.get_setting(key)
        if value:
            words.add(value)
    words.update(set(memory.list_names()) | set(STARTER_LISTS))   # the user's own list names
    hint = ", ".join(sorted(words))
    segments = stt.transcribe(audio, initial_prompt=hint)
    return " ".join(s.text.strip() for s in segments).strip()


# ---------- Thinking ----------
def gate(text, example_vecs):
    """Returns (similarity to the closest example, which kind of request it looks like)."""
    sims = example_vecs @ _embed(text)
    i = int(np.argmax(sims))
    return float(sims[i]), INTENT_EXAMPLES[i][1]


def route(text, tools):
    """Router: sees ONLY the question (no memories), so it can never leak personal data into a tool.
    Streams the reply and stops as soon as the model starts writing text (= no tool needed)."""
    with requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "stream": True,
        "keep_alive": "30m",
        "tools": tools,
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


def context_message(memory):
    """Small, safe context: the user's name and the part of the day (not the exact time)."""
    hour = time.localtime().tm_hour
    part = ("night" if hour < 5 or hour >= 22 else "morning" if hour < 12
            else "afternoon" if hour < 17 else "evening")
    name = memory.get_setting("user_name")
    who = f"The user's name is {name}. Use it now and then, not in every reply. " if name else ""
    return {"role": "system", "content": f"{who}It is currently {part}."}

def prewarm():
    """Start loading the LLM in the background, so it's ready by the time we need it."""
    def _load():
        try:
            requests.post(OLLAMA_URL, json={"model": LLM_MODEL, "messages": [],
                                            "keep_alive": "30m"}, timeout=120)
        except requests.RequestException:
            pass
    threading.Thread(target=_load, daemon=True).start()
    
    

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
def strip_address(text):
    """'Ok Jarvis, what's the weather?' / 'What's on my list, Jarvis?' -> just the request.
    Also catches mishearings like 'Jardis', by comparing how similar the word is to the name."""
    def is_name(word):
        return SequenceMatcher(None, word.lower().strip(",.!?"), ASSISTANT_NAME.lower()).ratio() >= 0.7

    words = text.split()
    if len(words) > 1 and is_name(words[-1]):                    # name at the end
        words = words[:-1]
    i = 1 if words and words[0].lower().strip(",.!?") in ("hey", "ok", "okay", "hi", "hello") else 0
    if len(words) > i + 1 and is_name(words[i]):                 # name at the start
        words = words[i + 1:]
    return " ".join(words).strip(" ,") or text

COMMAND_WORDS = ["cancel", "remind", "remember", "clear", "remove", "delete", "capture", "forget"]


def fix_command_word(text):
    """Speech-to-text sometimes mishears the first word of a command ('cancer all reminders').
    If it's very close to a command word, use the command word."""
    words = text.split()
    if not words:
        return text
    first = words[0].lower().strip(",.!?")
    if first in COMMAND_WORDS:
        return text
    best = max(COMMAND_WORDS, key=lambda w: SequenceMatcher(None, first, w).ratio())
    if SequenceMatcher(None, first, best).ratio() >= 0.8:
        print(f"   (heard '{first}', using '{best}')")
        return " ".join([best] + words[1:])
    return text

def take_introduction(text, memory):
    """'I am Krati, who are you?' -> saves the name and returns the rest: 'who are you?'.
    Returns (rest_of_text, name_or_None)."""
    t = re.sub(r"^(?:hi|hello|hey)[,!.]?\s+", "", text.strip(), flags=re.I)
    m = re.match(r"(?:my name is|call me|the user is|user is)\s+([A-Za-z]+)\b(?!')[,.!]?\s*(.*)$", t, re.I)
    if not m:
        # "I am ..." only counts when the next word is capitalised: "I am Krati" yes, "I am tired" no
        m = re.match(r"(?:I am|I'm|This is)\s+([A-Z][a-z]+)\b(?!')[,.!]?\s*(.*)$", t)
    if not m:
        return text, None
    name = m.group(1).title()
    
    return m.group(2).strip(), name


def handle_command(text, memory, history, reminders):
    """Handle reminder / list / voice / identity / city / remember / forget commands.
    Returns True if handled."""
    lower = text.lower().strip(" .!?,")

    reminder_reply = handle_reminder_command(text, reminders)
    if reminder_reply:
        print(f"⏰ {reminder_reply}")
        say(reminder_reply, "⏰ reminders (on device)")
        return True

    list_reply = handle_list_command(text, memory)
    if list_reply:
        print(f"📝 {list_reply}")
        say(list_reply, "📝 lists (on device)")
        return True

    m = re.search(r"\b(male|female|man|woman|boy|girl)(?:'s)? voice\b", lower)
    if m and re.search(r"\b(use|switch|change|speak|talk|sound)\b", lower):
        kind = "female" if m.group(1) in ("female", "woman", "girl") else "male"
        set_voice(kind)
        memory.set_setting("voice", kind)
        print(f"🎙️ Voice set: {kind}")
        say(f"Sure! This is my {kind} voice. How do I sound?", "⚙️ voice changed on device", tone="bright")
        return True

    if re.match(r"(who are you|what(?:'s| is) your name|introduce yourself)", lower):
        say(INTRO, f"🙂 about {ASSISTANT_NAME}", tone="bright")
        return True

    if re.match(r"(what can you do|what are your features|how can you help)", lower):
        say(CAPABILITIES, f"🙂 about {ASSISTANT_NAME}")
        return True

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
        if is_unsafe(fact):
            say(REFUSAL, "🛡️ not saved (safety)", tone="gentle")
            return True
        fact = resolve_dates(fact)                                   # 1. real date first
        fact = f"{fact} (saved on {date.today():%A, %d %B %Y})"     # 2. then when it was saved
        memory.add(fact)
        print(f"💾 Saved: {fact}")
        say("Okay, I'll remember that.", "💾 saved to memory")
        return True

    if lower.startswith(("forget everything", "delete everything", "erase everything")):
        count = memory.forget_all()
        reminders.clear_all()
        del history[1:]
        print(f"🧹 Erased {count} memories and list items, and all reminders")
        say("Done. I've erased everything I had saved.", "🧹 everything erased")
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

    if re.search(r"remember|saved|memor", lower) and lower.startswith(
            ("what", "tell me", "list", "do you", "do we", "is there", "anything")):
        items = memory.all()
        if not items:
            say("I don't have anything saved about you.", "📋 memory list")
        else:
            print("📋 Memories:\n" + "\n".join(f"- {t}" for t in items))
            clean = [re.sub(r"\s*\[date: (.*?)\]", r", on \1", t.split(" (saved on")[0]) for t in items]
            say(f"I have {len(items)} things saved. " + ". ".join(clean), "📋 memory list")
        return True

    return False


# ---------- Main loop ----------
def main():
    global _greeting, _pending_name
    dashboard.start()
    t0 = time.time()

    def step(name):
        print(f"   ✔ {name}  ({time.time() - t0:.1f}s)")

    print("Loading models...")
    prewarm()                                        # LLM loads in the background meanwhile
    try:
        wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    except Exception:
        openwakeword.utils.download_models()         # only needed the very first time
        wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    step("wake word")
    stt = SttModel("base.en")
    step("speech to text")
    memory = MemoryStore()
    reminders = Reminders()
    set_voice(memory.get_setting("voice") or "male")
    step("memory, reminders, voice")
    example_vecs = embed_many([e for e, _ in INTENT_EXAMPLES])
    step("intent gate")
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    follow_up = False
    follow_count = 0
    chime_next = False
    print(f"Ready! ({time.time() - t0:.1f}s)")

    while True:
        if reminders.due_now():          # check every turn, not only while waiting for the wake word
            announce_due(reminders)
            follow_up = True
            continue
        if chime_next:
            sleep_chime()
            print("   (conversation limit reached: say 'Hey Jarvis' to continue)")
            chime_next = False
        dashboard.update(memories=len(memory.all()))
        audio = listen(wake, reminders, follow_up)
        if isinstance(audio, str):       # a reminder or timer is due
            announce_due(reminders)
            follow_up = True             # so the user can reply ("thanks!") without the wake word
            continue
        if audio is None:
            if follow_up:
                print("   (quiet, so the conversation ended)")
            follow_up = False
            continue

        dashboard.update(status="thinking")
        text = transcribe(stt, audio, memory)
        text = re.sub(r"\[[^\]]*\]|\([^)]*\)", "", text).strip()     # drop [BLANK_AUDIO], (music) anywhere
        sentences = [s.strip().lower() for s in re.split(r"[.!?]+", text) if s.strip()]
        if len(sentences) >= 3 and len(set(sentences)) == 1:
            print(f"   (ignored repeated phrase, probably background noise: {text})")
            text = ""
        if not text:
            print("Didn't catch anything.")
            follow_up = False
            continue
        print(f"You: {text}")
        text = fix_command_word(strip_address(text))
        dashboard.update(last_heard=text, last_reply="", route="", confidence=None)
        follow_count = follow_count + 1 if follow_up else 0
        follow_up = follow_count < MAX_FOLLOW_UPS     # keep listening briefly, but not forever
        chime_next = not follow_up           # limit reached: play the sleep chime after this reply
        short_by = len(text.split()) <= 3 and text.lower().startswith("by ")
        if END_CONVERSATION.search(text.lower()) or short_by:
            say("Okay, talk soon!", "👋 conversation ended", tone="bright")
            follow_up = False
            continue

        
        # Waiting for yes/no on a name change?
        if _pending_name:
            name, _pending_name = _pending_name, None
            if re.match(r"(yes|yeah|yep|sure|ok|okay|please)\b", text.lower()):
                memory.set_setting("user_name", name)
                print(f"🙂 User name set: {name}")
                say(f"Done, I'll call you {name} from now on.", "⚙️ name saved on device", tone="bright")
                continue
            if re.match(r"(no|nope|don't|do not)\b", text.lower()):
                say(f"Okay, I'll keep calling you {memory.get_setting('user_name')}.", "⚙️ name unchanged")
                continue

        # Questions about the user's name: answer from the saved setting, never from the LLM
        if re.search(r"\b(what(?:'s| is) my name|who am i|who is the (?:current )?user)\b", text.lower()):
            name = memory.get_setting("user_name")
            say(f"You're {name}." if name else "I don't know your name yet. You can say, my name is...",
                "⚙️ name (on device)")
            continue

        # "I am Krati, who are you?" -> handle the name, then answer the rest
        text, new_name = take_introduction(text, memory)
        if new_name:
            current = memory.get_setting("user_name")
            if current and current.lower() != new_name.lower():
                # someone else may be talking (a friend) -> ask before changing anything
                _pending_name = new_name
                say(f"Nice to meet you, {new_name}! Should I call you {new_name} from now on, instead of {current}?",
                    "⚙️ name change? (asking)", tone="bright")
                continue
            memory.set_setting("user_name", new_name)
            print(f"🙂 User name set: {new_name}")
            _greeting = f"Nice to meet you, {new_name}!"
            if not text:
                say("", "⚙️ name saved on device", tone="bright")
                continue
        
        # 0. Daily summary (Python only, plus weather)
        if SUMMARY_Q.search(text.lower()):
            summary = build_summary(memory, reminders, weather=lambda: summary_weather(memory))
            say(summary, "☀️ daily summary (on device + weather)", tone="bright")
            continue
        
        # 1. Exact commands (fast, no LLM)
        if handle_command(text, memory, history, reminders):
            continue

        # 2. Date and time (Python, offline)
        tool_answer = answer_time_question(text)
        if tool_answer:
            print(f"🕐 {tool_answer}")
            say(tool_answer, "🕐 device clock (offline)")
            continue

        # 3. Obvious weather / news (rules)
        online_answer = answer_online_question(text, memory.get_setting("home_city"))
        if online_answer:
            print(f"AI:  {online_answer}")
            say(online_answer, "🌐 internet (rule)")
            continue

        # 4. Gate -> LLM router -> validated tool (lists or internet)
        found = memory.search(text)
        score, category = gate(text, example_vecs)
        print(f"🚦 Gate: {score:.2f} ({category})")
        wants_lists = category == "lists" and score >= GATE_THRESHOLD
        wants_online = category != "lists" and score >= GATE_THRESHOLD and not found
        if wants_lists or wants_online:
            t0 = time.time()
            call = route(text, LIST_TOOLS if wants_lists else TOOLS)
            if call is None and wants_online and score >= STRONG_GATE:
                call = {"name": category, "arguments": {}}
                print(f"🚦 Router hesitated, gate is confident → {category}")
            print(f"🧭 Router: {call['name'] + ' ' + str(call.get('arguments')) if call else 'no tool'}  ({time.time() - t0:.1f}s)")
            if call:
                args = call.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args)
                if call["name"] in LIST_TOOL_NAMES:
                    tool_reply = run_list_tool(call["name"], args, memory)
                    tool_route = "📝 lists (LLM router)"
                else:
                    tool_reply = run_tool_call(call["name"], args, memory.get_setting("home_city"))
                    tool_route = "🌐 internet (LLM router)"
                if tool_reply:
                    print(f"AI:  {tool_reply}")
                    say(tool_reply, tool_route)
                    continue

        # 5. Local LLM answer (with memories if relevant)
        messages = list(history)
        if found:
            facts = "\n".join(f"- {t}" for _, t in found)
            print(f"📚 Using memory:\n{facts}")
            messages.append({"role": "system",
                             "content": f"The user's saved memories:\n{facts}"})
        messages.append(context_message(memory))
        messages.append({"role": "user", "content": text})

        start = time.time()
        reply, confidence = think(messages)
        print(f"🤔 Confidence: {confidence:.2f}")
        route_name = "📚 memory (on device)" if found else "🧠 local LLM"

        # "I'm not sure" mode: low confidence on general knowledge = likely a guess
        declined = re.search(r"don't (know|have)|not sure|no access", reply.lower())
        is_question = re.match(
            r"(what|who|when|where|which|why|how (?:many|much|old|far|long|big)|is|are|was|were|does|did)\b",
            text.lower())
        about_assistant = re.search(r"\b(you|your|yourself)\b", text.lower())
        if not found and not declined and is_question and not about_assistant and confidence < CONFIDENCE_THRESHOLD:
            print(f"   (withheld guess: {reply})")
            reply = "Hmm, I'm not sure about that one, and I'd rather not guess wrong."
            route_name = "🤔 not sure (guess withheld)"

        # Block false claims: the LLM can't set alarms, change lists, send messages, or make calls
                # Block false claims: the LLM can't set alarms, change lists, send messages, or make calls
        promise = re.search(
            r"\b(i'll|i will|i've|i have|let me)\s+(?:make sure\b|"
            r"(?:remind|set|call|text|send|book|order|schedule|add|save|remove|note|check"
            r"|clear|delete|cancel|change|update))"
            r"|\b(added|removed|saved|scheduled|noted)\b",
            reply.lower())
        if promise and not declined:
            print(f"   (blocked false promise: {reply})")
            reply = "I can't do that yet, sorry. Ask me what I can do, and I'll tell you."
            route_name = "🛡️ false promise blocked"

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        history = history[:1] + history[-6:]   # keep only recent turns, stays fast
        print(f"AI:  {reply}   ({time.time() - start:.1f}s)")
        tone = "gentle" if route_name.startswith(("🤔", "🛡️")) else "calm"
        say(reply, route_name, round(confidence, 2), tone)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")