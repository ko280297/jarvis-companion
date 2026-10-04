import json
import math
import random
import re
import threading
import time
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
from online import answer_online_question, run_tool_call, get_weather, check_place
from lists import handle_list_command, run_list_tool, LIST_TOOLS, LIST_TOOL_NAMES, STARTER_LISTS
from reminders import Reminders, handle_reminder_command
from summary import SUMMARY_Q, build_summary
from safety import is_unsafe, REFUSAL
from emergency import handle_emergency
from wellbeing import handle_wellbeing, clear_journal, clear_session, vent_mode, vent_silence
from focus import (handle_focus, focus_tick, focus_due, is_focusing, focus_screen,
                   focus_summary_line, clear_focus_log)
from activities import handle_activity, is_active, end_activity, game_reprompt
from meeting import handle_meeting, meeting_chunk, meeting_active, meeting_screen

# ---------- Audio ----------
STT_MODEL = "base.en"           # everyday commands: fast
MEETING_STT_MODEL = "small.en"  # meetings: slower, but hears names and accents much better
SAMPLE_RATE = 16000
CHUNK = 1280                # 80 ms
MAX_RECORD_SECONDS = 8      # longest question allowed
SILENCE_TO_STOP = 1.5       # seconds of quiet that ends the question
NO_SPEECH_TIMEOUT = 4.0     # after the wake word: give up if nothing is said
FOLLOW_UP_SECONDS = 7.0     # conversation mode: how long to wait for a reply without the wake word
MAX_FOLLOW_UPS = 10         # after this many turns without the wake word, go back to waiting for it
WAKE_WORD = "hey_jarvis"    # or "hey_mycroft"
WAKE_THRESHOLD = 0.5
REMINDER_CHECK_EVERY = 12   # chunks (~1 second) between reminder checks while waiting
REMINDER_DUE = "reminder_due"

# ---------- Reasoning ----------
CONFIDENCE_THRESHOLD = 0.85   # from testing: facts 0.9+, guesses below 0.75
GATE_THRESHOLD = 0.45         # below this: no tool needed, skip the router
STRONG_GATE = 0.70            # above this: trust the gate even if the router hesitates (online only)

OLLAMA_URL = "http://localhost:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"
NUM_CTX = 2048                # our replies are short; a smaller context saves RAM on the Pi

# ---------- Personality ----------
ASSISTANT_NAME = "Jarvis"
INTRO = (f"I'm {ASSISTANT_NAME}, your private companion. I live right here on this device, "
         "so everything you tell me stays with you.")
CAPABILITIES = ("I can remember things for you, keep lists like groceries, ideas and your schedule, "
                "set reminders and timers, run focus sessions with healthy breaks, "
                "give you a summary of your day, tell you the date and time, "
                "play simple games, listen in meetings for dates and tasks, "
                "check the weather or news, guide you through calming exercises when you need a moment, "
                "and help you with the right numbers in an emergency. "
                "And if I'm not sure about something, I'll tell you instead of guessing.")
END_CONVERSATION = re.compile(
    r"\b(bye|goodbye|good ?night|that's all|thats all|that is all|nothing else|stop listening"
    r"|talk to you later|ttyl|see you)\b")
START_OVER = re.compile(r"\b(start (?:over|again|fresh|from scratch)|reset (?:the )?conversation|new conversation)\b")

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
    "If several saved memories answer the question, mention all of them. "
    "If the user asks about their own life and it is not in their memories, "
    "say you don't have that saved. "
    "If you are not sure about something, say you don't know instead of guessing. "
    "You cannot change settings. Never claim you saved, set, or changed anything. "
    "You cannot see or change the user's lists. Never say you added, removed, or saved anything. "
    "Only your user talks to you, unless memories say otherwise. "
    "Never guess anyone's name: if a person's name is not in the saved memories, say you don't know it."
    " Only if the user asks about health, symptoms or treatment: give no medical advice, kindly suggest a doctor."
    " Always call the user by the name given in the system message, never by any other name."
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


# ---------- Conversation state ----------
_greeting = ""         # e.g. "Nice to meet you, Krati!" to put in front of the next reply
_pending_name = None   # waiting for "yes/no": should I really change the user's name?
_expect_city = False   # just set or asked about the city -> "No, it's Pune" / "P-U-N-E" corrects it
_guest = None          # (name, since) while a friend is talking: RAM only
GUEST_MINUTES = 10

WHO_TALKING = re.compile(
    r"\bwho (?:are you|am i) (?:talking|speaking|interacting|chatting) (?:to|with)\b"
    r"|\bwho are you (?:interacting|talking|chatting) with\b|\bwho(?:'s| is) (?:talking|speaking) (?:to you|now)\b")
PRIVATE_Q = re.compile(
    r"\b(?:my day|summary|remember|memories|memory|saved|lists?|grocery|groceries|schedule|reminders?"
    r"|emergency contacts?|friends?|sister|brother|journal)\b")
MORE_SUMMARY = re.compile(
    r"\b(?:daily|day'?s|today'?s|entire|full|whole) summary\b|\bsummary of (?:my|the) (?:day|tasks?)\b")
