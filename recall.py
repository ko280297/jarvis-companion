"""Recall coach (fix 114, desk idea #10): flashcards from YOUR notes. Pure code on this device: no AI, no internet.

Where the questions come from (never from the AI's own memory, so nothing is made up):
  - your notes: every sentence like "Photosynthesis is how plants make food" or "GST: goods and services tax"
    becomes a card ("What is photosynthesis?" -> "how plants make food")
  - cards you make: "make a flashcard: what is the capital of Japan? Tokyo"
How it checks your answer: by code. The key words (and every number) of the answer must be in what you said.
Spaced repetition: right -> the card comes back later (1, 3, 7, 14, 30 days); wrong -> it comes back tomorrow.

Say:   "quiz me" (what's due today)   "quiz me on my biology note"   "how many flashcards do I have?"
In a quiz:  your answer · "I don't know" / "skip" · "stop the quiz"
Edit options:  "that was right" (if I marked you wrong) · "change the answer to ..." · "delete this card"
               "make a flashcard: question? answer"
Never for a guest or in kid mode (main.py). Nothing you say in a quiz is saved, only right/wrong for each card.
"""
import random
import re
import time
from datetime import date, timedelta

_ready = set()
GAPS = [1, 3, 7, 14, 30]           # days until the card comes back, by box
PER_QUIZ = 5
_quiz = None                       # {"cards": [...], "i": 0, "right": 0, "asked": time}
_last = {"id": None, "at": 0.0, "was_right": None, "box": 0, "due": None}   # for "that was right" / "delete this card"

START = re.compile(r"^(?:please\s+|can you\s+|let'?s\s+)?(?:quiz|test) me(?:\s+(?:on|about|from)\s+(?:my\s+|the\s+)?(.+?))?"
                   r"(?:\s+(?:again|please|now))?$"
                   r"|^(?:let'?s\s+|i want to\s+)?(?:revise|revision of|study|practise|practice) (?:my\s+)?(?:notes|flashcards?)"
                   r"(?:\s+(?:on|about)\s+(?:my\s+|the\s+)?(.+?))?$"
                   r"|^(?:start|begin) (?:a |my )?(?:revision|flashcards?|recall)(?: quiz| session)?$", re.I)
STOP = re.compile(r"^(?:stop|end|quit|finish|exit|cancel)(?: the| this| my)? (?:quiz|revision|flashcards?|test|session)\b"
                  r"|^(?:stop|that'?s enough|enough|i'?m done|no more)(?: for (?:now|today))?$", re.I)
DONT_KNOW = re.compile(r"^(?:i )?(?:don'?t|do not) know\b|^(?:skip|pass|no idea|not sure|tell me|show me"
                       r"|i forgot|i give up|next)(?: it| this one| the answer)?$", re.I)
COUNT = re.compile(r"\bhow many (?:flash ?cards|cards)\b|\bwhat(?:'s| is) (?:due|left) (?:for|to) (?:revision|revise)\b"
                   r"|\bany (?:flash ?cards|cards|revision) (?:due|today)\b", re.I)
MAKE = re.compile(r"^(?:make|add|create|new)\s+(?:a\s+)?flash ?card[:,]?\s+(.+?\?)\s*(?:answer[:,]?\s*)?(.+)$"
                  r"|^(?:make|add|create|new)\s+(?:a\s+)?flash ?card[:,]?\s+(?:question[:,]?\s+)?(.+?)[,.]?\s+answer[:,]?\s+(.+)$",
                  re.I)
STOPW = {"the", "a", "an", "of", "to", "in", "on", "is", "are", "and", "or", "for", "it", "its", "it's", "that",
         "this", "by", "with", "as", "at", "be", "was", "were", "which", "who", "what", "from", "into", "their", "they",
         "i", "think", "maybe", "um", "uh", "like", "answer", "my", "so", "um,", "well"}
OTHER = re.compile(r"^(?:what(?:'s| is| are| time)|who(?:'s| is)|when(?:'s| is)|where(?:'s| is)|how (?:is|are|do|much|many|long)"
                   r"|why|remind me|add|set (?:a|an|the)|tell me|turn (?:on|off)|play|open|show me|read (?:my|me)|call me"
                   r"|speak|can you|could you|please|let'?s|i want|i need|i feel|i'?m feeling|help)\b", re.I)
