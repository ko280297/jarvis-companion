"""Hearing fixes (fix 88): speech-to-text mistakes are fixed BEFORE Jarvis acts, using your own data.

  "what's in my tubular list"            -> your to-do list ("tubular", "2-do", "two do"... are how
                                            speech-to-text hears "to-do")
  "tick water plans"                     -> ticks "water the plants" (the closest open task)
  "take water plans"                     -> "Did you mean: tick off water the plants?" ("take" may be a real word,
                                            so it asks first and never guesses an action)
  "it is to-do list, not tubular list"   -> redoes your last request with the right list
  "what are the plans?"                  -> your day (schedule and to-do), never a made-up answer
Fix 90 (from the 28 Oct evening log): "tick, pay rent" (a comma), "pick off ...", "I said no", "to-do list" alone,
  "when will it reign next", "remove the mother's name from your memory", "it is wrong, forget that",
  and short replies like "Got it." or "I'll help you with that." when nothing was done.
Fix 91 (the 28 Oct night log): "pay rent" heard as "parent(s)", "can you pick ... from my to-do list",
  "- Jarvis, yes. - Yes.", "what is done and what is planned for later?", and the AI never invents an item.
And a guard for the AI: it can never claim it did something. Only the code changes your data, so a reply like
"Got it, I'll take care of that" is replaced with what really happened: nothing changed, and what you can say.
"""
import re
import time
from difflib import SequenceMatcher

LAST = {"text": "", "t": 0.0}       # the last request, for "it is X list, not Y list"
PENDING = None                       # {"text": the command to run on "yes", "t": when asked}

_TODO_HEARD = (r"(?:tubular|tubul[ae]r|tubeler|tubelar|tubler|two[- ]?do|2[- ]?do|to[- ]do|too[- ]do|todo|tudu"
               r"|tu[- ]du|todu|do[- ]?do|dodo|due do)")
TODO_HEARD = re.compile(rf"\b{_TODO_HEARD}(?=\s+lists?\b)", re.I)
TICK = re.compile(r"^(?:(?:and|then|so|okay|ok|please|now|also|can you|could you|will you|would you|jarvis)[,\s]+)*"
                  r"(?:(?:in|on|from)\s+(?:my\s+|mine\s+|the\s+)?to-do list,?\s+)?"
                  r"(?P<verb>take|tik|tic|tech|thick|tickle|pick|peck|tick|check|cross(?:\s+(?:off|out))?"
                  r"|strike(?:\s+(?:off|out))?|mark)[,\s]+(?:off[,\s]+)?(?P<what>.+?)"
                  r"(?:\s+(?:as\s+)?(?:done|complete(?:d)?|finished))?"
                  r"(?:,?\s+(?:off|from|in|on)\s+(?:my\s+|mine\s+|the\s+|a\s+)?to-do list)?[.!?]*$", re.I)
SURE_VERBS = re.compile(r"^(?:tick|check|cross|strike|mark)\b", re.I)
PLANS = re.compile(r"^(?:[\w']+,?\s+){0,2}?what(?:'s| is| are)\s+(?:the|my|our|today'?s)\s+plans?"
                   r"(?:\s+for\s+today|\s+today)?[?.!]*$", re.I)
CORR = re.compile(r"\b(?:it(?:'s| is)|i said|i mean|i meant|no,?)\s+(?:my\s+|the\s+|a\s+)?([\w-]+(?: [\w-]+)?) list,?"
                  r"\s+(?:and\s+)?not\s+(?:my\s+|the\s+|a\s+)?([\w-]+(?: [\w-]+)?) list\b", re.I)
YES = re.compile(r"^(?:yes|yeah|yep|yup|haan|ha|han|sure|ok(?:ay)?|correct|right|do it|please do|go ahead)\b", re.I)
NO = re.compile(r"^(?:no|nope|nahi|nah|not that|don'?t|leave it|never ?mind|cancel)\b", re.I)