BULLET = re.compile(r"^\s*(?:[*\-•]|\d+[.)])\s+")
NOISE_TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")   # [BLANK_AUDIO], (music), *cough*

# Whisper sometimes "hears" these in a short burst of noise; ignore them when the recording was tiny
WHISPER_GHOSTS = {"for you", "you", "thanks for watching", "thank you for watching", "so"}


# ---------- Output ----------
def say(text, route, confidence=None, tone="calm"):
    """Speak a reply (with any pending greeting in front) and show it on the dashboard."""
    global _greeting
    text = (_greeting + " " + text).strip()
    _greeting = ""
    print(f"🔊 Jarvis: {text}")
    dashboard.update(status="speaking", last_reply=text, route=route, confidence=confidence)
    speak(text, tone)


EMPATHY_PROMPT = (
    f"You are {ASSISTANT_NAME}, a warm, caring companion. The user just told you how they feel. "
    "Reply with ONE short, kind sentence that shows you understood what they said, in your own words. "
    "Do not give advice, do not suggest anything, do not ask a question, and do not use any names.")
EMPATHY_BLOCK = re.compile(
    r"\b(?:should|try|doctor|therap\w*|medic\w*|pill|tablet|diagnos\w*|you need|you must|harm|kill|die"
    r"|suicid\w*|as an ai|language model|i can'?t|i cannot)\b")


def empathy_line(user_text, earlier=()):
    """One warm sentence from the LLM, checked. Returns None if it fails any check (then the file's line is used).
    'earlier' = what the user shared about their feelings earlier in this session (RAM only)."""
    messages = [{"role": "system", "content": EMPATHY_PROMPT}]
    if earlier:
        shared = "\n".join(f"- {t}" for t in earlier)
        messages.append({"role": "system", "content":
                         f"Earlier in this conversation the user said:\n{shared}\n"
                         "Use this only to show you remember. Do not repeat it word for word."})
    messages.append({"role": "user", "content": user_text})
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": LLM_MODEL, "stream": False, "keep_alive": "30m", "messages": messages,
            "options": {"temperature": 0.3, "num_predict": 40, "num_ctx": NUM_CTX},
        }, timeout=15)
        r.raise_for_status()
        line = (r.json()["message"].get("content") or "").strip()
    except (requests.RequestException, ValueError, KeyError):
        return None
    line = re.split(r"(?<=[.!])\s", line)[0].strip()          # first sentence only
    if not line or len(line.split()) > 25 or "?" in line or EMPATHY_BLOCK.search(line.lower()):
        print(f"   (empathy line rejected: {line})")
        return None
    return line


_last_ack = None


def split_items(text):
    """'a window, a door and the fan' -> ['window', 'door', 'fan'] (for counting in grounding)."""
    items = []
    for part in re.split(r",|;|\.|\band\b|\s(?:a|an|the|my)\s", f" {text.lower()} "):
        part = re.sub(r"^\s*(?:i can (?:see|hear|feel|smell|taste)|there'?s|there is)\s+", "", part.strip())
        part = re.sub(r"^(?:a|an|the|my|some)\s+", "", part).strip(" !?")
        if len(part) > 1 and part not in ("um", "uh", "okay", "ok", "so", "like") and part not in items:
            items.append(part)
    return items


def play_sequence(colours, screen):
    """Memory game: light up each colour on screen while saying it, then hand over to the player."""
    print(f"   🧠 Pattern: {', '.join(colours)}")
    for i, colour in enumerate(colours):
        dashboard.update(activity={**screen, "flash": colour, "flash_id": time.time()})
        if i == 0:
            words = f"First, {colour}."
        elif colour == colours[i - 1]:
            words = f"Then {colour} again."          # a repeated colour, made easy to notice
        else:
            words = f"Then {colour}."
        speak(words, "calm")
        dashboard.update(activity={**screen, "flash": None})
        time.sleep(0.7)                              # a clear gap, so repeated colours flash twice
    say("Now it's your turn.", "🎮 game (on device)", tone="bright")