NOT_A_TOPIC = {"it", "this", "that", "he", "she", "they", "there", "i", "we", "you", "here", "what", "which", "who",
               "today", "tomorrow", "everything", "nothing", "something", "my plan", "the plan", "the problem",
               "the idea", "the meeting", "the deadline"}
NUM_WORDS = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
             "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "twenty": "20", "hundred": "100"}


# ---------- storage ----------
def _db(memory):
    db = memory.db
    if id(db) not in _ready:
        db.execute("CREATE TABLE IF NOT EXISTS flashcards (id INTEGER PRIMARY KEY, q TEXT, a TEXT, source TEXT, "
                   "box INTEGER DEFAULT 0, due TEXT, created REAL)")
        db.commit()
        _ready.add(id(db))
    return db


def _cards(memory, where="1=1", args=()):
    return [dict(zip(("id", "q", "a", "source", "box", "due"), r)) for r in _db(memory).execute(
        f"SELECT id, q, a, source, box, due FROM flashcards WHERE {where} ORDER BY id", args)]


# ---------- making cards from notes (code, never the AI) ----------
_IS = re.compile(r"^(?P<x>[A-Za-z0-9][\w\s'()./+-]{1,60}?)\s+(?P<v>is|are|was|were|means|stands for|refers to|is called)"
                 r"\s+(?P<y>.{2,140}?)[.!]*$", re.I)
_COLON = re.compile(r"^(?P<x>[A-Za-z0-9][\w\s'()./+-]{1,50}?)\s*(?::|=|–| - )\s*(?P<y>.{2,140}?)[.!]*$")


def cards_from(text):
    """'GST: goods and services tax. Photosynthesis is how plants make food.' -> [(question, answer), ...]"""
    out = []
    for s in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = s.strip(" -•*\t")
        if not s or s.endswith("?") or len(s.split()) > 30:
            continue
        m = _IS.match(s) or _COLON.match(s)
        if not m:
            continue
        x, y = m.group("x").strip(), m.group("y").strip(" .")
        v = (m.groupdict().get("v") or "").lower()
        if x.lower() in NOT_A_TOPIC or x.split()[0].lower() in {"it", "this", "that", "he", "she", "they", "there", "i",
                                                              "we", "you", "my", "our", "your"} or len(x.split()) > 7:
            continue
        if re.match(r"^(?:not|also|very|so|too|going|just|really|still)\b", y, re.I) or len(y.split()) < 1:
            continue
        xq = x if x[:1].isupper() and not x.isupper() and " " not in x else (x if x.isupper() else x[:1].lower() + x[1:])
        if v in ("means",):
            q = f"What does {xq} mean?"
        elif v == "stands for":
            q = f"What does {xq} stand for?"
        elif v == "refers to":
            q = f"What does {xq} refer to?"
        elif v == "is called":
            q, y = f"What is {y} called?", x
        elif v in ("was", "were", "are"):
            q = f"What {v} {xq}?"
        else:
            q = f"What is {xq}?"
        out.append((q, y))
    return out


def sync(memory):
    """New sentences in your notes become cards; cards from a deleted note go away. Your own cards stay."""
    import notes
    have = {(c["q"].lower(), c["a"].lower()): c for c in _cards(memory)}
    keep = set()
    for n in notes._all(memory):
        for q, a in cards_from(n["body"]):
            keep.add((q.lower(), a.lower()))
            if (q.lower(), a.lower()) not in have:
                _db(memory).execute("INSERT INTO flashcards (q, a, source, box, due, created) VALUES (?, ?, ?, 0, ?, ?)",
                                    (q, a, n["title"], date.today().isoformat(), time.time()))
                have[(q.lower(), a.lower())] = True
    for (q, a), c in list(have.items()):
        if c is not True and c["source"] != "you" and (q, a) not in keep:
            memory.db.execute("DELETE FROM flashcards WHERE id = ?", (c["id"],))
    memory.db.commit()