# The AI claiming it did something (only the code does things)
FAKE_ACTION = re.compile(
    r"\b(?:i'?ll|i will|i'?ve|i have|let me)\s+(?:go ahead and\s+|now\s+|just\s+)?(?:take care of|add|added|put|remove"
    r"|removed|delete|deleted|tick|ticked|mark|marked|set|schedule|scheduled|remind|save|saved|note|noted|update"
    r"|updated|water|book|booked|send|sent|cancel|cancelled|canceled|forget|forgotten|delete|erase|pay|make sure"
    r"|look (?:it |that |this )?up|search|find|check on|keep an eye|keep track|monitor|track|watch)\b"
    r"|\bi'?ll help you with (?:that|this|it)\b"
    r"|\bgot it,?\s+i'?ll\b|\b(?:it'?s|that'?s|all|everything'?s) (?:been\s+)?(?:done|added|set|saved|noted|ticked"
    r"|marked|updated|removed|taken care of)\b|^(?:done|all set)\b|\bconsider it done\b"
    r"|\b(?:added|ticked|marked|removed|saved|noted) (?:it|that|this)\b", re.I)
REFUSAL = re.compile(r"^(?:i'?m\s+)?sorry,?\s+(?:but\s+)?i\s+(?:can'?t|cannot)\s+(?:assist|help|do)\b", re.I)   # fix 91
_LISTY = re.compile(r"\b(?:to-do|list|task|tick|remind\w*|grocery|add|remove|delete|mark|done|forget|memory"
                    r"|name|erase|wrong)\b", re.I)
# A bare "Got it." / "Understood. Let's try again." after a command: nothing was done (fix 90)
EMPTY_OK = re.compile(r"^(?:got it|understood|okay|ok|sure|alright|noted|it'?s okay)[.!,]?"
                      r"(?:\s+(?:let'?s try (?:that )?again|i'?ll help you with (?:that|this|it))[.!]?)?$", re.I)
RELATIONS = ("mother", "mom", "father", "dad", "sister", "brother", "friend", "wife", "husband", "partner", "cousin",
             "colleague", "son", "daughter", "boss", "teacher")
FORGET_NAME = re.compile(r"\b(?:forget|delete|remove|erase|clear)\s+(?:the\s+|my\s+)?(" + "|".join(RELATIONS)
                         + r")(?:'s|s)?\s+name\b", re.I)
FORGET_THAT = re.compile(r"\b(?:forget|delete|remove|erase)\s+(?:that|it|this)\b|\b(?:it|that)(?:'s| is) (?:wrong|not right)\b"
                         r"|\bthe name is (?:not|wrong)\b", re.I)


def _open_tasks(memory):
    try:
        import todo
        return [r["text"] for r in todo._rows(memory) if not r["done"]]
    except Exception:
        return []


_SOUNDS = [(r"\bparents?\b", "pay rent"), (r"\bpay ?rents\b", "pay rent")]   # fix 91: how "pay rent" is heard


def _plain(s):
    s = re.sub(r"[^a-z0-9 ]", " ", s.lower())
    for a, b in _SOUNDS:
        s = re.sub(a, b, s)
    return " ".join(w for w in s.split() if w not in ("the", "my", "a", "an", "to", "it", "please", "jarvis"))


def closest_task(said, memory, at_least=0.72):
    """The open to-do task that sounds most like what was heard, or None."""
    p = _plain(said)
    if not p:
        return None
    best, score = None, 0.0
    for t in _open_tasks(memory):
        s = SequenceMatcher(None, p, _plain(t)).ratio()
        if s > score:
            best, score = t, s
    return best if score >= at_least else None


def learned_verbs(memory):
    """Words you said for "tick" and then confirmed with yes ("pick" -> tick). Grows with you (fix 91)."""
    try:
        return set(filter(None, (memory.get_setting("tick_words") or "").split(",")))
    except Exception:
        return set()


def learn_verb(memory, verb):
    if verb in ("take",):                    # "take" is too common a word ("take a break"): always ask
        return
    words = learned_verbs(memory)
    if verb not in words:
        try:
            memory.set_setting("tick_words", ",".join(sorted(words | {verb})))
            print(f"   (learned: when you say '{verb}' about a task, you mean tick)")
        except Exception:
            pass


def hint_words(memory):
    """Extra words for speech-to-text: your open tasks and the to-do command words (so it mishears less)."""
    return set(_open_tasks(memory)[:8]) | {"to-do list", "tick", "mark as done"}


def _fact_in(said):
    """'Your mother is Say.' / 'My mother's name is Say.' -> "my mother's name" (what 'forget that' points at)."""
    m = re.search(r"\b(?:your|my) (\w+)(?:'s name)? is ", said or "", re.I)
    if not m:
        return None
    w = m.group(1).lower()
    return f"my {w}'s name" if w in RELATIONS else f"my {w}"


