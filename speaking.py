"""Speaking practice (fix 115, desk idea #12). Pure code on this device: no AI judges you, nothing is recorded or kept.

You answer a question out loud (an interview, a viva, or your own presentation). Jarvis gives you only numbers:
  - how long you spoke, and your pace in words per minute (about 120 to 160 is easy to follow)
  - filler words ("um", "basically", "you know", "like," ...), and which ones
The sound is used only to count, then dropped, like every other sentence you say to Jarvis. Nothing is saved.

Say:  "interview practice" / "let's practise for my viva" / "practise my presentation"
      "practise with my own question: why should we hire you"
Then: answer · "another question" · "same question again" · "stop practice"
"""
import random
import re
import time

import numpy as np

QUESTIONS = {
    "interview": ["Tell me about yourself.", "Why do you want this job?", "What is your biggest strength?",
                  "Tell me about a time you solved a difficult problem.", "Where do you see yourself in five years?",
                  "Tell me about a mistake you made, and what you learned from it.",
                  "Why should we hire you?", "How do you handle pressure and deadlines?",
                  "Tell me about a project you are proud of."],
    "viva": ["Explain your project in two minutes.", "What problem does your project solve?",
             "Why did you choose this method?", "What are the limitations of your work?",
             "What would you do differently next time?", "How did you test that your results are right?"],
    "presentation": ["Start your presentation whenever you're ready. Say it the way you will on the day."],
}
FILLERS = [("um", r"\b(?:um+|umm+|erm+)\b"), ("uh", r"\b(?:uh+|er+|ah+)\b"), ("hmm", r"\bhmm+\b"),
           ("you know", r"\byou know\b(?! (?:what|how|that|the|about|when|where|why|who|if))"),
           ("basically", r"\bbasically\b"), ("actually", r"\bactually\b"), ("I mean", r"\bi mean\b(?! (?:it|that))"),
           ("like", r"(?:^|,\s*)like,|\blike,\s|,\s*like\s*,"), ("so", r"(?:^|[.!?]\s+)so,"),
           ("kind of", r"\bkind of\b(?! (?:a|an|the|thing|person|work|job))"), ("literally", r"\bliterally\b")]
FILLERS_HI = [("मतलब", r"मतलब"), ("यानी", r"यानी"), ("अं", r"(?:^|\s)(?:अं+|उम्म?|हम्म+)(?=\s|$|[,।])"),
              ("वो", r"(?:^|\s)वो(?=\s*[,।])"), ("तो", r"(?:^|[।,]\s*)तो,")]
START = re.compile(r"\b(?:interview|viva|presentation|speaking|speech|public speaking) practi[cs]e\b"
                   r"|\bpracti[cs]e (?:for |my |an? |speaking|the )*(?:interview|viva|presentation|speech|speaking)\b"
                   r"|\b(?:help me |let'?s )?practi[cs]e speaking\b|\bmock interview\b", re.I)
OWN = re.compile(r"\bpracti[cs]e (?:with|on) (?:my own|this) question[:,]?\s+(.+)$", re.I)
STOP = re.compile(r"^(?:stop|end|quit|finish|exit|cancel)(?: the| my)? (?:practi[cs]e|session|interview)\b"
                  r"|^(?:stop|that'?s enough|enough|i'?m done(?: for (?:now|today))?|no more)$", re.I)
AGAIN = re.compile(r"^(?:same question(?: again)?|again|try (?:it |that )?again|let me try again|one more time|repeat"
                   r"(?: the question)?)$", re.I)
NEXT = re.compile(r"^(?:another|next|new|a different|one more)(?: one| question)?(?: please)?$", re.I)
SEVERE = re.compile(r"\b(?:chest pain|can'?t breathe|cannot breathe|faint(?:ed|ing)|unconscious|bleeding a lot|fire"
                    r"|help me|call (?:an )?ambulance|emergency)\b", re.I)

OTHER = re.compile(r"^(?:what(?:'s| is| are| time)|who(?:'s| is)|when(?:'s| is)|where(?:'s| is)|how (?:is|are|do|much|many)"
                   r"|remind me|add|set (?:a|an|the)|turn (?:on|off)|play|open|show me|read (?:my|me)|call me|speak"
                   r"|can you|could you|please|let'?s|hey)\b", re.I)
_s = None        # {"kind", "q", "waiting", "at", "asked": [...]}  RAM only


def active():
    global _s
    if _s and time.time() - _s["at"] > 600:                  # left alone for 10 minutes: over
        _s = None
    return _s is not None


def waiting_answer():
    """main.py: listen longer (up to 2 minutes, with longer pauses) because an answer is coming."""
    return active() and _s["waiting"]


