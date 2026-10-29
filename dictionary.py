"""Offline dictionary (fix 75): meanings, similar words, opposites, example sentences and spelling.
Uses WordNet (Princeton's English dictionary, about 150,000 words), saved on this device. Nothing goes online.

  "What does serendipity mean?"   "Define happy"   "Meaning of the word frugal"
  "Another word for happy"        "Opposite of brave"    "Use frugal in a sentence"
  "How do you spell necessary?"   -> N, E, C, E, double S, A, R, Y.
  Follow-ups: "another meaning", "spell it", "use it in a sentence", "opposite of it"

Setup (once, needs internet):  pip install nltk
                               python -c "import nltk; nltk.download('wordnet'); nltk.download('omw-1.4')"
"""
import re
import threading
import time

_WN = None
_READY = threading.Event()
LAST = {"word": "", "senses": [], "i": 0, "t": 0.0}

POS = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}
NOT_WORDS = {"life", "love", "everything", "existence", "this", "that", "it", "my name", "your name", "jarvis", "the"}

WORD = r"[\"']?([a-z][a-z'-]{1,30}(?: [a-z][a-z'-]{1,20})?)[\"']?"
MEAN = re.compile(rf"^(?:what(?:'s| is| does)\s+(?:the\s+)?(?:meaning|definition)\s+of\s+(?:the\s+word\s+)?{WORD}"
                  rf"|what\s+does\s+(?:the\s+word\s+)?{WORD}\s+mean"
                  rf"|(?:define|meaning of|definition of)\s+(?:the\s+word\s+)?{WORD}"
                  rf"|{WORD}\s+meaning)$")
SYN = re.compile(rf"^(?:what(?:'s| is| are)\s+)?(?:a\s+|an\s+|the\s+)?(?:another|other|different)\s+words?\s+(?:for|than)\s+{WORD}"
                 rf"|^(?:what(?:'s| is| are)\s+)?(?:a\s+|the\s+)?synonyms?\s+(?:for|of)\s+{WORD}"
                 rf"|^(?:give me\s+)?(?:a\s+)?synonyms?\s+(?:for|of)\s+{WORD}")
ANT = re.compile(rf"^(?:what(?:'s| is)\s+)?(?:the\s+|an\s+)?(?:opposite|antonym)s?\s+(?:of|for)\s+{WORD}")
EXAMPLE = re.compile(rf"^(?:use|put)\s+{WORD}\s+in\s+a\s+sentence|^(?:give me\s+)?an?\s+example\s+(?:of|for|with)\s+{WORD}")
SPELL = re.compile(rf"^(?:how\s+(?:do\s+(?:you|i)|to|should i)\s+spell\s+{WORD}|spell\s+(?:the\s+word\s+)?{WORD}"
                   rf"|what(?:'s| is)\s+the\s+spelling\s+of\s+{WORD}|spelling\s+of\s+{WORD})$")
MORE = re.compile(r"^(?:another|other|more|next)(?:\s+one)?(?:\s+meanings?)?$|^(?:any\s+)?other\s+meanings?$"
                  r"|^what else(?: does it mean)?$")
SPELL_IT = re.compile(r"^(?:how do you )?spell (?:it|that)$")
EXAMPLE_IT = re.compile(r"^(?:use (?:it|that) in a sentence|(?:give me )?an example)$")
ANT_IT = re.compile(r"^(?:what(?:'s| is) )?(?:the )?(?:opposite|antonym)(?: of (?:it|that))?$")
SYN_IT = re.compile(r"^(?:another|other) words? for (?:it|that)$|^(?:any )?synonyms?$")


def _load():
    global _WN
    try:
        from nltk.corpus import wordnet as wn
        wn.synsets("test")                        # the first call reads the data (a few seconds on the Pi)
        _WN = wn
    except Exception as e:
        print(f"   (dictionary not available: {type(e).__name__}. See the top of dictionary.py to install it.)")
    _READY.set()


_STARTED = []


def warm():
    """Load the dictionary in the background at startup, so the first question is quick."""
    if not _STARTED:
        _STARTED.append(True)
        threading.Thread(target=_load, daemon=True).start()


def _wn():
    if not _READY.is_set():
        warm()
        _READY.wait(timeout=20)
    return _WN


def _name(lemma):
    return lemma.name().replace("_", " ")


def _senses(word):
    wn = _wn()
    if wn is None:
        return None
    return wn.synsets(word.replace(" ", "_"))