def process(text, memory, last_said=""):
    """-> {"text": what to act on} or {"say": a question / answer, and nothing else happens}."""
    global PENDING
    now = time.time()
    try:                                       # fix 99: set first, so an early answer below can't leave an old one
        import lists
        lists.HEARD = text
    except Exception:
        pass
    text = re.sub(r"^[\s.,!?]+", "", text)                    # fix 90: Whisper's leading ". "
    low = text.lower().strip(" .!?,")
    low = re.sub(r"^[\s\-–.,!?]*(?:(?:hey|ok|okay)\s+)?(?:jarvis)?[\s\-–.,!?]*", "", low)   # fix 91: "- Jarvis, yes."
    low = re.sub(r"^(?:i said|i say|i mean|i meant)[,\s]+", "", low)      # fix 90: "I said no"
    if PENDING and now - PENDING["t"] < 90:
        p, PENDING = PENDING, None
        if YES.match(low):
            print(f"   (yes: {p['text']})")
            if p.get("verb"):
                learn_verb(memory, p["verb"])
            LAST.update(text=p["text"], t=now)
            return {"text": p["text"]}
        if NO.match(low):
            return {"say": "Okay, I didn't change anything."}
    PENDING = None

    fixed = TODO_HEARD.sub("to-do", text)
    if fixed != text:
        heard = ", ".join(dict.fromkeys(m.group(0) for m in TODO_HEARD.finditer(text)))
        print(f"   (heard '{heard} list', using 'to-do list')")

    m = CORR.search(fixed)
    if m and LAST["text"] and now - LAST["t"] < 180:
        right, wrong = m.group(1).lower(), m.group(2).lower()
        redo = LAST["text"]
        if re.search(rf"\b{re.escape(wrong)} list\b", redo, re.I) and wrong != right:
            redo = re.sub(rf"\b{re.escape(wrong)} list\b", f"{right} list", redo, flags=re.I)
        if re.search(rf"\b{re.escape(right)} list\b", redo, re.I):
            print(f"   (correction: doing it again as: {redo})")
            LAST.update(text=redo, t=now)
            return {"text": redo}

    if re.fullmatch(r"(?:(?:my|the|show|open|read)\s+)*to-do list[.?!]*", fixed.strip(), re.I):   # fix 90
        fixed = "what's on my to-do list"
    fixed = re.sub(r"\b(?:reign|rein)(?=\s+(?:next|again|today|tomorrow|later|soon)\b|[?.!]*$)", "rain", fixed,
                   flags=re.I)                                                       # fix 90: "when will it reign next"
    m = FORGET_NAME.search(fixed)                                                    # fix 90
    if m:
        fixed = f"forget my {m.group(1).lower()}'s name"
        print(f"   (forgetting: {fixed})")
    elif FORGET_THAT.search(fixed) and _fact_in(last_said):
        fixed = f"forget {_fact_in(last_said)}"
        print(f"   (forget that = {fixed}, what I just said)")

    m = re.search(r"\b(?:forget|stop|don'?t)\b.*?\b(\w+)'?\s+(?:means?|as|is|for)\s+tick\b", low)   # fix 91: edit
    if m and m.group(1) in learned_verbs(memory):
        memory.set_setting("tick_words", ",".join(sorted(learned_verbs(memory) - {m.group(1)})))
        return {"say": f"Okay. When you say {m.group(1)}, I'll ask first again."}
    if re.search(r"\b(?:which|what) words? (?:mean|means|do you treat as) tick\b", low):
        w = sorted(learned_verbs(memory))
        return {"say": ("When you say " + " or ".join(w) + " about a task, I tick it. "
                        f"To change that, say: forget {w[0]} means tick.") if w
                else "Only tick, check, mark and cross. I haven't learned any other words for it yet."}
    if DONE_LATER.search(fixed):                                                    # fix 91
        return {"say": done_and_later(memory)}

    if PLANS.match(fixed.strip()):
        print("   (asked about the plans: reading your day)")
        fixed = "what's my day"

    m = TICK.match(fixed.strip())
    if m:
        what = re.split(r",?\s+(?:tick|take|pick|check|mark)\b", m.group("what"), flags=re.I)[0].strip(" ,.")   # fix 90
        what = re.sub(r"^(?:(?:on|up|off|out|all|the|task|item|of)[,\s]+)+", "", what, flags=re.I)        # fix 91
        task = closest_task(what, memory, 0.72)
        verb = m.group("verb").lower().split()[0]
        if task and (SURE_VERBS.match(verb) or verb in learned_verbs(memory)):
            fixed = f"tick {task}"
        elif task and closest_task(what, memory, 0.75):
            PENDING = {"text": f"tick {task}", "t": now, "verb": verb}
            return {"say": f"Did you mean: tick off {task}? Say yes or no."}

    LAST.update(text=fixed, t=now)
    try:                                       # fix 91: the list feature checks the AI's items against these words
        import lists
        lists.HEARD = fixed
    except Exception:
        pass
    return {"text": fixed}