def voiced_seconds(audio, rate=16000, level=None):
    """How long you really spoke: from the first loud moment to the last (the silence around it doesn't count)."""
    if audio is None or len(audio) == 0:
        return 0.0
    frame = int(0.08 * rate)
    n = len(audio) // frame
    if not n:
        return 0.0
    loud = np.sqrt(np.mean(np.square(audio[:n * frame].reshape(n, frame).astype(np.float64)), axis=1))
    floor, peak = float(np.percentile(loud, 10)), float(np.percentile(loud, 95))
    lvl = level if level is not None else max(0.005, min(3 * floor, 0.3 * peak))   # above the room, below your voice
    idx = np.nonzero(loud > lvl)[0]
    if not len(idx):
        return 0.0
    return round((idx[-1] - idx[0] + 1) * frame / rate, 1)


def count_fillers(text, hindi=False):
    low = " " + (text or "").lower() + " "
    found = {}
    for name, pat in (FILLERS_HI if hindi else FILLERS):
        n = len(re.findall(pat, low))
        if n:
            found[name] = n
    return found


def words_in(text):
    return len(re.findall(r"[\w'’]+", text or ""))


def feedback(text, seconds, hindi=False):
    """Only numbers, said kindly. No judgement of what you said."""
    words = words_in(text)
    if words >= 5 and seconds == 0:                          # typed (on the phone or in the test): no sound to time
        fill = count_fillers(text, hindi)
        return ("You typed that, so I can't time it. "
                + (f"Filler words: {sum(fill.values())}." if fill else "No filler words.")
                + " Say: same question again, another question, or stop practice.")
    if seconds < 3 or words < 5:
        return "That was very short, so I can't count much. Try again, and take your time."
    wpm = round(words / seconds * 60)
    pace = ("a bit fast: try slowing down a little" if wpm > 170 else
            "a calm, easy pace" if wpm >= 110 else "a bit slow: try to keep the flow going")
    secs = int(round(seconds))
    took = f"{secs // 60} minute{'s' if secs // 60 != 1 else ''} {secs % 60} seconds" if secs >= 60 else f"{secs} seconds"
    fill = count_fillers(text, hindi)
    total = sum(fill.values())
    if total == 0:
        f = "No filler words that I could hear."
    else:
        top = ", ".join(f"{k} {v} time{'s' if v != 1 else ''}" for k, v in sorted(fill.items(), key=lambda x: -x[1])[:3])
        f = f"Filler words: {total} ({top})."
    return (f"You spoke for {took}, about {wpm} words a minute: {pace}. {f} "
            "Say: same question again, another question, or stop practice.")


def _ask(kind, own=None, again=False):
    global _s
    if own:
        q = own.strip(" .")
        q = q[:1].upper() + q[1:] + ("" if q.endswith("?") else "?")
    elif again and _s:
        q = _s["q"]
    else:
        asked = _s["asked"] if _s else []
        pool = [x for x in QUESTIONS[kind] if x not in asked] or QUESTIONS[kind]
        q = random.choice(pool)
    asked = (_s["asked"] if _s else []) + [q]
    _s = {"kind": kind, "q": q, "waiting": True, "at": time.time(), "asked": asked}
    lead = "" if again or own else ("Okay, a practice " + kind + ". Answer out loud, like you would on the day. "
                                    "I'll only count your time, pace and filler words. Nothing is recorded. ")
    if kind == "presentation" and not own:
        return lead + q + " Pause for 4 seconds when you're done."
    return lead + "Here's the question: " + q + " Take your time; pause for 4 seconds when you're done."


def handle(text, memory=None, seconds=None, heard_hindi=""):
    """A reply if this was about speaking practice (or an answer to it), else None.
    seconds: how long you spoke (from main.py, measured on the sound). heard_hindi: the Hindi words, if you spoke Hindi."""
    global _s
    clean = (text or "").strip()
    low = clean.lower().strip(" .!?")
    if active():
        if SEVERE.search(low):                               # an emergency always wins
            _s = None
            return None
        if STOP.match(low):
            _s = None
            return "Okay, practice is over. Well done for practising out loud!"
        if AGAIN.match(low):
            return _ask(_s["kind"], again=True)
        if NEXT.match(low):
            return _ask(_s["kind"])
        m = OWN.search(clean)
        if m:
            return _ask(_s["kind"], own=m.group(1))
        if _s["waiting"] and words_in(clean) < 10 and OTHER.match(low):
            _s = None                                        # "what's the weather?" is not an answer: carry on normally
            return None
        if _s["waiting"] and seconds is not None:
            _s["waiting"], _s["at"] = False, time.time()
            said = heard_hindi or clean
            return feedback(said, seconds, hindi=bool(heard_hindi))
        if not _s["waiting"]:
            _s = None                                        # after the numbers, anything else: carry on normally
            return None
    m = OWN.search(clean)
    if m:
        return _ask("interview", own=m.group(1))
    m = START.search(low)
    if m and not re.search(r"\b(?:remind|tomorrow at|at \d|don'?t|not|didn'?t|how was|went)\b", low):
        kind = "viva" if "viva" in low else "presentation" if re.search(r"presentation|speech", low) else "interview"
        _s = None
        return _ask(kind)
    return None