def run_plan(plan, hear=None):
    """Carry out a plan: things to say, timed breathing cues, listening steps and screen updates.
    hear(seconds) -> what the user said ('' if nothing, None if an emergency took over)."""
    global _last_ack
    for step in plan:
        kind = step[0]
        if kind == "say":
            say(step[1], "🌿 wellbeing (on device)", tone=step[2])
        elif kind == "empathy":                            # one warm LLM sentence, checked, with a fallback
            _, user_text, fallback, lead, earlier = step
            line = empathy_line(user_text, earlier)
            if line:
                say(f"{line} {lead}".strip(), "🌿 wellbeing (warm line by local LLM, checked)", tone="gentle")
            else:
                say(fallback, "🌿 wellbeing (on device)", tone="gentle")
        elif kind == "cue":                                # one breathing step, with timing
            _, cue, phase, secs, name = step
            dashboard.update(activity={"title": f"🌿 {name}", "lines": [cue],
                                       "breath": {"phase": phase, "seconds": secs, "id": time.time()}})
            started = time.time()
            speak(cue, "gentle")
            time.sleep(max(0, secs - (time.time() - started)))
        elif kind == "ask":                                # listen and count: shown on screen, never saved
            _, secs, name, line, acks, count = step
            items, tries = [], 0
            while tries < 3:
                heard = hear(secs) if hear else ""
                if heard is None:                          # an emergency came up: stop the exercise
                    return
                if not heard:
                    break
                tries += 1
                if not count:                              # nothing to count: just show what was said
                    items = [heard]
                    break
                items += [i for i in split_items(heard) if i not in items]
                items = items[:count]                      # named 7 of 5? only the first 5 count
                print(f"   (counted {len(items)} of {count}: {', '.join(items)})")
                dashboard.update(activity={"title": f"🌿 {name}",
                                           "lines": [line] + [f"{n}. {i}" for n, i in enumerate(items, 1)]})
                if len(items) >= count:
                    break
                if tries < 3:
                    time.sleep(0.8)
                    say(f"That's {len(items)}. Can you find {count - len(items)} more?",
                        "🌿 wellbeing (on device)", tone="gentle")
            if items:
                time.sleep(1.0)                            # let the moment settle before speaking
                if count and len(items) < count:
                    ack = "That's okay, that's enough."
                else:
                    _last_ack = random.choice([a for a in acks if a != _last_ack] or acks)
                    ack = _last_ack
                say(ack, "🌿 wellbeing (on device)", tone="gentle")
                time.sleep(1.5)                            # a breath before the next step
        elif kind == "wait":
            time.sleep(step[1])
        elif kind == "screen":
            dashboard.update(activity=step[1])
        elif kind == "silent":                             # thought dump: just keep listening
            pass


# ---------- Listening ----------
_threshold = 300.0    # speech loudness threshold, learned from the room while waiting for the wake word


def _chime(first_hz, second_hz, volume):
    rate = 44100
    t = np.linspace(0, 0.12, int(0.12 * rate), False)
    fade = np.linspace(1, 0, t.size)
    tone = np.concatenate([np.sin(2 * np.pi * first_hz * t), np.sin(2 * np.pi * second_hz * t)])
    tone = volume * tone * np.concatenate([fade, fade])
    sd.play(tone.astype(np.float32), rate)
    sd.wait()


def ding():
    """Rising chime: 'I'm listening, go ahead.'"""
    _chime(660, 990, 0.6)


def sleep_chime():
    """Falling chime: 'I've stopped listening, say Hey Jarvis to wake me again.'"""
    _chime(990, 660, 0.4)


def rms(frame):
    """Loudness of one audio chunk."""
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def listen(wake, reminders, follow_up=False, max_seconds=MAX_RECORD_SECONDS,
           silence=SILENCE_TO_STOP, timeout=FOLLOW_UP_SECONDS, level=None):
    """Wake-word mode: wait for 'Hey Jarvis', then record (and keep an eye on due reminders).
    Follow-up mode: skip the wake word and just listen briefly for a reply.
    level: a lower loudness level for quieter voices (meetings).
    Returns the audio, REMINDER_DUE if a reminder needs announcing, or None if nobody spoke."""
    global _threshold
    chunk_sec = CHUNK / SAMPLE_RATE

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="int16", blocksize=CHUNK) as stream:
        if follow_up:
            print("\n💬 Still listening, no wake word needed...")
            no_speech_timeout = timeout
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
                    prewarm()       # LLM starts loading while the user is still speaking
                    break
                checks += 1
                if checks % REMINDER_CHECK_EVERY == 0 and (
                        focus_due() or (reminders.due_now() and not is_focusing() and not meeting_active())):
                    return REMINDER_DUE
            ding()
            for _ in range(3):              # skip the ding itself (~240 ms)
                stream.read(CHUNK)
            _threshold = max(3 * float(np.median(noise)), 300)
            print("👂 Listening...")
            no_speech_timeout = NO_SPEECH_TIMEOUT

        dashboard.update(status="listening")
        frames, heard_speech, quiet = [], False, 0.0
        for _ in range(int(max_seconds / chunk_sec)):
            frame, _ = stream.read(CHUNK)
            frame = frame.flatten()
            frames.append(frame)
            if rms(frame) > (level or _threshold):
                heard_speech, quiet = True, 0.0
            else:
                quiet += chunk_sec
            if heard_speech and quiet >= silence:
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
    lines = []
    for rid, text, kind, due_ts in reminders.due_now():
        reminders.mark_done(rid)
        what = "your timer is done" if kind == "timer" else text
        if time.time() - due_ts > 120:                       # more than 2 minutes late
            what += f", which was for {datetime.fromtimestamp(due_ts):%I:%M %p}".replace(" 0", " ")
        lines.append(what)
    if not lines:
        return
    if len(lines) == 1:
        message = f"Reminder: {lines[0]}."
    else:
        message = f"You have {len(lines)} reminders. " + ". ".join(lines) + "."
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
    words.update({ASSISTANT_NAME, "Tic-tac-toe", "Memory sequence", "Mental math"})   # app words
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
        "options": {"temperature": 0, "num_predict": 40, "num_ctx": NUM_CTX},
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
    """Small, safe context: who is talking and the part of the day (not the exact time)."""
    hour = time.localtime().tm_hour
    part = ("night" if hour < 5 or hour >= 22 else "morning" if hour < 12
            else "afternoon" if hour < 17 else "evening")
    name = memory.get_setting("user_name")
    if _guest:
        who = (f"You are talking to {_guest[0]}, a guest of {name}. Call them {_guest[0]}. "
               f"Never share anything about {name}'s life. ")
    else:
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
        "options": {"temperature": 0, "num_predict": 80, "num_ctx": NUM_CTX},
    }, timeout=120)
    if response.status_code != 200:
        print("⚠️ Ollama says:", response.text)
    response.raise_for_status()
    data = response.json()
    reply = (data["message"].get("content") or "").strip()
    lps = data.get("logprobs") or []
    confidence = math.exp(sum(t["logprob"] for t in lps) / len(lps)) if lps else 1.0
    return reply, confidence


