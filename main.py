import sys
import json
import math
import os
import random
import re
import threading
import time
import socket
import subprocess
import queue
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
from tts import speak as _tts_speak, set_voice
from memory import MemoryStore, _embed, embed_many
from tools import answer_time_question, resolve_dates
from online import answer_online_question, run_tool_call, get_weather, check_place, LAST_CALL, check_internet
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
from lookup import handle_lookup, offer_lookup as _offer_lookup, cancel_lookup
from period import handle_period
from cycle import handle_cycle, is_cycle_question, clear_cycle_log, cycle_nudge_due, cycle_summary, is_symptom_log
from share import share_list, stop_sharing

# Testing aid: "python main.py --log" also writes everything to session_log.txt (never committed)
if "--log" in sys.argv:
    class _Tee:
        def __init__(self, *streams):
            self.streams = streams
        def write(self, data):
            for s in self.streams:
                s.write(data)
                s.flush()
        def flush(self):
            for s in self.streams:
                s.flush()
    sys.stdout = _Tee(sys.stdout, open("session_log.txt", "a", encoding="utf-8"))

# ---------- Audio ----------
STT_MODEL = "base.en"           # everyday commands: fast
MEETING_STT_MODEL = "small.en"  # meetings: slower, but hears names and accents much better
SAMPLE_RATE = 16000
CHUNK = 1280                # 80 ms
MAX_RECORD_SECONDS = 20    # longest question allowed
SILENCE_TO_STOP = 2.0       # seconds of quiet that ends the question
NO_SPEECH_TIMEOUT = 4.0     # after the wake word: give up if nothing is said
FOLLOW_UP_SECONDS = 7.0     # conversation mode: how long to wait for a reply without the wake word
MAX_FOLLOW_UPS = 10         # after this many turns without the wake word, go back to waiting for it
WAKE_WORD = "hey_jarvis"    # or "hey_mycroft"
WAKE_THRESHOLD = 0.5
MIC_DEVICE = None           # on the Pi: part of the USB mic's name (e.g. "USB"), so Jarvis never silently
                            # switches to another microphone when the mute switch cuts the USB mic
REMINDER_CHECK_EVERY = 12   # chunks (~1 second) between reminder checks while waiting
REMINDER_DUE = "reminder_due"

# ---------- Reasoning ----------
CONFIDENCE_THRESHOLD = 0.85   # from testing: facts 0.9+, guesses below 0.75
GATE_THRESHOLD = 0.45         # below this: no tool needed, skip the router
STRONG_GATE = 0.70            # above this: trust the gate even if the router hesitates (online only)

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
LLM_MODEL = "qwen2.5:1.5b"
NUM_CTX = 2048                # our replies are short; a smaller context saves RAM on the Pi
HISTORY_TURNS = 4             # how many recent exchanges the LLM remembers (more = slower on the Pi)
CHAT_RESET_MINUTES = 15       # after this long without talking, a new conversation starts

# ---------- Personality ----------
ASSISTANT_NAME = "Jarvis"
INTRO = (f"I'm {ASSISTANT_NAME}, your private companion. I live right here on this device, "
         "so everything you tell me stays with you.")
CAPABILITIES = ("I can remember things for you, keep lists like groceries, ideas and your schedule, "
                "set reminders and timers, run focus sessions with healthy breaks, "
                "give you a summary of your day, tell you the date and time, "
                "play simple games, listen in meetings for dates and tasks, "
                "check the weather or news, look things up online if you ask, tell you what's planned for any day, week or month, "
                "keep a private period tracker, "
                "answer quietly on the screen when you need, "
                "and help you with the right numbers in an emergency. "
                "And if I'm not sure about something, I'll tell you instead of guessing.")
END_CONVERSATION = re.compile(
    r"\b(bye|goodbye|good ?night|that's all|thats all|that is all|nothing else|stop listening"
    r"|talk to you later|ttyl|see you|talk soon)\b")
# What Jarvis really can do, per area: answered from code, so the model never invents a feature
FEATURE_SECTIONS = [
    ("wellbeing", r"well[- ]?being|\bcalm|breath\w*|stress|anxi\w*|mental|relax\w*|exercises?"),
    ("focus", r"\bfocus|productiv\w*|pomodoro"),
    ("games", r"\bgames?\b"),
    ("lists", r"\b(?:grocer\w*|shopping|to-?do|tasks? list|my lists)\b"),
    ("period", r"\bperiods?\b|\bcycle\b"),
    ("meeting", r"\bmeetings?\b"),
    ("emergency", r"emergenc\w*|first aid|safety"),
    ("privacy", r"privacy|private|internet|online|offline"),
]
FEATURE_HELP = {
    "wellbeing": "In well-being, I can do a physiological sigh, slow counted breathing, 4 7 8 breathing for sleep, "
                 "a 5 4 3 2 1 grounding exercise, the butterfly hug, a thought dump where you talk and I just listen, "
                 "and gentle journaling prompts. Just tell me how you feel, or say: let's do a butterfly hug.",
    "focus": "In focus, I run sessions with healthy breaks. Say: let's focus for 25 minutes on emails. "
             "I can tell you how long is left, park stray thoughts for later, pause, and check your energy at breaks.",
    "games": "I can play tic-tac-toe, a memory sequence game, mental math, and guess the number. Say: let's play a game.",
    "lists": "I keep your grocery list, tasks, ideas and schedule. I can add and remove things, keep counts like "
             "6 eggs, tick things off when you say you bought them, clear a list, and share any list to your phone "
             "with a QR code.",
    "period": "I keep a private period tracker: start and end dates, symptoms, your cycle day, the next estimate, "
              "patterns, a discreet reminder, and a summary for your doctor. It never leaves this device.",
    "meeting": "In meeting mode, I listen quietly for plans and tasks, then ask before saving anything. "
               "The conversation itself is never kept.",
    "emergency": "In an emergency, I give you the right numbers like 1 1 2, your emergency contacts, and simple "
                 "first aid for burns, cuts, sprains, dizziness and more. It works offline.",
    "privacy": "Everything runs on this device. I only go online for the weather, the news or a look-up you ask for, "
               "every call is shown on my screen, and quiet mode lets me answer on the screen only.",
}
JOKES = [
    "Why don't scientists trust atoms? Because they make up everything.",
    "Why did the scarecrow win an award? Because he was outstanding in his field.",
    "What do you call a fake noodle? An impasta.",
    "Why did the bicycle fall over? It was two tired.",
    "What do you call a bear with no teeth? A gummy bear.",
    "Why did the math book look sad? It had too many problems.",
    "Why don't eggs tell jokes? They'd crack each other up.",
    "What did the ocean say to the beach? Nothing, it just waved.",
    "Why was the computer cold? It left its Windows open.",
    "What do you call a sleeping dinosaur? A dino-snore.",
    "How does a penguin build its house? Igloos it together.",
    "Why can't a nose be twelve inches long? Because then it would be a foot.",
]
_told = []          # the last few jokes, so they don't repeat

START_OVER = re.compile(r"\b(start (?:over|again|fresh|from scratch)|reset (?:the )?conversation|new conversation)\b")