def _meaning_reply(word, i):
    senses = LAST["senses"]
    s = senses[i]
    pos = POS.get(s.pos(), "")
    similar = [n for n in dict.fromkeys(_name(l) for l in s.lemmas()) if n.lower() != word.lower()][:3]
    reply = f"{word[:1].upper() + word[1:]}" + (f", {pos}" if pos else "") + f": {s.definition()}."
    if similar:
        reply += f" Similar: {', '.join(similar)}."
    left = len(senses) - i - 1
    if i == 0 and left > 0:
        reply += f" It has {left} more meaning{'s' if left > 1 else ''}. Say: another meaning."
    elif left == 0 and i > 0:
        reply += " That's the last meaning I have."
    return reply


def spell(word):
    """'necessary' -> 'N, E, C, E, double S, A, R, Y.'"""
    letters, out, i = [c for c in word.upper() if c.isalpha()], [], 0
    while i < len(letters):
        if i + 1 < len(letters) and letters[i + 1] == letters[i]:
            out.append(f"double {letters[i]}")
            i += 2
        else:
            out.append(letters[i])
            i += 1
    return ", ".join(out) + "."


def _word_of(m):
    w = next((g for g in m.groups() if g), "") if m else ""
    w = re.sub(r"^(?:the|a|an)\s+", "", w.strip(" '\""))
    return w


def _missing():
    return ("My dictionary isn't installed yet. On this device, run: pip install nltk, "
            "and then download WordNet once. It's at the top of dictionary.py.")


def handle(text):
    """A spoken reply for a dictionary question, or None if it isn't one (other features get it)."""
    low = re.sub(r"^(?:hey |ok |okay )?jarvis,?\s*", "", text.lower().strip(" .!?")).replace("’", "'")
    low = re.sub(r"\s+(?:please|jarvis)$", "", low)
    recent = LAST["word"] and time.time() - LAST["t"] < 180
    try:                                       # fix 107: "next one" after "what should I do first?" is a task
        import todo
        if todo._first["at"] > LAST["t"]:
            recent = False
    except Exception:
        pass

    if recent and MORE.match(low):
        if LAST["i"] + 1 >= len(LAST["senses"]):
            return f"That's all the meanings I have for {LAST['word']}."
        LAST["i"] += 1
        LAST["t"] = time.time()
        return _meaning_reply(LAST["word"], LAST["i"])
    if recent and SPELL_IT.match(low):
        return f"{LAST['word'][:1].upper() + LAST['word'][1:]} is spelled: {spell(LAST['word'])}"
    if recent and EXAMPLE_IT.match(low):
        low = f"use {LAST['word']} in a sentence"
    elif recent and ANT_IT.match(low):
        low = f"opposite of {LAST['word']}"
    elif recent and SYN_IT.match(low):
        low = f"another word for {LAST['word']}"

    m = SPELL.match(low)
    if m:
        word = _word_of(m)
        if word and len(word) <= 30 and word not in NOT_WORDS and not word.startswith(("my ", "your ")):
            LAST.update(word=word, senses=LAST["senses"] if word == LAST["word"] else [], i=0, t=time.time())
            return f"{word[:1].upper() + word[1:]} is spelled: {spell(word)}"

    for rx, kind in ((MEAN, "mean"), (SYN, "syn"), (ANT, "ant"), (EXAMPLE, "example")):
        m = rx.match(low)
        if not m:
            continue
        word = _word_of(m)
        if not word or word in NOT_WORDS:
            return None
        senses = _senses(word)
        if senses is None:
            return _missing() if kind != "mean" or low.startswith(("define", "definition")) else None
        if not senses:
            if kind == "mean" and not low.startswith(("define", "definition")):
                return None                                   # maybe a name: other features try
            return f"I couldn't find {word} in my dictionary."
        LAST.update(word=word, senses=senses, i=0, t=time.time())
        if kind == "mean":
            return _meaning_reply(word, 0)
        if kind == "syn":
            words = [n for s in senses for n in (_name(l) for l in s.lemmas()) if n.lower() != word.lower()]
            words = list(dict.fromkeys(words))[:5]
            return (f"Other words for {word}: {', '.join(words)}." if words
                    else f"I don't have other words for {word}.")
        if kind == "ant":
            opp = [_name(a) for s in senses for l in s.lemmas() for a in l.antonyms()]
            opp = list(dict.fromkeys(opp))[:3]
            if not opp:                                    # "brave": the opposite is on a similar word
                for s in senses:
                    for sim in getattr(s, "similar_tos", lambda: [])():
                        opp += [_name(a) for l in sim.lemmas() for a in l.antonyms()]
                opp = list(dict.fromkeys(opp))[:3]
            return (f"The opposite of {word} is {' or '.join(opp)}." if opp
                    else f"I don't have an opposite for {word}.")
        ex = [e for s in senses for e in s.examples() if word.split()[0].lower()[:4] in e.lower()]
        return (f"Here's an example: {ex[0][:1].upper() + ex[0][1:]}." if ex
                else f"I don't have an example for {word}, but it means: {senses[0].definition()}.")
    return None