# ---------- checking an answer (code) ----------
def _norm(s):
    s = s.lower().replace("-", " ")
    words = re.findall(r"[a-z0-9]+", s)
    out = []
    for w in words:
        w = NUM_WORDS.get(w, w)
        if w in STOPW:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def is_right(said, answer):
    """The key words of the answer must be in what you said; every number exactly."""
    want, got = _norm(answer), set(_norm(said))
    if not want:
        return said.strip().lower() == answer.strip().lower()
    nums = [w for w in want if w.isdigit()]
    if any(n not in got for n in nums):
        return False
    words = [w for w in want if not w.isdigit()]
    if not words or (nums and not [g for g in got if not g.isdigit()]):
        return True                                          # "seven" for "7 days": the number is the answer
    hit = sum(1 for w in words if w in got or any(g.startswith(w[:5]) and len(w) >= 6 for g in got))
    return hit / len(words) >= (1.0 if len(words) <= 2 else 0.6)


# ---------- the quiz ----------
def active():
    return _quiz is not None


def _ask():
    c = _quiz["cards"][_quiz["i"]]
    _quiz["asked"] = time.time()
    n = len(_quiz["cards"])
    return f"Question {_quiz['i'] + 1} of {n}: {c['q']}"


def _grade(memory, c, right):
    box = min(c["box"] + 1, len(GAPS) - 1) if right else 0
    due = date.today() + timedelta(days=GAPS[box] if right else 1)
    _last.update(id=c["id"], at=time.time(), was_right=right, box=c["box"], due=c["due"])
    memory.db.execute("UPDATE flashcards SET box = ?, due = ? WHERE id = ?", (box, due.isoformat(), c["id"]))
    memory.db.commit()


def _next(lead):
    _quiz["i"] += 1
    if _quiz["i"] >= len(_quiz["cards"]):
        return lead + " " + _end()
    return lead + " " + _ask()


def _end():
    global _quiz
    q, _quiz = _quiz, None
    done = q["i"] if q["i"] <= len(q["cards"]) else len(q["cards"])
    if not done:
        return "Okay, we'll stop here."
    tail = (" Well done!" if q["right"] == done else
            " The ones you missed will come back tomorrow." if q["right"] < done else "")
    return f"That's the end: {q['right']} of {done} right.{tail}"


def _start(memory, about=None):
    global _quiz
    sync(memory)
    cards = _cards(memory)
    if not cards:
        return ("I don't have any flashcards yet. Take a note with facts, like: photosynthesis is how plants make food. "
                "Or say: make a flashcard: what is the capital of Japan? Tokyo.")
    if about:
        words = {w for w in re.findall(r"[a-z0-9]+", about.lower()) if w not in {"note", "notes", "my", "the"}}
        pick = [c for c in cards if words & set(re.findall(r"[a-z0-9]+", (c["source"] + " " + c["q"]).lower()))]
        if not pick:
            return f"I couldn't find flashcards about {about}. Say: quiz me, for everything that's due."
    else:
        today = date.today().isoformat()
        pick = [c for c in cards if (c["due"] or today) <= today]
        if not pick:
            nxt = min(c["due"] for c in cards if c["due"])
            d = date.fromisoformat(nxt)
            return (f"Nothing is due for revision today. The next cards are due "
                    f"{'tomorrow' if (d - date.today()).days == 1 else 'on ' + format(d, '%A')}. "
                    "Say: quiz me on, and a note's name, to practise anyway.")
    pick.sort(key=lambda c: (c["box"], c["due"] or "", c["id"]))
    pick = pick[:PER_QUIZ]
    random.shuffle(pick)
    _quiz = {"cards": pick, "i": 0, "right": 0, "asked": time.time()}
    return (f"Okay, {len(pick)} card{'s' if len(pick) != 1 else ''}. Say: I don't know, to see the answer, "
            "or: stop the quiz. " + _ask())