SYSTEM_PROMPT = (
    f"You are {ASSISTANT_NAME}, a warm, friendly companion who lives on this device. "
    "Reply in one or two short spoken sentences, with no lists, emojis or symbols. "
    "Never make up facts: if you are not sure, say you don't know. "
    "If the user's saved memories answer the question, use only the relevant part, and copy any date exactly. "
    "If something about the user isn't in their memories, say you don't have it saved; never guess names, gender or age. "
    "You cannot set, save, change or send anything, so never claim you did. "
    "Give no medical advice; kindly suggest a doctor. "
    "Call the user by the name in the system message. If asked how you are, answer warmly and ask about them. "
    "Never call yourself a computer program."
    " Never make up things you did or felt, like sleeping, eating, waking up or going out."
    " You cannot go online yourself; the device only checks the weather, news or a look-up when asked."
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
_offer_remember = None   # "I love badminton" -> "Should I remember that?"
_turn_start = None
_last_list = None        # "what's left?" reads the list we just talked about
_quiet = False           # quiet mode: answers on screen only (emergencies still speak)
_lookup_at = None        # when "want me to look it up?" was offered (the offer expires)


def offer_lookup(text):
    global _lookup_at
    _lookup_at = time.time()
    _offer_lookup(text)

_offline_mode = False    # offline mode: nothing but this device's own services can be reached
_mic_muted = False       # muted from the screen: the microphone isn't even opened
_mic_ok = True           # is the microphone there? (the mute switch cuts its power)
MIC_OFF = "mic_off"      # what listen() returns while the microphone is off
GUEST_MINUTES = 10

# Offline mode (from the touch screen or by voice): only this device's own services can be reached
_real_request = requests.Session.request


def _guarded_request(self, method, url, *args, **kwargs):
    if _offline_mode and not re.match(r"https?://(?:127\.0\.0\.1|localhost)[:/]", str(url)):
        raise requests.ConnectionError("offline mode is on: nothing leaves this device")
    return _real_request(self, method, url, *args, **kwargs)


requests.Session.request = _guarded_request


def handle_actions(memory):
    """Buttons pressed on the touch screen: quiet mode, offline mode, mic mute, close a card."""
    global _quiet, _offline_mode, _mic_muted
    while not dashboard.ACTIONS.empty():
        action = dashboard.ACTIONS.get()
        if action == "toggle_quiet":
            _quiet = not _quiet
            memory.set_setting("quiet_mode", "on" if _quiet else "off")
            print(f"👆 Screen: quiet mode {'on' if _quiet else 'off'}")
        elif action == "toggle_offline":
            _offline_mode = not _offline_mode
            memory.set_setting("offline_mode", "on" if _offline_mode else "off")
            print("👆 Screen: offline mode " + ("on: nothing leaves this device" if _offline_mode else "off"))
        elif action == "toggle_mic":
            _mic_muted = not _mic_muted
            memory.set_setting("mic_muted", "on" if _mic_muted else "off")
            print("👆 Screen: microphone " + ("muted (it isn't even opened)" if _mic_muted else "on"))
        elif action == "close_card":
            dashboard.update(activity=None)
            stop_sharing()
            print("👆 Screen: card closed (any share link is closed too)")
        dashboard.update(quiet=_quiet, offline_mode=_offline_mode, mic_muted=_mic_muted)


WHO_TALKING = re.compile(
    r"\bwho (?:are you|am i) (?:talking|speaking|interacting|chatting) (?:to|with)\b"
    r"|\bwho are you (?:interacting|talking|chatting) with\b|\bwho(?:'s| is) (?:talking|speaking) (?:to you|now)\b")
PRIVATE_Q = re.compile(
    r"\b(?:my day|summary|remember|memories|memory|saved|lists?|grocery|groceries|schedule|reminders?"
    r"|emergency contacts?|friends?|sister|brother|journal)\b")
MORE_SUMMARY = re.compile(
    r"\b(?:daily|day'?s|today'?s|entire|full|whole) summary\b|\bsummary of (?:my|the) (?:day|tasks?)\b"  
    r"|\bdescribe (?:the|my) day\b|\bhow does my day look\b")
BULLET = re.compile(r"^\s*(?:[*\-•]|\d+[.)])\s+")
# The semantic gate and the LLM router only run when the sentence has a word from that area.
# Small talk ("How was the day?") skips both and goes straight to one LLM answer: much faster.
GATE_KEYWORDS = {
    "get_weather": re.compile(r"\b(?:weather|rain\w*|temperature|hot|cold|umbrella|wear|forecast|sunny|"
                              r"humid\w*|degrees|snow\w*|wind\w*|storm\w*|outside|jacket)\b"),
    "get_news": re.compile(r"\b(?:news|headlines?|happening|current events)\b"),
    "lists": re.compile(r"\b(?:list|lists|add|put|remove|delete|capture|note|grocer\w*|shopping|"
                        r"schedule|ideas?|tasks?)\b"),
}

NOISE_TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\*[^*]*\*")   # [BLANK_AUDIO], (music), *cough*

# Whisper sometimes "hears" these in a short burst of noise; ignore them when the recording was tiny
WHISPER_GHOSTS = {"for you", "you", "thanks for watching", "thank you for watching", "so"}

STOP_EXERCISE = re.compile(r"^(?:stop|enough|that's enough|i'm done|i am done|quit|cancel|no more|skip it)\b"
                           r"|\bstop (?:it|this|the exercise|grounding)\b|\blet'?s stop\b")

# Wanting to hurt someone else (harm to oneself is handled earlier, by the crisis path)
HARM_INTENT = re.compile(
    r"\b(?:i (?:want|wanna|am going|'m going|will|plan|need) to|how (?:do i|to|can i|would i)|help me"
    r"|(?:look|search)(?: it)? up(?: online)?(?: about)?(?: how to)?|tell me how to)\b.*"
    r"\b(?:kill|murder|hurt|harm|attack|stab|shoot|poison|kidnap|rape|beat up|bomb)\b"
    r"(?!\s+(?:myself|me)\b)")

# Everyday phrases that only sound violent
HARM_EXCLUDE = re.compile(
    r"\bkill(?:ing)? (?:some |the )?time\b|\bkill (?:the )?(?:lights?|process|app|task|music|engine)\b"
    r"|\bshoot (?:a |an |the )?(?:photo|video|picture|email|mail|message)\b|\bkilling it\b"
    r"|\bbomb(?:ed)? (?:the |my )?(?:exam|test|interview)\b")

# Serious signs that must always win over a milder first-aid match ("cramps and chest pain")
SEVERE = re.compile(r"\b(?:chest pain|can'?t breathe|cannot breathe|trouble breathing|faint(?:ed|ing)|passed out"
                    r"|unconscious|a lot of blood|bleeding (?:a lot|heavily)|seizure|stroke)\b")
# ---------- Output ----------
def speak(text, tone="calm"):
    """Speak aloud, unless quiet mode is on (then the screen shows it instead)."""
    if not _quiet:
        _tts_speak(text, tone)


def say(text, route, confidence=None, tone="calm"):
    """Speak a reply (with any pending greeting in front) and show it on the dashboard."""
    global _greeting, _turn_start
    text = (_greeting + " " + text).strip()
    _greeting = ""
    on_screen_only = _quiet and not route.startswith("🚨")     # emergencies are always spoken
    print(f"🔇 Jarvis (on screen): {text}" if on_screen_only else f"🔊 Jarvis: {text}")
    if _turn_start:
        print(f"   ⏱️ reply after {time.time() - _turn_start:.1f}s")
        _turn_start = None
    dashboard.update(status="speaking", last_reply=text, route=route, confidence=confidence,
                     tone=tone, quiet=_quiet, reply_at=time.time())
    if _quiet and route.startswith("🚨"):
        _tts_speak(text, tone)                     # emergencies always speak, even in quiet mode
    else:
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
            "model": LLM_MODEL, "stream": False, "keep_alive": "24h", "messages": messages,
            "options": {"temperature": 0.3, "num_predict": 40, "num_ctx": NUM_CTX},
        }, timeout=15)
        r.raise_for_status()
        line = (r.json()["message"].get("content") or "").strip()
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"   (empathy line failed: {e})")
        return None
    line = re.split(r"(?<=[.!])\s", line)[0].strip()          # first sentence only
    if not line or len(line.split()) > 25 or "?" in line or EMPATHY_BLOCK.search(line.lower()):
        print(f"   (empathy line rejected: {line})")
        return None                                            # the checked line from the file is used instead
    return line


IDEA_PROMPT = ("Suggest ONE simple, healthy thing a person can do right now, at home or at their desk, "
               "to beat boredom. Reply with one short sentence, under 15 words. No lists, no questions.")


def fresh_idea(avoid=()):
    """One new activity idea from the local LLM, checked. None if it fails a check."""
    content = IDEA_PROMPT + (f" Do not suggest: {', '.join(avoid)}." if avoid else "")
    try:
        r = requests.post(OLLAMA_URL, json={
            "model": LLM_MODEL, "stream": False, "keep_alive": "24h",
            "messages": [{"role": "system", "content": content}, {"role": "user", "content": "I'm bored."}],
            "options": {"temperature": 0.9, "num_predict": 30, "num_ctx": NUM_CTX},   # a little variety
        }, timeout=15)
        r.raise_for_status()
        line = (r.json()["message"].get("content") or "").strip()
    except (requests.RequestException, ValueError, KeyError) as e:
        print(f"   (idea failed: {e})")
        return None
    line = re.split(r"(?<=[.!])\s", for_speech(line))[0].strip()
    line = re.sub(r"^(?:i'll|i will|i'd|i would|you could|you can|you should|try to|maybe)\s+", "", line, flags=re.I)
    if not line or len(line.split()) > 20 or "?" in line or is_unsafe(line):
        print(f"   (idea rejected: {line})")
        return None
    return line[0].lower() + line[1:]

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
                if STOP_EXERCISE.search(heard.lower()):      # "stop" in the middle of an exercise
                    dashboard.update(activity=None)
                    say("Okay, we'll stop here. I'm here whenever you need me.", "🌿 wellbeing (on device)", tone="gentle")
                    return
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
                           # quiet mode: a soft chime, not a loud one

def _chime(first_hz, second_hz, volume, wait=True):
    if _quiet:
        volume *= 0.25                             # quiet mode: a soft chime, not a loud one
    rate = 44100
    t = np.linspace(0, 0.12, int(0.12 * rate), False)
    fade = np.linspace(1, 0, t.size)
    tone = np.concatenate([np.sin(2 * np.pi * first_hz * t), np.sin(2 * np.pi * second_hz * t)])
    tone = volume * tone * np.concatenate([fade, fade])
    sd.play(tone.astype(np.float32), rate)
    if wait:
        sd.wait()


def ding():
    """Rising chime: 'I'm listening, go ahead.'"""
    _chime(660, 990, 0.6)

def think_tick():
    """A soft, short tick: 'I heard you, I'm thinking.' Plays while the LLM starts, instead of before it."""
    _chime(620, 620, 0.12, wait=False)