# ---------- Text helpers ----------
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


def take_introduction(text):
    """'I am Krati, who are you?' -> ('who are you?', 'Krati'). Does NOT save the name:
    main() decides, and asks first if it would replace a different saved name."""
    t = re.sub(r"^(?:hi|hello|hey)[,!.]?\s+", "", text.strip(), flags=re.I)
    m = re.search(r"\b(?:my name is|call me|the user is|user is)\s+([A-Za-z]+)\b(?!')[,.!]?\s*", t, re.I)
    if m:   # anywhere in the sentence: "we've talked before... my name is Shubhangi"
        rest = (t[:m.start()] + " " + t[m.end():]).strip(" ,.")
        return rest, m.group(1).title()
    # "I am ..." only counts when the next word is capitalised: "I am Krati" yes, "I am tired" no
    m = re.match(r"(?:I am|I'm|This is)\s+([A-Z][a-z]+)\b(?!')[,.!]?\s*(.*)$", t)
    if m:
        return m.group(2).strip(), m.group(1).title()
    return text, None


def detect_guest(lower, user):
    """'I'm Krati's friend Shubhangi' / 'you're talking to my friend Shubhangi' -> 'Shubhangi'."""
    owner = re.escape(user.lower()) if user else r"\w+"
    patterns = [
        rf"\b(?:i(?:'m| am)|this is) {owner}'?s {RELATIONS}[, ]+(\w+)",
        rf"\byou(?:'re| are) (?:talking|speaking|interacting|chatting) (?:to|with) (?:{owner}'?s |my )?{RELATIONS}[, ]+(\w+)",
        rf"\bmy {RELATIONS}[, ]+(\w+) is (?:talking|speaking|here)\b",
    ]
    for p in patterns:
        m = re.search(p, lower)
        if m:
            return m.group(2).title()
    return None


def for_speech(text):
    """LLM replies are spoken: turn bullet lists into one sentence and drop markdown symbols."""
    lines = [l for l in text.splitlines() if l.strip()]
    bullets = [BULLET.sub("", l).strip() for l in lines if BULLET.match(l)]
    others = [l.strip() for l in lines if not BULLET.match(l)]
    if bullets:
        text = " ".join(others[:1] + [", ".join(bullets) + "."] + others[1:])
    text = re.sub(r"[*_#`]", "", text)
    return " ".join(text.split())


SPELLED = re.compile(r"\b[a-z](?:[\s\-.,]+[a-z]\b){2,}")


def spelled_word(lower):
    """'p-u-n-e' / 'p u n e' (a word spelled letter by letter) -> 'pune', or None."""
    m = SPELLED.search(lower)
    return re.sub(r"[^a-z]", "", m.group(0)) if m else None


def clean_city(name):
    """'mumbai now' / 'just mumbai' -> 'Mumbai' (drop little filler words around the name)."""
    name = re.sub(r"\b(now|please|instead|actually|only|just|then|currently|thanks|thank you)\b", " ", name)
    return " ".join(name.split()).title()


def save_city(city, memory):
    """Check the place exists, then save it. Returns the spoken reply."""
    global _expect_city
    found = check_place(city)
    if found == "":   # offline: can't check, so don't overwrite a good city with a guess
        _expect_city = False
        old = memory.get_setting("home_city")
        if old:
            return f"I can't check places right now because I'm offline, so I'll keep {old} for now."
        return "I can't check places right now because I'm offline. Please tell me your city again later."
    if found is None:
        _expect_city = True
        return f"I couldn't find a place called {city}. Could you spell it for me, letter by letter?"
    memory.set_setting("home_city", city)
    memory.set_setting("home_country", found.split(", ")[-1])   # for the right emergency numbers
    _expect_city = True                # allow a quick "No, it's ..." correction next
    return f"Got it, your city is {found}."


# ---------- Commands ----------
RELATIONS = r"(friend|sister|brother|mother|mom|father|dad|cousin|colleague|wife|husband|partner)"
PERSON = re.compile(
    rf"\b(?:this is|meet) my {RELATIONS}[,]?\s+([a-z]+)"
    rf"|\bmy {RELATIONS}'?s? name is\s+([a-z]+)")