def handle(text, memory):
    """A reply if this was about flashcards (or an answer in a quiz), else None."""
    global _quiz
    clean = re.sub(r"^(?:ok(?:ay)?|so|and|um+|uh+|hmm+|jarvis)[,]?\s+", "", (text or "").strip(), flags=re.I).strip(" .!")
    low = clean.lower()

    if _quiz is not None and time.time() - _quiz["asked"] > 300:
        _quiz = None                                         # a quiz left alone for 5 minutes is over

    # edit options right after a card (in a quiz or just after it)
    recent = _last["id"] and time.time() - _last["at"] < 300
    if recent and re.match(r"^(?:no,?\s+)?(?:that was|i was|it was|that'?s|i'?m) (?:right|correct)$", low):
        rows = _cards(memory, "id = ?", (_last["id"],))
        if rows and _last["was_right"] is False:
            c = rows[0]
            box = min(_last["box"] + 1, len(GAPS) - 1)
            memory.db.execute("UPDATE flashcards SET box = ?, due = ? WHERE id = ?",
                              (box, (date.today() + timedelta(days=GAPS[box])).isoformat(), c["id"]))
            memory.db.commit()
            if _quiz is not None and _quiz["right"] < _quiz["i"]:
                _quiz["right"] += 1
            _last["was_right"] = True
            return "Okay, I've counted it as right." + (" " + _ask() if _quiz is not None else "")
        if rows:
            return "Yes, I counted that one as right already." + (" " + _ask() if _quiz is not None else "")
    if recent and _quiz is None and re.match(r"^(?:please\s+)?(?:delete|remove) (?:this|that|the last) (?:flash ?)?card$", low):
        memory.db.execute("DELETE FROM flashcards WHERE id = ?", (_last["id"],))
        memory.db.commit()
        _last["id"] = None
        return "Okay, I've deleted that card." + (" " + _ask() if _quiz is not None else "")
    m = re.match(r"^(?:change|make) the answer (?:to|into) (.+)$", clean, re.I)
    if m and recent:
        memory.db.execute("UPDATE flashcards SET a = ?, source = 'you' WHERE id = ?", (m.group(1).strip(" ."), _last["id"]))
        memory.db.commit()
        return f"Okay, the answer is now: {m.group(1).strip(' .')}." + (" " + _ask() if _quiz is not None else "")

    if _quiz is not None:                                   # in a quiz: this is an answer (or stop / skip)
        if STOP.match(low):
            return _end()
        if re.match(r"^(?:please\s+)?(?:delete|remove) (?:this|that) (?:flash ?)?card$", low):
            c = _quiz["cards"][_quiz["i"]]
            memory.db.execute("DELETE FROM flashcards WHERE id = ?", (c["id"],))
            memory.db.commit()
            return _next("Okay, I've deleted that card.")
        c = _quiz["cards"][_quiz["i"]]
        if DONT_KNOW.match(low):
            _grade(memory, c, False)
            return _next(f"The answer is: {c['a']}.")
        if is_right(clean, c["a"]):
            _grade(memory, c, True)
            _quiz["right"] += 1
            return _next("Right!" + (f" {c['a'][:1].upper() + c['a'][1:]}." if len(c["a"].split()) > 3 else ""))
        if OTHER.match(low):                                # "what's the weather?" in the middle: not an answer
            _quiz = None
            return None                                     # the quiz ends quietly; Jarvis answers normally
        _grade(memory, c, False)
        return _next(f"Not quite. The answer is: {c['a']}. If you think you were right, say: that was right.")

    m = MAKE.match(clean)
    if m:
        q, a = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        q, a = q.strip(" ,"), a.strip(" .")
        if not q.endswith("?"):
            q += "?"
        if not a or len(a.split()) > 25:
            return "Say it like this: make a flashcard: what is the capital of Japan? Tokyo."
        _db(memory).execute("INSERT INTO flashcards (q, a, source, box, due, created) VALUES (?, ?, 'you', 0, ?, ?)",
                            (q[:1].upper() + q[1:], a, date.today().isoformat(), time.time()))
        memory.db.commit()
        return f"Made a flashcard: {q[:1].upper() + q[1:]} Answer: {a}."

    if COUNT.search(low):
        sync(memory)
        cards = _cards(memory)
        today = date.today().isoformat()
        due = [c for c in cards if (c["due"] or today) <= today]
        if not cards:
            return "You don't have any flashcards yet. Take a note with facts, and I'll make them for you."
        return (f"You have {len(cards)} flashcard{'s' if len(cards) != 1 else ''}, and {len(due)} "
                f"{'is' if len(due) == 1 else 'are'} due today. Say: quiz me.")

    m = START.match(low)
    if m:
        return _start(memory, m.group(1) or m.group(2))
    return None