DONE_LATER = re.compile(r"\bwhat(?:'s| is| are)?\s+(?:all\s+)?(?:done|finished|completed|ticked)\b"
                        r"|\bwhat(?:'s| is| are)\s+(?:planned\s+)?(?:for\s+)?later\b|\bplanned for later\b"
                        r"|\bwhat did i (?:finish|complete|tick)\b", re.I)


def done_and_later(memory):
    """'What is done and what is planned for later?' -> both, from the to-do list itself."""
    try:
        import todo
        done = [r["text"] for r in todo._today_tasks(memory) if r["done"]]
        later = todo._later(memory)
    except Exception:
        return "I couldn't read your to-do list right now."
    from datetime import date
    def when(r):
        d = date.fromisoformat(r["day"])
        diff = (d - date.today()).days
        return "tomorrow" if diff == 1 else f"on {d:%A}" if diff < 7 else f"on {d.day} {d:%B}"
    done = [t + (f" ({n} times)" if n > 1 else "") for t, n in
            {t: sum(1 for x in done if x.lower() == t.lower()) for t in dict.fromkeys(done)}.items()]
    parts = [("Done today: " + "; ".join(done) + ".") if done else "Nothing is ticked today yet."]
    parts.append(("Planned for later: " + "; ".join(f"{r['text']}, {when(r)}" for r in later) + ".")
                 if later else "Nothing is planned for later.")
    return " ".join(parts)


def no_action_reply(text, memory):
    """What to say instead of an AI reply that claimed an action: the truth, and what to say instead."""
    global PENDING
    words = re.sub(r"^(?:.*?\bto-do list,?\s+)?(?:take|tick|tik|check|mark|cross|do|water)?\s*", "", text.lower())
    task = closest_task(words, memory, 0.6) or closest_task(text, memory, 0.6)
    if task:
        PENDING = {"text": f"tick {task}", "t": time.time()}
        return f"I didn't change anything. Did you mean: tick off {task}? Say yes or no."
    if re.search(r"\b(?:forget|memory|erase|wrong)\b", text, re.I):              # fix 90
        return ("I didn't change anything. To remove something I saved, say it like: forget my mother's name. "
                "Or ask: what do you remember?")
    if _LISTY.search(text):
        return ("I didn't change anything. You can say it like: add milk to my grocery list, "
                "or tick water the plants.")
    return "I can't do that myself, so I didn't change anything."


def check_reply(text, reply, memory):
    """The AI's reply, made honest. Returns (reply, why it was changed or '')."""
    if FAKE_ACTION.search(reply):
        return no_action_reply(text, memory), "the AI claimed an action that didn't happen"
    if EMPTY_OK.match(reply.strip()) and _LISTY.search(text):          # fix 90: "Got it." and nothing done
        return no_action_reply(text, memory), "the AI said okay, but nothing was done"
    if REFUSAL.match(reply.strip()):
        task = closest_task(text, memory, 0.6)
        if task or _LISTY.search(text):
            return no_action_reply(text, memory), "the AI refused a harmless request"
        return "Sorry, I didn't understand that. Could you say it another way?", "the AI refused a harmless request"
    return reply, ""


def cleanup(memory):
    """Once: remove list items that were only a misheard list name (like '2-do list' added to a '2-do' list)."""
    try:
        if memory.get_setting("hear_cleanup_88") == "done":
            return
        rows = memory.db.execute("SELECT id, list, item FROM list_items").fetchall()
        bad = [(i, l, it) for i, l, it in rows
               if re.fullmatch(_TODO_HEARD, l.strip().lower()) and re.search(r"\blist\b", it.lower())]
        for i, l, it in bad:
            memory.db.execute("DELETE FROM list_items WHERE id = ?", (i,))
            print(f"   (cleaned a mishearing: '{it}' in a '{l}' list)")
        memory.db.commit()
        memory.set_setting("hear_cleanup_88", "done")
    except Exception as e:
        print(f"   (mishearing cleanup skipped: {type(e).__name__})")