def sleep_chime():
    """Falling chime: 'I've stopped listening, say Hey Jarvis to wake me again.'"""
    _chime(990, 660, 0.4)


def rms(frame):
    """Loudness of one audio chunk."""
    return float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


def pick_mic():
    """The microphone to use: the default one, or the one whose name contains MIC_DEVICE."""
    if not MIC_DEVICE:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and MIC_DEVICE.lower() in d["name"].lower():
            return i
    raise RuntimeError(f"no microphone named '{MIC_DEVICE}'")


def _set_mic(ok):
    """Show the microphone state on screen (only when it changes)."""
    global _mic_ok
    if ok != _mic_ok:
        print("🎙️ Microphone is back." if ok else "🔇 Microphone is off (mute switch?). Waiting for it...")
    _mic_ok = ok
    dashboard.update(mic=ok)


def listen(*args, **kwargs):
    """Like _listen_inner, but if the microphone is missing (mute switch), wait calmly instead of crashing."""
    if _mic_muted:                                 # muted from the screen: the microphone isn't even opened
        dashboard.update(status="waiting")
        time.sleep(0.5)
        return MIC_OFF
    try:
        return _listen_inner(*args, **kwargs)
    except (sd.PortAudioError, RuntimeError, OSError):
        _set_mic(False)
        dashboard.update(status="waiting")
        time.sleep(2)
        try:                                       # look for the microphone again
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        return MIC_OFF                             # a string: callers just try again

class MeetingRecorder:
    """Records the meeting non-stop in the background and cuts it at natural pauses,
    so nothing is lost while the previous piece is being written down."""

    def __init__(self, level):
        self.pieces = queue.Queue()
        self.level = level
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        chunk_sec = CHUNK / SAMPLE_RATE
        frames, quiet, voiced = [], 0.0, 0.0
        try:
            with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                blocksize=CHUNK, device=pick_mic()) as stream:
                while not self._stop.is_set():
                    frame, _ = stream.read(CHUNK)
                    frame = frame.flatten()
                    frames.append(frame)
                    if rms(frame) > self.level:
                        voiced, quiet = voiced + chunk_sec, 0.0
                    else:
                        quiet += chunk_sec
                    length = len(frames) * chunk_sec
                    if (length >= 4 and quiet >= 0.8) or length >= 20:      # a natural pause, or 20 s at most
                        if voiced >= 0.5:                                   # only pieces with someone speaking
                            self.pieces.put(np.concatenate(frames).astype(np.float32) / 32768.0)
                        frames, quiet, voiced = [], 0.0, 0.0
        except Exception:
            self.pieces.put(None)                                           # the microphone went away

    def stop(self):
        self._stop.set()
        self.thread.join(timeout=2)

def _listen_inner(wake, reminders, follow_up=False, max_seconds=MAX_RECORD_SECONDS,
           silence=SILENCE_TO_STOP, timeout=FOLLOW_UP_SECONDS, level=None):
    """Wake-word mode: wait for 'Hey Jarvis', then record (and keep an eye on due reminders).
    Follow-up mode: skip the wake word and just listen briefly for a reply.
    level: a lower loudness level for quieter voices (meetings).
    Returns the audio, REMINDER_DUE if a reminder needs announcing, or None if nobody spoke."""
    global _threshold
    chunk_sec = CHUNK / SAMPLE_RATE
    
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
        dtype="int16", blocksize=CHUNK, device=pick_mic()) as stream:
        if follow_up:
            print("\n💬 Still listening, no wake word needed...")
            no_speech_timeout = timeout
        else:
            wake.reset()
            noise = deque(maxlen=50)    # background loudness over the last ~4 seconds
            print("\n💤 Waiting for wake word...")
            dashboard.update(status="waiting")
            checks = 0
            zero_run = 0
            while True:
                frame, _ = stream.read(CHUNK)
                frame = frame.flatten()
                noise.append(rms(frame))
                if not dashboard.ACTIONS.empty():
                    return REMINDER_DUE                    # a button was tapped on the screen: handle it now
                if frame.any():
                    zero_run = 0
                    if not _mic_ok:
                        _set_mic(True)                     # real sound again: the mic is back
                else:
                    zero_run += 1
                    if zero_run > 25:                      # ~2 s of perfect zeros: a real mic always hears a little
                        raise RuntimeError("the microphone gives no signal")
                score = max(wake.predict(frame).values())
                if score > 0.2 and "--wake-debug" in sys.argv:
                    print(f"   (wake score {score:.2f})")
                if score > WAKE_THRESHOLD:
                    prewarm()       # LLM starts loading while the user is still speaking
                    break
                checks += 1
                if checks % REMINDER_CHECK_EVERY == 0 and (
                       focus_due() or cycle_nudge_due(peek=True) or not dashboard.ACTIONS.empty()
                        or (reminders.due_now() and not is_focusing() and not meeting_active())):
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
            if not heard_speech and not dashboard.ACTIONS.empty():
                return REMINDER_DUE                        # a button was tapped: handle it right away
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
    words.update({ASSISTANT_NAME, "Tic-tac-toe", "Memory sequence", "Mental math",
                  "Quiet mode on", "Quiet mode off", "Meeting mode on", "Meeting mode off",
                  "QR code", "Close the QR code", "grocery list"})   # app words
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
        "keep_alive": "24h",
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
    now = datetime.now()
    return {"role": "system", "content": f"{who}Today is {now:%A}, {now.day} {now:%B %Y}, in the {part}."}