def handle_command(text, memory, history, reminders):
    """Handle reminder / list / people / voice / identity / city / remember / forget commands.
    Returns True if handled."""
    global _expect_city
    lower = text.lower().strip(" .!?,")

    reminder_reply = handle_reminder_command(text, reminders)
    if reminder_reply:
        say(reminder_reply, "⏰ reminders (on device)")
        return True

    # "add milk, eggs and bread to my grocery list" -> three separate items
    m = re.match(r"(?:add|put)\s+(.+?)\s+(?:to|on|in)\s+(?:my\s+|the\s+)?(.+?)\s*list$", lower)
    if m and re.search(r",|\band\b", m.group(1)):
        items = [i.strip() for i in re.split(r",|\band\b", m.group(1)) if i.strip()]
        for item in items:
            handle_list_command(f"add {item} to my {m.group(2)} list", memory)
        names = ", ".join(items[:-1]) + " and " + items[-1]
        say(f"Added {names} to your {m.group(2)} list.", "📝 lists (on device)")
        return True

    list_reply = handle_list_command(text, memory)
    if list_reply:
        say(list_reply, "📝 lists (on device)")
        return True

    # ----- People -----
    m = PERSON.search(lower)
    if m:   # "This is my friend Shubhangi" / "My friend's name is Shubhangi"
        relation = m.group(1) or m.group(3)
        person = (m.group(2) or m.group(4)).title()
        memory.add(f"My {relation}'s name is {person} (saved on {date.today():%A, %d %B %Y})")
        print(f"🙂 Remembered: my {relation} is {person}")
        say(f"Nice to meet you, {person}! I'll remember that.", "💾 person saved to memory", tone="bright")
        return True

    m = re.search(rf"\bwho (?:is|are) my {RELATIONS}s?\b|\bmy {RELATIONS}s?'?s? names?\b", lower)
    if m:   # "Who are my friends?" / "What's my sister's name?" -> read straight from memory
        relation = m.group(1) or m.group(2)
        people = []
        for t in memory.all():
            core = t.split(" (saved on")[0]
            p = re.search(rf"\bmy {relation}'s name is (\w+)", core, re.I)
            if not p and relation == "friend":
                p = re.match(r"(\w+) is a friend of", core)      # guests saved earlier
            if p:
                people.append(p.group(1).title())
        people = list(dict.fromkeys(people))                     # no repeats
        if not people:
            say(f"I don't know your {relation}'s name yet. You can say, this is my {relation}, and their name.",
                "👥 people (on device)")
        elif len(people) == 1:
            say(f"Your {relation} is {people[0]}.", "👥 people (on device)")
        else:
            names = ", ".join(people[:-1]) + " and " + people[-1]
            say(f"Your {relation}s are {names}.", "👥 people (on device)")
        return True

    # ----- Voice -----
    m = re.search(r"\b(male|female|man|woman|boy|girl)(?:'s)? voice\b", lower)
    if m and re.search(r"\b(use|switch|change|speak|talk|sound)\b", lower):
        kind = "female" if m.group(1) in ("female", "woman", "girl") else "male"
        set_voice(kind)
        memory.set_setting("voice", kind)
        say(f"Sure! This is my {kind} voice. How do I sound?", "⚙️ voice changed on device", tone="bright")
        return True

    # ----- About Jarvis -----
    if re.match(r"(?:who are you|what(?:'s| is) your name|introduce yourself)\b"
                r"(?!\s+(?:talking|speaking|interacting|chatting))", lower):
        say(INTRO, f"🙂 about {ASSISTANT_NAME}", tone="bright")
        return True

    if re.match(r"(what can you do|what are your features|how can you help)", lower):
        say(CAPABILITIES, f"🙂 about {ASSISTANT_NAME}")
        return True

    # ----- City -----
    if _expect_city:   # right after a city was set or asked: "No, it's Pune" / "It's just Mumbai" / "P-U-N-E"
        _expect_city = False
        fixed = spelled_word(lower)
        if not fixed:
            words = re.findall(r"\b(?:it'?s|it is|i said|i meant)\s+(?!not\b)([a-z][a-z ]*)", lower)
            fixed = words[-1] if words else None
        if fixed and not re.search(r"\b(weather|news|time|date|reminder|list|day)\b", fixed):
            say(save_city(clean_city(fixed), memory), "⚙️ city corrected on device")
            return True

    m = re.match(
        r"(?:(?:currently\s+)?my (?:current |home )?(?:city|location) is"
        r"|(?:set|change|update) my (?:current |home )?(?:city|location) (?:to|as)"
        r"|i live in|i(?:'m| am) (?:now |currently )?(?:residing |reciting |staying |living |based )?in)"
        r"\s+([a-z][a-z ]*?)(?:[,.].*)?$", lower)
    if m:
        city = clean_city(spelled_word(lower) or m.group(1))
        say(save_city(city, memory), "⚙️ city saved on device")
        return True

    if re.search(r"\bwhat(?:'s| is) my (?:current |home )?(?:city|location)\b|\bwhere (?:do i live|am i)\b", lower):
        city = memory.get_setting("home_city")
        _expect_city = True            # so "No, it's Delhi now" right after this corrects it
        say(f"Your city is set to {city}." if city else "I don't know your city yet. You can say, my city is...",
            "⚙️ city (on device)")
        return True

    # ----- Memory -----
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
        clear_journal()
        clear_focus_log()
        clear_session()
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
    global _greeting, _pending_name, _guest
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
    stt = SttModel(STT_MODEL)
    meeting_stt = None                               # loaded the first time meeting mode starts
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

    def hear(seconds):
        """Listen for a short answer during an exercise. '' = nothing heard, None = an emergency took over."""
        audio = listen(wake, reminders, True, max_seconds=seconds + 10, silence=3.0, timeout=seconds)
        if audio is None or isinstance(audio, str):
            return ""
        heard = NOISE_TAGS.sub("", transcribe(stt, audio, memory)).strip()
        print(f"You: {heard}")
        help_ = handle_emergency(heard, memory) if heard else None
        if help_:
            dashboard.update(emergency=help_["banner"], activity=None)
            say(help_["say"], "🚨 emergency help (offline)", tone=help_["tone"])
            return None
        return heard

    print(f"Ready! ({time.time() - t0:.1f}s)")

    while True:
        plan = focus_tick()                          # focus or break time is up
        if plan:
            ding()
            run_plan(plan, hear)
            follow_up = True
            continue
        if reminders.due_now() and not is_focusing() and not meeting_active():   # wait during focus / meetings
            announce_due(reminders)
            follow_up = True
            continue
        if focus_screen() and not is_active():       # keep the countdown on screen (unless a game is on)
            dashboard.update(activity=focus_screen())
        if chime_next:
            sleep_chime()
            print("   (conversation limit reached: say 'Hey Jarvis' to continue)")
            chime_next = False
        dashboard.update(memories=len(memory.all()))

        # Meeting mode: listen quietly in 15-second pieces, note dates and tasks, never reply mid-meeting
        if meeting_active():
            dashboard.update(status="listening", activity=meeting_screen())
            if meeting_stt is None:
                print("   (loading the meeting speech model...)")
                meeting_stt = SttModel(MEETING_STT_MODEL)
            audio = listen(wake, reminders, True, max_seconds=15, silence=99, timeout=15,
                           level=max(_threshold * 0.5, 150))     # full 15 s pieces, quieter voices count
            if audio is None or isinstance(audio, str):
                continue
            heard = NOISE_TAGS.sub("", transcribe(meeting_stt, audio, memory)).strip()
            if not heard:
                continue
            print(f"   📝 (meeting) {heard}")
            help_ = handle_emergency(heard, memory)              # emergencies still come first
            if help_:
                dashboard.update(emergency=help_["banner"])
                say(help_["say"], "🚨 emergency help (offline)", tone=help_["tone"])
                continue
            plan = meeting_chunk(heard, memory)
            if plan:
                run_plan(plan, hear)
            follow_up = not meeting_active()                     # after "meeting mode off", stay for the questions
            continue

        venting = vent_mode()
        audio = listen(wake, reminders, follow_up or venting,
                       max_seconds=60 if venting else MAX_RECORD_SECONDS,
                       silence=4.0 if venting else SILENCE_TO_STOP,
                       timeout=45 if venting else FOLLOW_UP_SECONDS)
        if isinstance(audio, str):       # a reminder, or focus/break time, is due: handled at the top
            continue
        if audio is None:
            if venting:                                    # quiet during a thought dump: check in gently
                run_plan(vent_silence() or [], hear)
                follow_up = vent_mode()
                continue
            if follow_up:
                print("   (quiet, so the conversation ended)")
            follow_up = False
            continue

        dashboard.update(status="thinking")
        text = transcribe(stt, audio, memory)
        text = NOISE_TAGS.sub("", text).strip()                                   # drop [BLANK_AUDIO], (music), *cough*
        text = re.sub(r"\bto (day|morrow|night)\b", r"to\1", text, flags=re.I)   # "to day" -> "today"
        sentences = [s.strip().lower() for s in re.split(r"[.!?]+", text) if s.strip()]
        if len(sentences) >= 3 and len(set(sentences)) == 1:
            print(f"   (ignored repeated phrase, probably background noise: {text})")
            text = ""
        if len(audio) < 2.0 * SAMPLE_RATE and text.lower().strip(" .!?,") in WHISPER_GHOSTS:
            print(f"   (ignored a short noise heard as: {text})")
            text = ""
        if not text:
            print("Didn't catch anything.")
            follow_up = False
            continue

        print(f"You: {text}")
        stripped = strip_address(text)
        addressed = stripped != text            # the user said "Jarvis": clearly talking to me
        text = fix_command_word(stripped)
        dashboard.update(last_heard=text, last_reply="", route="", confidence=None)

        # Emergency help comes before everything else: no LLM, works offline
        help_ = handle_emergency(text, memory)
        if help_:
            if help_["private"]:
                dashboard.update(last_heard="(private)")
            dashboard.update(emergency=help_["banner"])
            say(help_["say"], "🚨 emergency help (offline)", tone=help_["tone"])
            follow_up, follow_count, chime_next = True, 0, False    # stay with the user
            continue

        # Meeting mode: "meeting mode on", and the yes/no questions after the meeting
        plan = handle_meeting(text, memory)
        if plan:
            run_plan(plan, hear)
            follow_up, follow_count, chime_next = True, 0, False
            continue

        # Wellbeing. During a thought dump: did the recording end because they paused (not because 60 s ran out)?
        paused = not venting or len(audio) < (60 - 1) * SAMPLE_RATE
        plan = handle_wellbeing(text, memory, paused=paused)
        if plan:
            dashboard.update(last_heard="(private)")
            run_plan(plan, hear)
            follow_up, follow_count, chime_next = True, 0, False
            continue

        # Goodbye (a single "bye" during a game is usually "five" misheard, so the game gets it)
        short_by = len(text.split()) <= 3 and re.match(r"by\b", text.lower())
        one_word = len(text.split()) <= 1
        if (END_CONVERSATION.search(text.lower()) or short_by) and not (is_active() and one_word):
            say("Okay, talk soon!", "👋 conversation ended", tone="bright")
            _guest = None
            follow_up, chime_next = False, False
            continue

        # Focus & Energy: sessions, breaks, pause/resume (no LLM)
        plan = handle_focus(text, memory)
        if plan:
            run_plan(plan, hear)
            follow_up, follow_count, chime_next = True, 0, False
            continue

        # Games: while one is running, it gets the first say (emergencies still come first)
        act = handle_activity(text)
        if act:
            dashboard.update(activity=act["screen"])
            say(act["say"], "🎮 game (on device)", tone="bright")
            if act.get("show"):
                play_sequence(act["show"], act["screen"])
            follow_up, follow_count, chime_next = True, 0, False    # games don't hit the follow-up limit
            continue

        if addressed:
            follow_count = 0                    # saying "Jarvis" keeps the conversation going
        else:
            follow_count = follow_count + 1 if follow_up else 0
        follow_up = follow_count < MAX_FOLLOW_UPS     # keep listening briefly, but not forever
        chime_next = not follow_up                    # limit reached: play the sleep chime after this reply

        if START_OVER.search(text.lower()):
            del history[1:]
            _pending_name = None
            clear_session()
            say("Okay, fresh start! What would you like to talk about?", "🔄 conversation reset", tone="bright")
            continue

        # Who is talking right now? (RAM only; ends after 10 minutes, on "bye", or when the user comes back)
        if _guest and time.time() - _guest[1] > GUEST_MINUTES * 60:
            _guest = None
        user = memory.get_setting("user_name")
        guest_name = detect_guest(text.lower(), user)
        if guest_name and (not user or guest_name.lower() != user.lower()):
            _guest = (guest_name, time.time())
            del history[1:]
            say(f"Hi {guest_name}, nice to meet you! I'll keep {user}'s personal things private while we talk.",
                "👥 guest mode (on device)", tone="bright")
            continue
        if WHO_TALKING.search(text.lower()):
            if _guest:
                say(f"I'm talking to {_guest[0]}, {user}'s guest.", "👥 who's talking (on device)")
            else:
                say(f"I'm talking to you, {user}." if user else "I don't know your name yet. You can say, my name is...",
                    "👥 who's talking (on device)")
            continue
        if _guest and PRIVATE_Q.search(text.lower()):
            say(f"That's {user}'s private information, so I'll keep it for them. Is there anything else I can help with?",
                "🔒 guest mode: kept private", tone="gentle")
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
                user = memory.get_setting("user_name")
                memory.add(f"{name} is a friend of {user} (saved on {date.today():%A, %d %B %Y})")
                _guest = (name, time.time())
                print(f"🙂 Guest remembered: {name}")
                say(f"Got it, {name}! {user} is still my main user, and I'll remember you're {user}'s friend.",
                    "⚙️ guest remembered", tone="bright")
                continue

        # Questions about the user's name: answer from the saved setting, never from the LLM
        if re.search(r"\b(what(?:'s| is) my name|who am i|who is the (?:current )?user)\b", text.lower()):
            name = memory.get_setting("user_name")
            say(f"You're {name}." if name else "I don't know your name yet. You can say, my name is...",
                "⚙️ name (on device)")
            continue

        # "I am Krati, who are you?" -> handle the name, then answer the rest
        text, new_name = take_introduction(text)
        if new_name:
            current = memory.get_setting("user_name")
            if current and current.lower() == new_name.lower():
                # same name as saved: the main user is (back) here
                was_guest = _guest is not None
                _guest = None
                del history[1:]
                if not text:
                    msg = f"Welcome back, {current}!" if was_guest else f"Of course, {current}! Sorry about that."
                    say(msg, "⚙️ name (on device)", tone="bright" if was_guest else "gentle")
                    continue
            elif current:
                # a different name: someone else may be talking (a friend) -> ask first
                _pending_name = new_name
                say(f"Nice to meet you, {new_name}! Should I call you {new_name} from now on, instead of {current}?",
                    "⚙️ name change? (asking)", tone="bright")
                continue
            else:
                # first time we hear a name
                memory.set_setting("user_name", new_name)
                print(f"🙂 User name set: {new_name}")
                _greeting = f"Nice to meet you, {new_name}!"
                if not text:
                    say("", "⚙️ name saved on device", tone="bright")
                    continue

        # 0. Daily summary (Python only, plus weather)
        if SUMMARY_Q.search(text.lower()) or MORE_SUMMARY.search(text.lower()):
            parts = [build_summary(memory, reminders, weather=lambda: None)]   # your day first
            focus_line = focus_summary_line()
            if focus_line:
                parts.append(focus_line)                                       # then what you've done
            weather_line = summary_weather(memory)
            if weather_line:
                parts.append(weather_line)                                     # weather last
            say(" ".join(parts), "☀️ daily summary (on device + weather)", tone="bright")
            continue

        # 1. Exact commands (fast, no LLM)
        if handle_command(text, memory, history, reminders):
            continue

        # 2. Date and time (Python, offline)
        tool_answer = answer_time_question(text)
        if tool_answer:
            say(tool_answer, "🕐 device clock (offline)")
            continue

        # 3. Obvious weather / news (rules)
        online_answer = answer_online_question(text, memory.get_setting("home_city"))
        if online_answer:
            say(online_answer, "🌐 internet (rule)")
            continue

        # During a game, anything that isn't a move or a real command gets a reprompt, never the LLM
        act = game_reprompt()
        if act:
            dashboard.update(activity=act["screen"])
            say(act["say"], "🎮 game (on device)", tone="bright")
            follow_up, follow_count, chime_next = True, 0, False
            continue

        # 4. Gate -> LLM router -> validated tool (lists or internet)
        found = [] if _guest else memory.search(text)      # a guest never gets the user's memories
        score, category = gate(text, example_vecs)
        print(f"🚦 Gate: {score:.2f} ({category})")
        wants_lists = category == "lists" and score >= GATE_THRESHOLD
        wants_online = category != "lists" and score >= GATE_THRESHOLD and not found
        if wants_lists or wants_online:
            t1 = time.time()
            call = route(text, LIST_TOOLS if wants_lists else TOOLS)
            if call is None and wants_online and score >= STRONG_GATE:
                call = {"name": category, "arguments": {}}
                print(f"🚦 Router hesitated, gate is confident → {category}")
            print(f"🧭 Router: {call['name'] + ' ' + str(call.get('arguments')) if call else 'no tool'}  ({time.time() - t1:.1f}s)")
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
        reply = for_speech(reply)
        recent = [m["content"] for m in history if m["role"] == "assistant"][-2:]
        if reply in recent:                                 # stuck in a loop: start the chat fresh
            print(f"   (repeated reply, resetting chat: {reply})")
            del history[1:]
            reply = "Sorry, I think I'm going in circles. Could you say that in a different way?"
        print(f"🤔 Confidence: {confidence:.2f}  ({time.time() - start:.1f}s)")
        route_name = "📚 memory (on device)" if found else "🧠 local LLM"

        # "I'm not sure" mode: low confidence on factual questions = likely a guess
        declined = re.search(r"don't (know|have)|not sure|no access", reply.lower())
        is_question = re.match(
            r"(what|who|when|where|which|why|how (?:many|much|old|far|long|big)|is|are|was|were|does|did)\b",
            text.lower())
        about_assistant = re.search(r"\b(you|your|yourself)\b", text.lower())
        if not found and not declined and is_question and not about_assistant and confidence < CONFIDENCE_THRESHOLD:
            print(f"   (withheld guess: {reply})")
            if re.search(r"\bmy\b", text.lower()):        # about their own life, and nothing saved
                reply = "I don't have that saved. You can tell me, and I'll remember it."
            else:
                reply = "Hmm, I'm not sure about that one, and I'd rather not guess wrong."
            route_name = "🤔 not sure (guess withheld)"

        # Block false claims: the LLM can't set alarms, change lists, send messages, or make calls
        promise = re.search(
            r"\b(i'll|i will|i've|i have|let me)\s+(?:make sure\b|"
            r"(?:remind|set|call|text|send|book|order|schedule|add|save|remove|note|check"
            r"|clear|delete|cancel|change|update|adjust|fix))"
            r"|\b(added|removed|saved|scheduled|noted)\b",
            reply.lower())
        if promise and not declined and not route_name.startswith("🤔"):   # our own "not sure" lines are honest
            print(f"   (blocked false promise: {reply})")
            reply = "I can't do that yet, sorry. Ask me what I can do, and I'll tell you."
            route_name = "🛡️ false promise blocked"

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        history = history[:1] + history[-6:]   # keep only recent turns, stays fast
        tone = "gentle" if route_name.startswith(("🤔", "🛡️")) else "calm"
        say(reply, route_name, round(confidence, 2), tone)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")