def os_internet():
    """Ask the operating system whether it has internet. Jarvis itself sends nothing:
    Windows and NetworkManager (on the Pi) check this on their own. None if we can't tell."""
    try:
        if os.name == "nt":
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-NetConnectionProfile).IPv4Connectivity"],
                                 capture_output=True, text=True, timeout=5, creationflags=0x08000000).stdout
            return "Internet" in out
        out = subprocess.run(["nmcli", "-t", "networking", "connectivity"],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out == "full"
    except Exception:
        return None

def network_up():
    """True if this device has a route to the internet. Sends nothing: a UDP 'connect' only asks the
    operating system for a route; no packet leaves the device."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
        return True
    except OSError:
        return False


def watch_network():
    """Every 5 seconds, update the online/offline icon (in the background)."""
    def _loop():
        last, had_route, os_ok, checked = None, True, None, 0.0
        while True:
            route = network_up()
            if route and not had_route:                   # just reconnected: check again straight away
                LAST_CALL["failed"], checked = 0.0, 0.0
            had_route = route
            if time.time() - checked > 10:                # ask the system every 10 s (cheap, sends nothing)
                new_os = os_internet() if route else False
                if new_os and os_ok is False:
                    LAST_CALL["failed"] = 0.0             # the internet came back
                os_ok, checked = new_os, time.time()
            up = route and os_ok is not False and LAST_CALL["failed"] <= LAST_CALL["ok"]
            if up != last:
                print("🌐 Network is up." if up else "✈️ Offline: everything on the device still works.")
                dashboard.update(online=up)
                last = up
            time.sleep(2)
    threading.Thread(target=_loop, daemon=True).start()


def prewarm(read_prompt=False):
    """Keep the LLM loaded. With read_prompt (at startup and after 'forget everything'), also read the system
    prompt once so the first answer is fast. On the wake word we only load: reading again would push the
    current conversation out of the model's cache."""
    def _load():
        payload = {"model": LLM_MODEL, "stream": False, "keep_alive": "24h", "messages": [],
                   "options": {"num_ctx": NUM_CTX}}   # same context size as every answer, or Ollama reloads the model
        if read_prompt:
            payload["messages"] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": "hi"}]
            payload["options"] = {"temperature": 0, "num_predict": 1, "num_ctx": NUM_CTX}
        try:
            requests.post(OLLAMA_URL, json=payload, timeout=120)
            if read_prompt:
                print("   ✔ language model warmed up (answers will be quick now)")
        except requests.RequestException:
            pass
    threading.Thread(target=_load, daemon=True).start()

def clear_llm_cache():
    """Unload the model, which also wipes its cache from RAM ('forget everything' should mean everything)."""
    try:
        requests.post(OLLAMA_URL, json={"model": LLM_MODEL, "messages": [], "keep_alive": 0}, timeout=10)
    except requests.RequestException:
        pass


def think(messages):
    """Returns (reply, confidence). Confidence comes from the model's own token probabilities."""
    response = requests.post(OLLAMA_URL, json={
        "model": LLM_MODEL,
        "messages": messages,
        "stream": False,
        "keep_alive": "24h",
        "logprobs": True,
        "options": {"temperature": 0, "num_predict": 45, "num_ctx": NUM_CTX},
    }, timeout=120)
    if response.status_code != 200:
        print("⚠️ Ollama says:", response.text)
    response.raise_for_status()
    data = response.json()
    reply = (data["message"].get("content") or "").strip()
    read_n, write_n = data.get("prompt_eval_count", 0), data.get("eval_count", 0)
    read_s, write_s = data.get("prompt_eval_duration", 0) / 1e9, data.get("eval_duration", 0) / 1e9
    load_s = data.get("load_duration", 0) / 1e9
    print(f"   (LLM read {read_n} tokens in {read_s:.1f}s, wrote {write_n} in {write_s:.1f}s"
          + (f", model load {load_s:.1f}s" if load_s > 0.5 else "") + ")")
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

def private_names(memory):
    """Names that must never leave the device in a search: the user and the people they've told me about."""
    names = {memory.get_setting("user_name")}
    for t in memory.all():
        m = re.search(r"\bname is (\w+)|^(\w+) is a friend of", t.split(" (saved on")[0], re.I)
        if m:
            names.add(m.group(1) or m.group(2))
    return {n for n in names if n}


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
    bullets = [BULLET.sub("", l).strip().rstrip(".") for l in lines if BULLET.match(l)]   
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
    global _expect_city, _quiet, _offline_mode, _mic_muted
    lower = text.lower().strip(" .!?,")
    # Closing the QR card: any wording while it's on screen ("close it", "closer QR", "close the viewer", "done")
    qr_on_screen = bool((dashboard.STATE.get("activity") or {}).get("qr"))
    asks_close = re.search(r"\b(?:clos\w*|hide|remove|dismiss|take (?:it )?(?:away|off)|get rid of|go away)\b", lower)
    if qr_on_screen and (asks_close or re.fullmatch(r"(?:ok(?:ay)?\s*)?(?:done|scanned|i(?:'ve| have)? scanned it|got it)", lower)):
        import share
        share._shares.clear()                            # the share link closes too, not just the picture
        dashboard.update(activity=None)
        say("Done. The code is gone, and the link is closed too.", "📤 share closed")
        return True
    if not qr_on_screen and asks_close and re.search(r"\b(?:q\s?r|code|viewer)\b", lower):
        say("There's no code on my screen right now.", "📤 share (on device)")
        return True
    # "Is the mic on?" / "is Mike on or off?": the real state, never a guess
    if re.search(r"\b(?:mic|mike|microphone)\b", lower) and re.search(r"\b(?:on|off|working|status|listening)\b", lower) \
            and not re.search(r"\bmute\b", lower):
        say("My microphone is on, and I'm listening. Tap the mic button on my screen, or say mute the mic, "
            "to turn it off.", "🎙️ mic (real state)")
        return True
    # Offline mode by voice: no weather, news or look-ups at all; everything else still works
    m = re.search(r"\b(?:offline|airplane) mode (on|off)\b|\b(?:turn|switch) (on|off) (?:the )?(?:offline|airplane) mode\b",
                  lower)
    if m:
        _offline_mode = (m.group(1) or m.group(2)) == "on"
        memory.set_setting("offline_mode", "on" if _offline_mode else "off")
        dashboard.update(offline_mode=_offline_mode)
        say("Offline mode on. I won't go online at all, not even for the weather. Everything else still works."
            if _offline_mode else "Offline mode off. I can check the weather, news and look-ups again when you ask.",
            "✈️ offline mode")
        return True
    if re.search(r"\bmute (?:the |your )?(?:mic|microphone)\b", lower):
        _mic_muted = True
        memory.set_setting("mic_muted", "on")
        dashboard.update(mic_muted=True)
        say("Microphone muted. Tap the mic button on my screen to turn me back on.", "🔇 mic muted")
        return True
    # "Close the QR code" / "hide the code" / "clear the screen": the card goes, and any share link closes
    if re.search(r"\b(?:remove|hide|close|clear|dismiss|take (?:away|off)|get rid of)\b.*\b(?:qr|q r|code|card|screen)\b"
                 r"|\bi(?:'ve| have)? scanned it\b", lower):
        dashboard.update(activity=None)
        stop_sharing()
        say("Done. The code is gone, and the link is closed too.", "📤 share closed")
        return True
    # Quiet mode: answers on screen only (for the office, the library, late at night, private things)
    if re.search(r"\b(?:quiet|quite|silent) mo(?:de|od|ved|re) off\b|\b(?:turn|switch) off (?:the )?(?:quiet|silent) mode\b"
                 r"|\bspeak (?:again|out loud)\b|\bunmute (?:yourself|your voice)\b|\byou can talk\b", lower):
        _quiet = False
        memory.set_setting("quiet_mode", "off")
        say("I'm talking again.", "🔈 quiet mode off", tone="bright")
        return True
    if re.search(r"(?<!i've )(?<!have )\b(?:quiet|quite|silent) mo(?:de|od|ved|re)(?: on)?$|\b(?:turn|switch) on (?:the )?(?:quiet|silent) mode\b"
                 r"|^be quiet\b|\bmute (?:yourself|your voice)\b|\bdon'?t speak\b", lower):
        _quiet = True
        memory.set_setting("quiet_mode", "on")
        say("Quiet mode on. I'll answer on the screen. Emergencies will still be spoken.", "🔇 quiet mode on")
        return True
    # "set a reminder for 7 pm to call papa" -> "remind me at 7 pm to call papa"
    m = re.match(r"^(?:please\s+)?(?:set|add|create) (?:a )?reminder (?:for|at) (.+?) to (.+)$", lower)
    if m:
        text = f"remind me at {m.group(1)} to {m.group(2)}"

    reminder_reply = handle_reminder_command(text, reminders)
    if reminder_reply:
        say(reminder_reply, "⏰ reminders (on device)")
        return True

    # ----- Lists: add (no duplicates), remove (every match), show (counted), "what's left?" -----
    global _last_list

    def list_name(said):
        said = " ".join(said.split())
        n = said[:-3] + "y" if said.endswith("ies") else said[:-1] if said.endswith("s") and not said.endswith("ss") else said
        n = {"shopping": "grocery"}.get(n, n)
        return n

    def items_of(said):
        """'milk, 2 eggs and the bread' -> ['milk', '2 eggs', 'bread'] (numbers are kept for quantities)."""
        parts = [re.sub(r"^(?:the|some|a few|few)\s+", "", p.strip()) for p in re.split(r",|\band\b", said)]
        return [p for p in parts if p]

    NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "a couple of": 2, "a dozen": 12,
            "dozen": 12, "half a dozen": 6}

    def split_qty(item):
        """'2 eggs' -> (2, 'eggs'); 'a dozen bananas' -> (12, 'bananas'); 'milk' -> (None, 'milk')."""
        item = item.strip()
        m_ = re.match(r"^(\d+|half a dozen|a couple of|a dozen|dozen|one|two|three|four|five|six|seven|eight"
                      r"|nine|ten|eleven|twelve)\s+(.+)$", item)
        if m_:
            return (int(m_.group(1)) if m_.group(1).isdigit() else NUMS[m_.group(1)]), m_.group(2)
        return None, re.sub(r"^(?:a|an)\s+", "", item)

    def same_key(word):
        """'eggs' / 'egg', 'potatoes' / 'potato', 'berries' / 'berry' -> the same key."""
        w = word.lower().strip()
        for suffix, repl in (("oes", "o"), ("ies", "y"), ("s", "")):
            if w.endswith(suffix) and len(w) > len(suffix) + 1:
                return w[:-len(suffix)] + repl
        return w

    def find_item(lst, thing):
        """The list entry for this thing, if any: ('6 eggs', 6, 'eggs')."""
        for entry in memory.list_get(lst):
            q, n = split_qty(entry)
            if same_key(n) == same_key(thing):
                return entry, q, n
        return None

    def replace_item(lst, old, new):
        memory.list_remove(lst, old)
        if new:
            memory.list_add(lst, new)

    def join(xs):
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

    def take_off(lst, raws):
        """Remove things (or just some of them): returns (gone, still_left, not_found)."""
        gone, left, missing = [], [], []
        for raw in raws:
            qty, thing = split_qty(raw)
            hit = find_item(lst, thing)
            if not hit:
                missing.append(thing)
                continue
            old, old_qty, old_name = hit
            if qty and old_qty and qty < old_qty:            # "remove 2 eggs" from "6 eggs" -> "4 eggs"
                replace_item(lst, old, f"{old_qty - qty} {old_name}")
                left.append(f"{old_qty - qty} {old_name}")
            else:                                             # all of it (and any repeats)
                while memory.list_remove(lst, old):
                    pass
                gone.append(old_name)
        return gone, left, missing

    known_lists = {list_name(n) for n in memory.list_names()} | {list_name(n) for n in STARTER_LISTS}

    def is_list_target(said):
        """'grocery list', 'groceries', 'my shopping' -> True; 'the fridge' -> False.
        The schedule is left to the older handler, which turns 'Friday' into a real date."""
        name = list_name(said)
        return (lower.endswith(("list", "list too", "list as well")) or name in known_lists) \
            and not name.startswith("sched")

    # Add: new things are added, numbers add up ("2 eggs" + "4 eggs" = "6 eggs"), no duplicates
    m = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:add|put)\s+(.+?)\s+(?:to|on|in|into)\s+(?:my\s+|the\s+)?([a-z ]+?)(?:\s*list)?$", lower)
    if m and is_list_target(m.group(2)):
        name = list_name(m.group(2))
        _last_list = name
        added, more, already = [], [], []
        for raw in items_of(m.group(1)):
            qty, thing = split_qty(raw)
            hit = find_item(name, thing)
            if hit and qty:
                old, old_qty, old_name = hit
                total = (old_qty or 0) + qty
                replace_item(name, old, f"{total} {old_name}")
                more.append(f"{total} {old_name}")
            elif hit:
                already.append(thing)
            else:
                entry = f"{qty} {thing}" if qty else thing
                memory.list_add(name, entry)
                added.append(entry)
        parts = []
        if added:
            parts.append(f"Added {join(added)} to your {name} list.")
        if more:
            parts.append(f"You now have {join(more)} on your {name} list.")
        if already:
            text_ = join(already)
            parts.append(f"{text_[0].upper() + text_[1:]} {'is' if len(already) == 1 else 'are'} already there.")
        say(" ".join(parts), "📝 lists (on device)")
        return True

    # Remove: all of it, or just some ("remove 2 eggs" from "6 eggs" leaves "4 eggs")
    m = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:remove|delete|take off|cross off)\s+(?:all\s+(?:the\s+)?)?(.+?)"
                 r"\s+from\s+(?:my\s+|the\s+)?([a-z ]+?)(?:\s*list)?(?:\s+(?:too|as well))?$", lower)
    if m and is_list_target(m.group(2)):
        name = list_name(m.group(2))
        _last_list = name
        gone, left, missing = take_off(name, items_of(m.group(1)))
        parts = []
        if gone:
            parts.append(f"Removed {join(gone)} from your {name} list.")
        if left:
            parts.append(f"There {'is' if len(left) == 1 and left[0].startswith('1 ') else 'are'} now {join(left)} left.")
        if missing:
            rest = memory.list_get(name)
            parts.append(f"I couldn't find {join(missing)}. " +
                         (f"Your list has: {', '.join(dict.fromkeys(rest))}." if rest else "The list is empty."))
        say(" ".join(parts), "📝 lists (on device)")
        return True

    # "I bought potatoes" / "I've got milk and 2 eggs": tick them off the grocery list
    m = re.match(r"^(?:(?:i|we)(?:'ve|\s+have|\s+just|\s+already)*\s+)?(?:bought|got|picked up|purchased)\s+(.+?)"
                 r"(?:\s+(?:today|already|now|just now|from the (?:market|shop|store)))?$", lower)
    if m:
        grocery = next((n for n in memory.list_names() if list_name(n) == "grocery"), "grocery")
        gone, left, _ = take_off(grocery, items_of(m.group(1)))
        if gone or left:                                   # only if it was really on the list
            _last_list = list_name(grocery)
            parts = ["Nice!"]
            if gone:
                parts.append(f"I've taken {join(gone)} off your grocery list.")
            if left:
                parts.append(f"You still need {join(left)}.")
            say(" ".join(parts), "📝 lists (on device)", tone="bright")
            return True
        # nothing matched the list ("I got a promotion"): not about shopping, let the rest handle it
     
    
    # Any way of asking for a QR code: "QR for my grocery list", "give me the QR for this", "share it with me"
    wants_qr = re.search(r"\bq\s?r\b|\bbar ?code\b", lower) or (
        re.search(r"\b(?:share|send)\b.*\b(?:it|this|that)\b", lower) and _last_list)
    # A QUESTION about sharing ("can I share the QR without Wi-Fi?") gets an answer, not another share
    if wants_qr and re.search(r"^(?:so\s+|and\s+)?(?:can|could|does|do|will|is|why|how|what)\b.*"
                              r"\b(?:if|without|wi-?fi|wifi|internet|work|works|need)\b", lower):
        say("Sharing works over your own Wi-Fi: your phone needs to be on the same Wi-Fi or hotspot as me, "
            "and nothing goes to the internet. Without Wi-Fi I can't share it, but you can always read the list "
            "on my screen. To share, say: share my grocery list.", "📤 share (how it works)")
        return True
    if wants_qr and not re.search(r"\b(?:remove|hide|clos\w*|clear|dismiss)\b", lower):
        named = next((list_name(w) for w in re.findall(r"[a-z]+", lower) if list_name(w) in known_lists), None)
        target = named or _last_list                     # "this" / "it" = the list we just talked about
        if not target:
            say("Which list should I share? For example: share my grocery list.", "📤 share (on device)")
            return True
        lower = f"share my {target} list"                # handled just below, like any other share
    # Share a list to the phone: a QR code opens it straight from this device, over the Wi-Fi, for 10 minutes
    m = re.search(r"\b(?:share|send|download|export|print)\b.*?\b(?:my\s+|the\s+)?([a-z]+?)(?:\s*list)?"
                  r"(?:\s+(?:to|with|on)\b.*)?$", lower)
    if m and ("list" in lower or list_name(m.group(1)) in known_lists):     # any list, even "my schedule"
        name = list_name(m.group(1))
        _last_list = name
        items = memory.list_get(name)
        if not items:
            say(f"Your {name} list is empty, so there's nothing to share yet.", "📤 share (on device)")
            return True
        url, svg = share_list(f"{name.title()} list", [re.sub(r"\s*\[date: (.*?)\]", r" (\1)", i) for i in items])
        if not url:
            say("I need to be on Wi-Fi to send it to your phone. It only travels over your own Wi-Fi, never the internet.",
                "📤 share (no network)")
            return True
        print(f"📤 Shared over this Wi-Fi only, for 10 minutes: {url}")
        dashboard.update(activity={"title": f"📤 {name.title()} list",
                                   "lines": ["Scan with your phone (same Wi-Fi)", "The link works for 10 minutes"],
                                   "qr": svg, "expires": time.time() + 120})
        say("Scan the code on my screen with your phone. It opens your list straight from me, over your Wi-Fi, "
            "for the next 10 minutes. You can save it as a PDF from there.", "📤 shared on this Wi-Fi (not online)")
        return True
     
    # Clear a whole list: "clear my shopping list" / "empty my grocery list"
    m = re.match(r"^(?:please\s+)?(?:can you\s+)?(?:clear|empty|wipe) (?:out )?(?:my\s+|the\s+)?([a-z ]+?)(?:\s*list)?$", lower)
    if m and is_list_target(m.group(1)):
        name = list_name(m.group(1))
        items = memory.list_get(name)
        for item in items:
            memory.list_remove(name, item)
        _last_list = name
        say(f"Done, your {name} list is empty now." if items else f"Your {name} list is already empty.",
            "📝 lists (on device)")
        return True
   

    m = re.search(r"\bwhat(?:'s| is) (?:on|in) (?:my\s+|the\s+)?([a-z ]+?)\s*list\b"
                  r"|\b(?:show|display|read)(?: me)? (?:my\s+|the\s+)?([a-z ]+?)\s*list\b", lower)
    left_q = re.search(r"\bwhat(?:'s| is) (?:left|remaining|on it)\b", lower) and _last_list
    if m or left_q:
        name = list_name(m.group(1) or m.group(2)) if m else _last_list
        _last_list = name
        items = memory.list_get(name)
        if not items:
            say(f"Your {name} list is empty.", "📝 lists (on device)")
            return True
        counts = {}
        for i in items:
            counts[i.lower()] = counts.get(i.lower(), 0) + 1
        shown = [f"{i} ({c})" if c > 1 else i for i, c in counts.items()]
        say(f"Your {name} list has: {', '.join(shown)}.", "📝 lists (on device)")
        return True
    

    list_reply = handle_list_command(text, memory)
    if list_reply:
        m_last = re.search(r"\byour (\w+) list\b", list_reply.lower())
        if m_last:
            _last_list = list_name(m_last.group(1))       # so "share it" / "the QR for this" knows which list
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
        clear_cycle_log()
        clear_session()
        del history[1:]
        clear_llm_cache()              # also wipe the model's working memory (its cache) from RAM
        prewarm(read_prompt=True)                      # load a fresh, empty copy so the next answer is quick again
        print(f"🧹 Erased {count} memories and list items, all reminders, and the model's cache")
        say("Done. I've erased everything I had saved, including my working memory.", "🧹 everything erased")
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
    global _greeting, _pending_name, _guest, _offer_remember, _last_list, _turn_start, _quiet, _lookup_at, _offline_mode, _mic_muted
    dashboard.start()
    watch_network()
    t0 = time.time()

    def step(name):
        print(f"   ✔ {name}  ({time.time() - t0:.1f}s)")

    print("Loading models...")
    prewarm(read_prompt=True)                                        # LLM loads in the background meanwhile
    try:
        wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    except Exception:
        openwakeword.utils.download_models()         # only needed the very first time
        wake = WakeModel(wakeword_models=[WAKE_WORD], inference_framework="onnx")
    step("wake word")
    stt = SttModel(STT_MODEL, n_threads=4, print_progress=False)
    meeting_stt = None                               # loaded the first time meeting mode starts
    step("speech to text")
    memory = MemoryStore()
    reminders = Reminders()
    set_voice(memory.get_setting("voice") or "male")
    _quiet = memory.get_setting("quiet_mode") == "on"   # remembered across restarts
    dashboard.update(quiet=_quiet)
    _offline_mode = memory.get_setting("offline_mode") == "on"
    _mic_muted = memory.get_setting("mic_muted") == "on"
    dashboard.update(offline_mode=_offline_mode, mic_muted=_mic_muted)
    step("memory, reminders, voice")
    example_vecs = embed_many([e for e, _ in INTENT_EXAMPLES])
    step("intent gate")
    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    last_chat = time.time()
    follow_up = False
    follow_count = 0
    chime_next = False
    recorder = None                                  # meeting mode: the non-stop recorder

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
        handle_actions(memory)
        plan = focus_tick()                          # focus or break time is up
        if plan:
            ding()
            run_plan(plan, hear)
            follow_up = True
            continue
        nudge = None if _guest else cycle_nudge_due()       # the discreet period reminder, once per cycle
        if nudge:
            ding()
            say(nudge, "🌸 private reminder (on device)", tone="gentle")
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

        # Meeting mode: record non-stop, write down each piece, note dates and tasks, never reply mid-meeting
        if meeting_active():
            dashboard.update(status="listening", activity=meeting_screen())
            if meeting_stt is None:
                print("   (loading the meeting speech model...)")
                meeting_stt = SttModel(MEETING_STT_MODEL, n_threads=4, print_progress=False)
            if recorder is None:
                recorder = MeetingRecorder(level=max(_threshold * 0.5, 150))
                print("   🎙️ (meeting: recording non-stop, cut at natural pauses)")
            try:
                audio = recorder.pieces.get(timeout=1.0)
            except queue.Empty:
                continue
            if audio is None:                                    # microphone lost: try again shortly
                recorder.stop()
                recorder = None
                _set_mic(False)
                time.sleep(2)
                continue
            t_stt = time.time()
            heard = NOISE_TAGS.sub("", transcribe(meeting_stt, audio, memory)).strip()
            print(f"   (piece of {len(audio) / SAMPLE_RATE:.0f}s written down in {time.time() - t_stt:.1f}s, "
                  f"{recorder.pieces.qsize()} waiting)")
            if not heard:
                continue
            heard = re.sub(r"\bmeeting mode of\b", "meeting mode off", heard, flags=re.I)   # a common mishearing
            print(f"   📝 (meeting) {heard}")
            help_ = handle_emergency(heard, memory)              # emergencies still come first
            if help_:
                dashboard.update(emergency=help_["banner"])
                say(help_["say"], "🚨 emergency help (offline)", tone=help_["tone"])
                continue
            # One sentence at a time, so a past or cancelled plan doesn't get mixed with a real one
            for sentence in [s.strip() for s in re.split(r"(?<=[.!?])\s+", heard) if s.strip()]:
                if re.search(r"\b(?:last (?:week|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
                             r"|yesterday|days? ago)\b", sentence.lower()):
                    print(f"   (skipped, about the past: {sentence})")
                    continue
                if re.search(r"\b(?:don'?t (?:think )?(?:we )?need to|no need to|won'?t (?:need|meet)|not going to meet)\b",
                             sentence.lower()):
                    print(f"   (skipped, a plan that was called off: {sentence})")
                    continue
                plan = meeting_chunk(sentence, memory)
                if plan:
                    if not meeting_active():                     # the meeting just ended: stop recording first
                        recorder.stop()
                        recorder = None
                    run_plan(plan, hear)
                    break
            follow_up = not meeting_active()
            continue
        if recorder:                                             # meeting ended some other way
            recorder.stop()
            recorder = None       


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
        _turn_start = time.time()
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

       # Never help hurt anyone: a calm, firm answer from code (never the LLM, never online)
        if HARM_INTENT.search(text.lower()) and not HARM_EXCLUDE.search(text.lower()):
            cancel_lookup()
            print("   (harmful request: refused)")
            say("I can't help with anything that could hurt someone. If you're feeling angry or overwhelmed, "
                "I'm here to talk it through. And if anyone is in danger right now, please call 1 1 2.",
                "🛡️ refused (safety)", tone="gentle")
            follow_up, follow_count, chime_next = True, 0, False
            continue

        # Emergency help comes before everything else: no LLM, works offline
        help_ = None if is_symptom_log(text) else handle_emergency(text, memory)   # "log cramps" is a note, not first aid
        if help_ and (help_.get("banner") or {}).get("kind") == "first_aid":
            severe = SEVERE.search(text.lower())
            if severe:                                     # a serious sign in the same sentence: emergency help instead
                help_ = handle_emergency(severe.group(0), memory) or help_
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

        if _lookup_at and (time.time() - _lookup_at > 60
                           or re.search(r"\b(?:thanks?|thank you|bye|goodbye|talk soon|see you)\b", text.lower())):
            cancel_lookup()                                  # an old or polite "okay" is not a yes
            _lookup_at = None

        # "Look it up?": only after the user agrees, or asks directly. Personal questions never leave.
        looked = handle_lookup(text, private_names(memory))
        if looked:
            say(looked[0], looked[1])
            follow_up, follow_count, chime_next = True, 0, False
            continue


        # Period tracker: private, on this device only, never in guest mode (before wellbeing, so "log low mood" is a note)
        if is_cycle_question(text):
            if _guest:
                say(f"That's {memory.get_setting('user_name') or 'the user'}'s private information, so I'll keep it for them.",
                    "🔒 guest mode: kept private")
                continue
            lower_c = text.lower()
            if re.search(r"\b(?:share|send|print|download)\b", lower_c) and re.search(r"\b(?:summary|report)\b", lower_c):
                title, lines = cycle_summary()
                if not lines:
                    say("I don't have any period dates saved yet, so there's no summary to share.",
                        "🩸 cycle (on device, private)", tone="gentle")
                else:
                    url, svg = share_list(title, lines, checks=False)
                    if not url:
                        say("I need to be on Wi-Fi to send it to your phone. It only travels over your own Wi-Fi.",
                            "📤 share (no network)", tone="gentle")
                    else:
                        dashboard.update(activity={"title": "🌸 Cycle summary",
                                                   "lines": ["Scan with your phone (same Wi-Fi)", "The link works for 10 minutes"],
                                                   "qr": svg, "expires": time.time() + 120})
                        say("Scan the code on my screen. Your summary opens on your phone, straight from me, "
                            "for 10 minutes. You can save it as a PDF for your doctor.",
                            "📤 shared on this Wi-Fi (not online)", tone="gentle")
                follow_up, follow_count, chime_next = True, 0, False
                continue
            cycle_reply = handle_cycle(text)
            if cycle_reply:
                dashboard.update(last_heard="(private)")
                if isinstance(cycle_reply, dict):             # the summary: shown on screen for a minute
                    dashboard.update(activity={"title": "🌸 Cycle summary", "lines": cycle_reply["lines"],
                                               "expires": time.time() + 60})
                    cycle_reply = cycle_reply["say"]
                say(cycle_reply, "🩸 cycle (on device, private)", tone="gentle")
                follow_up, follow_count, chime_next = True, 0, False
                continue

        # ----- Things only the code may answer, so the model never invents them -----
        lower_f = text.lower().strip(" .!?,")
        if re.fullmatch(r"[-*\[(\s]*(?:music|applause|laughter|noise|silence)[-*\])\s]*", lower_f):
            print(f"   (ignored background sound heard as: {text})")
            follow_up = False
            continue
        if re.search(r"\b(?:features?|capabilit\w*|what (?:all |else )?can you do|things you can do"
                     r"|what (?:all )?(?:can|do) you (?:help|offer))\b", lower_f):
            section = next((k for k, pat in FEATURE_SECTIONS if re.search(pat, lower_f)), None)
            say(FEATURE_HELP[section] if section else CAPABILITIES, f"🙂 about {ASSISTANT_NAME}")
            follow_up, follow_count, chime_next = True, 0, False
            continue
        if re.search(r"\b(?:play|put on|listen to|sing)\b.*\b(?:music|songs?|playlist|tunes?)\b", lower_f):
            say("I can't play music yet, it's on my roadmap. I can play a game with you, or tell you a joke.",
                "🎵 not available yet (honest)")
            follow_up, follow_count, chime_next = True, 0, False
            continue
        if re.search(r"\b(?:tell|say|another|one more|know any|got any)\b.*\bjokes?\b|\bmake me laugh\b", lower_f):
            joke = random.choice([j for j in JOKES if j not in _told] or JOKES)
            _told.append(joke)
            del _told[:-6]
            say(joke, "😄 joke (on device)", tone="bright")
            follow_up, follow_count, chime_next = True, 0, False
            continue
        if re.search(r"\b(?:speaker|volume)\b", lower_f):
            say("I can't see the speaker switch or the volume myself. " + (
                "Right now I'm in quiet mode, so I answer on the screen." if _quiet else
                "Right now I'm talking out loud. Say quiet mode on if you'd like answers on the screen only."),
                "🔈 speaker (honest)")
            follow_up, follow_count, chime_next = True, 0, False
            continue
        if re.search(r"\bbutterfly\s+(?:hu\w*|hag\w*)", lower_f):
            text = "let's do a butterfly hug"                 # mishearings like "butterfly hub"

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

        # Plans and progress over a period: "What's planned next week?", "What did I do this week?"
        period_reply = None if _guest else handle_period(text, memory)
        if period_reply:
            say(period_reply, "📅 plans & progress (on device)")
            follow_up, follow_count, chime_next = True, 0, False
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

        # "Are you online?" / "Can you access the internet?": check for real, never let the LLM guess
        if re.search(r"\b(?:are you|am i|is (?:the )?(?:internet|wi-?fi))\s+(?:\w+\s+)?(?:online|offline|connected|working|down)\b"
                     r"|\b(?:can|do) you (?:access|use|reach|get on|go on|have)\s+(?:the\s+)?(?:internet|wi-?fi|web)\b"
                     r"|\bdo you have (?:internet|wi-?fi|a connection)\b|\bis (?:there|the) (?:internet|connection)\b",
                     text.lower()):
            ok = network_up() and check_internet()
            say("Yes, I'm connected right now. I only go online when you ask for the weather, the news or a look-up, "
                "and only a city name or a short topic ever leaves this device." if ok else
                "No, I'm offline right now. Everything else still works here: memory, lists, reminders, games "
                "and emergency help.", "🌐 connection check (real)")
            continue

        # 1. Exact commands (fast, no LLM)
        if handle_command(text, memory, history, reminders):
            continue

        # "tell me the time" / "got the time?" / "current time" -> the same as "what time is it"
        if re.search(r"\b(?:tell me|what'?s|what is|got) the (?:current )?time\b|\bcurrent time\b", text.lower()) \
                and not re.search(r"\btime (?:period|of the|zone)\b", text.lower()):
            text = "what time is it"
            
        # "what is today" / "what's today's date" -> the device clock, never the LLM
        if re.search(r"\bwhat(?:'s| is) today\b|\bwhat day is (?:it )?today\b|\btoday'?s date\b", text.lower()):
            text = "what day is it today"

        # 2. Date and time (Python, offline). Not for "the time period of..." / "time zone" questions
        if not re.search(r"\btime (?:period|of the|zone|line|travel|machine)\b", text.lower()):
            tool_answer = answer_time_question(text)
            if tool_answer:
                say(tool_answer, "🕐 device clock (offline)")
                continue



        # 3. Obvious weather / news (rules). Only for real requests, never for talk about the past
        lower_q = text.lower()
        is_request = re.search(r"\?|^(?:what|what's|how|will|is|are|do|does|should|tell|check|give|any)\b", lower_q)
        about_past = re.search(r"\b(?:last (?:week|month|year|night)|yesterday|we had|there was|it was)\b", lower_q)
        if is_request and not about_past:
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

        # "I'm bored" -> one thing they love (from memory) + one fresh idea (local LLM, checked)
        if not _guest and re.search(r"\b(?:i'?m bored|any ideas|what should i do|something to do|free time"
                                    r"|something (?:new|different))\b", text.lower()):
            liked = []
            for t in memory.all():
                m = re.match(r"^i (?:really |also )?(?:love|like|enjoy) (.+)$",
                             t.split(" (saved on")[0].strip().rstrip("."), re.I)
                if m:
                    liked += [p.strip() for p in re.split(r",|\band\b", m.group(1)) if p.strip()]
            fresh = fresh_idea(liked)
            new_only = re.search(r"\bsomething (?:new|different)\b|\bnot the usual\b", text.lower())
            if liked and not new_only:
                reply = f"How about some {random.choice(liked)}, since you love it?"
                if fresh:
                    reply += f" Or for something different: {fresh}"
            elif fresh:
                reply = f"Here's something new: {fresh}"
            else:
                reply = None
            if reply:
                say(reply, "💡 your likes + a fresh idea (local LLM, checked)", tone="bright")
                continue

        # "How are you?": a warm answer straight away, no model needed (and never "I'm just a program")
        if len(text.split()) <= 7 and re.search(
                r"\bhow (?:are|r) (?:you|u)\b(?!\s+(?:going|planning|able|supposed|so\b))|\bhow'?s it going\b",
                text.lower()):
            name = memory.get_setting("user_name")
            say(random.choice([f"I'm doing well, thanks for asking{', ' + name if name else ''}! How about you?",
                               "I'm good, and happy to see you! How's your day going?",
                               "Feeling bright and ready to help! How are you?"]), "💛 small talk", tone="bright")
            continue

        # Kind words get a warm, honest reply (never "I'm just a computer program")
        if re.search(r"\bi love you\b|\byou(?:'re| are) my best friend\b", text.lower()):
            say("That's really sweet, thank you. I'm glad I can be part of your day. "
                "And I hope you have people around you who make you feel loved too.", "💛 kind words", tone="gentle")
            continue
        if re.search(r"\byou(?:'re| are) (?:so |very |really )?(?:good|great|amazing|awesome|the best|helpful|sweet|smart|a good \w+)\b"
                     r"|\b(?:good job|well done|nice work|you rock)\b|\bcompliments?\b", text.lower()):
            say(random.choice(["Aww, that made my day!", "Thank you, that means a lot!",
                               "That's so kind of you. I'm happy to help!"]), "💛 kind words", tone="bright")
            continue

        # "I am a female, please save it" / "My blood group is O positive, remember that" -> save the first part
        m = re.match(r"^(.*?)[,.!]?\s*(?:please\s+)?(?:save|remember|note)(?: down)? (?:it|this|that)\b", text, re.I)
        if m and len(m.group(1).split()) >= 2 and not is_unsafe(m.group(1)):
            fact = strip_address(m.group(1).strip(" ,.!"))
            memory.add(f"{fact} (saved on {date.today():%A, %d %B %Y})")
            print(f"💾 Saved: {fact}")
            say("Saved. I'll remember that.", "💾 saved to memory", tone="bright")
            continue

        # "I love badminton and music" -> offer to remember it (only with a yes)
        if _offer_remember:
            liked, _offer_remember = _offer_remember, None
            if re.match(r"(?:yes|yeah|yep|sure|ok|okay|please)\b", text.lower()):
                memory.add(f"I love {liked} (saved on {date.today():%A, %d %B %Y})")
                say("Saved. I'll remember that.", "💾 saved to memory", tone="bright")
                continue
            if re.match(r"(?:no|nope|don't)\b", text.lower()):
                say("Okay, I won't save it.", "💾 not saved")
                continue
        m = re.match(r"^(?:i|i really|i also) (?:love|like|enjoy|adore)\s+(?!you\b)(.{3,80})$", text.lower().strip(" .!"))
        if m and not is_unsafe(m.group(1)):
            _offer_remember = m.group(1)
            say(f"That's lovely! Should I remember that you love {_offer_remember}?", "💾 remember?", tone="bright")
            continue

        found = [] if _guest else memory.search(text)      # search memory once per turn, reuse it below

        # Never invent facts about the user: questions about their likes/life with nothing saved get an honest answer
        user = memory.get_setting("user_name") or ""
        names = r"my|i|me" + (f"|{re.escape(user.lower())}" if user else "")
        asks_about_user = (re.search(rf"\b(?:{names})\b", text.lower()) and re.search(
            r"\b(?:likes?|loves?|hobb\w*|favou?rite|enjoys?|prefers?|interests?|birthday|allerg\w*"
            r"|gender|male|female|a man|a woman|how old|my age)\b", text.lower()))
        if asks_about_user and not re.match(r"^(?:i|i really|i also) (?:love|like|enjoy)", text.lower()):
            if _guest:
                say(f"That's {user}'s private information, so I'll keep it for them.", "🔒 guest mode: kept private")
                continue
            # Likes and hobbies: read them straight from memory (meaning-search can miss "what do I like")
            if re.search(r"\b(?:likes?|loves?|hobb\w*|enjoys?|favou?rite|interests?)\b", text.lower()):
                third = user and re.search(rf"\b{re.escape(user.lower())}\b", text.lower())
                prefs = []
                for t in memory.all():
                    core = re.sub(r"\s*\[date:.*?\]", "", t.split(" (saved on")[0]).strip().rstrip(".")
                    m = re.match(r"^i (?:really |also )?(love|like|enjoy|adore) (.+)$", core, re.I)
                    if m:
                        verb = m.group(1).lower()
                        prefs.append(f"{user} {verb}s {m.group(2)}" if third else f"you {verb} {m.group(2)}")
                    elif re.match(r"^my (?:favou?rite|hobb)", core, re.I):
                        prefs.append(re.sub(r"^my\b", f"{user}'s" if third else "your", core, flags=re.I))
                prefs = list(dict.fromkeys(prefs))         # no repeats
                if prefs:
                    
                    joined = prefs[0] if len(prefs) == 1 else "; ".join(prefs[:-1]) + "; and " + prefs[-1]
                    say(joined[0].upper() + joined[1:] + ".", "📚 memory (on device)", tone="bright")
                    continue
            if not found:
                say("I don't have that saved yet. You can tell me, and I'll remember it.",
                    "📚 memory (nothing saved)")
                continue

        own_q = re.match(r"^(?:where|when) (?:is|are|was|were|did|do) (?:my|i)\b|^what(?:'s| is| was) my\b"
                         r"|^(?:who|which) (?:is|are|was) my\b", text.lower())
        if own_q and not _guest and not found:
            say("I don't have that saved. You can tell me, and I'll remember it.", "📚 memory (nothing saved)")
            continue


        # 4. Gate -> LLM router -> validated tool (lists or internet)
        lower_t = text.lower()
        if any(p.search(lower_t) for p in GATE_KEYWORDS.values()):
            score, category = gate(text, example_vecs)
            on_topic = GATE_KEYWORDS[category].search(lower_t)      # the gate's pick must match a real word
            print(f"🚦 Gate: {score:.2f} ({category}){'' if on_topic else ' → off topic, skipped'}")
        else:
            score, category, on_topic = 0.0, "none", None             # small talk: no gate, no router
        wants_lists = category == "lists" and score >= GATE_THRESHOLD and on_topic
        wants_online = category in ("get_weather", "get_news") and score >= GATE_THRESHOLD and not found and on_topic   
        # The gate is sure and the sentence has a weather/news word: call the tool directly, no router (saves 2-4 s)
        if wants_online and (score >= STRONG_GATE or (category == "get_weather" and re.search(
                r"\b(?:weather|forecast|temperature)\b", lower_t))):
            args = {}
            if category == "get_weather":
                m = re.search(r"\b(?:in|at|for) ([A-Z][a-z]+(?: [A-Z][a-z]+)?)\b", text)
                args = {"city": m.group(1) if m else "", "day": "tomorrow" if "tomorrow" in lower_t else "today"}
            tool_reply = run_tool_call(category, args, memory.get_setting("home_city"))
            if tool_reply:
                print(f"🧭 Router skipped: the gate is sure → {category} {args}")
                say(tool_reply, "🌐 internet (gate)")
                continue
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

        if time.time() - last_chat > CHAT_RESET_MINUTES * 60:          # a new conversation after 10 quiet minutes
            del history[1:]
        last_chat = time.time()


        # 5. Local LLM answer (with memories if relevant)
        # Things that rarely change go first (system, name, part of day), so Ollama can reuse what it already read
        messages = [history[0], context_message(memory)] + history[1:]
        if found:
            facts = "\n".join(f"- {t}" for _, t in found)
            print(f"📚 Using memory:\n{facts}")
            messages.append({"role": "system",
                             "content": f"The user's saved memories:\n{facts}"})
        # Suggestions get personal: add the user's saved likes (only for suggestion-type questions)
        if not _guest and re.search(r"\b(?:suggest|recommend|ideas?|what should i|plan (?:my|a|the)|gift|weekend|bored)\b",
                                    text.lower()):
            likes = [t.split(" (saved on")[0] for t in memory.all()
                     if re.match(r"(?:i (?:really |also )?(?:love|like|enjoy)|my favou?rite)", t.lower())]
            if likes:
                print(f"📚 Using likes: {likes[:5]}")
                messages.append({"role": "system", "content": "Things the user has told you they like:\n"
                                 + "\n".join(f"- {l}" for l in likes[:5])
                                 + "\nUse one of them if it fits, to make the suggestion personal."})
        
        messages.append({"role": "user", "content": text})

        think_tick()
        start = time.time()
        reply, confidence = think(messages)
        reply = for_speech(reply)
        # "I'm just a friendly AI, but I'm doing well" -> "I'm doing well" (Jarvis never talks itself down)
        reply = re.sub(r"\bI'?m (?:just |only )?an? (?:\w+ )?(?:AI|bot|assistant|companion|helper|program|computer program|language model)\b"
                       r",?\s*(?:but\s+)?(?:I'?m\s+)?", "I'm ", reply, flags=re.I).replace("I'm I'm", "I'm")
        # Stay in character: drop "I'm just a computer program" / "I don't feel emotions" sentences
        kept = [s for s in re.split(r"(?<=[.!?])\s+", reply)
                if not re.search(r"computer program|don'?t (?:feel|have) (?:any )?emotions|as an ai|language model", s.lower())]
        reply = " ".join(kept) or "I'm Jarvis, right here with you."
        # A reply cut off mid-sentence: keep only the complete sentences
        if reply and reply[-1] not in ".!?":
            cut = max(reply.rfind(". "), reply.rfind("! "), reply.rfind("? "))
            if cut > 20:
                reply = reply[:cut + 1]
        recent = [m["content"] for m in history if m["role"] == "assistant"][-2:]
        looped = reply in recent
        if looped:                                          # stuck in a loop: start the chat fresh
            print(f"   (repeated reply, resetting chat: {reply})")
            del history[1:]
            reply = "Sorry, I think I'm going in circles. Could you say that in a different way?"
            
        print(f"🤔 Confidence: {confidence:.2f}  ({time.time() - start:.1f}s)")
        route_name = "📚 memory (on device)" if found else "🧠 local LLM"

        # "I'm not sure" mode: low confidence on factual questions = likely a guess
        declined = re.search(r"don't (know|have)|not sure|no access", reply.lower())
        # The memory was found but the LLM still said it doesn't know: read the memory out ourselves
        if found and declined:
            core = re.sub(r"\s*\[date:.*?\]", "", found[0][1].split(" (saved on")[0]).strip().rstrip(".")
            mine = re.sub(r"\bI am\b", "you are", core, flags=re.I)
            mine = re.sub(r"\bI'm\b", "you're", mine, flags=re.I)
            mine = re.sub(r"\bmy\b", "your", mine, flags=re.I)
            mine = re.sub(r"\bI\b", "you", mine)
            reply, declined = f"Here's what you told me: {mine}.", None
            route_name = "📚 memory (read directly)"
        is_question = re.match(
            r"(what|who|when|where|which|why|how (?:many|much|old|far|long|big)|is|are|was|were|does|did)\b",
            text.lower())
        about_assistant = re.search(r"\b(you|your|yourself)\b", text.lower())
        if not found and not declined and is_question and not about_assistant and not looped \
                and confidence < CONFIDENCE_THRESHOLD:
            print(f"   (withheld guess: {reply})")
            if re.search(r"\bmy\b", text.lower()):        # about their own life, and nothing saved
                reply = "I don't have that saved. You can tell me, and I'll remember it."
            else:
                reply = "Hmm, I'm not sure about that one. Want me to look it up online?"
                offer_lookup(text)
            route_name = "🤔 not sure (guess withheld)"

        # The LLM half-declined with very low confidence ("I'm not sure... but you're a great player!"): say it plainly
        if declined and not found and is_question and not about_assistant and not looped \
                and confidence < 0.6 and not re.search(r"\bmy\b", text.lower()):
            print(f"   (unsure, mixed reply replaced: {reply})")
            reply = "Hmm, I'm not sure about that one. Want me to look it up online?"
            offer_lookup(text)
            route_name = "🤔 not sure (guess withheld)"
            
        # Confidence isn't truth: for facts with a year, offer a quick online double-check
        if (is_question and not found and not route_name.startswith("🤔")
                and re.search(r"\b(?:1[5-9]\d\d|20\d\d)\b", text)):
            reply += " I can double-check that online if you like."
            offer_lookup(text)

        # Never let the model describe weather it hasn't checked ("clear blue sky and a gentle breeze")
        if route_name.startswith(("🧠", "📚")) and re.search(
                r"\b(?:the weather (?:is|was|looks|will)|it'?s (?:sunny|raining|cloudy|chilly)|(?:clear|blue) sky"
                r"|sky is|breeze|\d+ degrees)\b", reply.lower()):
            print(f"   (blocked made-up weather: {reply})")
            reply = "I haven't checked the weather yet. Just ask me, what's the weather, and I'll look it up."
            route_name = "🛡️ made-up weather blocked"

        # Block false claims: the LLM can't set alarms, change lists, send messages, or make calls
        promise = re.search(
            r"\b(i'll|i will|i've|i have|let me)\s+(?:make sure\b|"
            r"(?:remind|set|call|text|send|book|order|schedule|add|save|remove|note|check"
            r"|clear|delete|cancel|change|update|adjust|fix|create|make|share|generate))"
            r"|\bi can (?:create|make|share|generate|play)\b"
            r"|\b(added|removed|saved|scheduled|noted|closed|cleared|deleted|created|shared|sent)\b",
            reply.lower())
        negated = re.search(r"\b(?:haven'?t|have not|didn'?t|did not|never|can'?t|cannot)\b", reply.lower())
        if promise and not declined and not negated and not route_name.startswith("🤔"):
            print(f"   (blocked false promise: {reply})")
            reply = "I can't do that yet, sorry. Ask me what I can do, and I'll tell you."
            route_name = "🛡️ false promise blocked"

        history.append({"role": "user", "content": text})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 1 + 2 * (HISTORY_TURNS + 2):        # trim in steps, so the model's cache stays useful
            history = history[:1] + history[-2 * HISTORY_TURNS:]
        tone = "gentle" if route_name.startswith(("🤔", "🛡️")) else "calm"
        say(reply, route_name, round(confidence, 2), tone)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye!")