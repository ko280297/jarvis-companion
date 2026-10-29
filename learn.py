"""Learning from corrections (fix 74). A small, private list of fixes on this device: no AI training.

  Misheard words:  "add P rent to my to-do list" ... "no, I said pay rent"
                   -> Got it: pay rent. I fixed it, and from now on "P rent" means "pay rent".
                   "When I say P rent, I mean pay rent" teaches it directly.
  Pronunciation:   "Say Krati as Kruh-tee" -> the voice says it that way; the screen still shows Krati.
  Edit:            "What have you learned?"  "Forget the P rent fix"  "Change the P rent fix to pay the rent"
                   "Forget how to say Krati"  "Forget everything you learned"
"""
import re
import time
from difflib import SequenceMatcher

COMMON = {"a", "an", "the", "to", "too", "two", "by", "buy", "bye", "for", "four", "in", "on", "at", "it", "is", "and",
          "or", "of", "my", "me", "i", "you", "we", "be", "do", "go", "no", "so", "up", "this", "that", "with", "what",
          "add", "set", "list", "remind", "yes", "okay", "ok", "jarvis", "here", "hear", "there", "their", "right",
          "write", "one", "won", "new", "knew", "know", "now", "see", "sea", "day", "time", "today", "tomorrow"}

CORRECT = re.compile(r"^(?:(?:no|nope|wrong|sorry|oh)[,.!]?\s+)*(?:i said|i meant|i was saying|i actually said)\s+"
                     r"[\"']?(.+?)[\"']?(?:,?\s+not\s+[\"']?(.+?)[\"']?)?$")
CORRECT_NOT = re.compile(r"^(?:(?:no|nope)[,.!]?\s+)?(?:it'?s|it is)\s+[\"']?(.+?)[\"']?,?\s+not\s+[\"']?(.+?)[\"']?$")
TEACH = re.compile(r"^(?:when|if|whenever) (?:i say|you hear)\s+[\"']?(.+?)[\"']?,?\s+i mean\s+[\"']?(.+?)[\"']?$"
                   r"|^(?:learn|remember) that\s+[\"']?(.+?)[\"']?\s+means\s+[\"']?(.+?)[\"']?$")
SAY_AS = re.compile(r"^(?:please\s+)?(?:say|pronounce)\s+(?!it\b|that\b|this\b|again\b|something\b|hello\b|hi\b)"
                    r"[\"']?(.+?)[\"']?\s+(?:as|like)\s+[\"']?(.+?)[\"']?$"
                    r"|^[\"']?(.+?)[\"']?\s+is pronounced(?:\s+as|\s+like)?\s+[\"']?(.+?)[\"']?$")
SHOW = re.compile(r"\b(?:what (?:have|did) you learn(?:ed|t)?|what(?:'s| is| are) (?:the |your )?(?:learned )?fixes"
                  r"|show (?:me )?(?:what you(?:'ve)? learned|(?:the |your )?fixes)|what words have you learned)\b")
FORGET_ALL = re.compile(r"^(?:forget|clear|delete|erase)\s+(?:all\s+)?(?:everything you(?:'ve)? learned|what you(?:'ve)? learned"
                        r"|(?:all\s+)?(?:the\s+|your\s+)?(?:learned\s+)?(?:fixes|corrections))$")
FORGET = re.compile(r"^(?:forget|delete|remove)\s+(?:the\s+)?(?:fix (?:for\s+)?)?[\"']?(.+?)[\"']?\s+(?:fix|correction)$"
                    r"|^(?:forget|delete|remove)\s+the\s+(?:fix|correction)\s+(?:for\s+)?[\"']?(.+?)[\"']?$"
                    r"|^stop (?:correcting|changing)\s+[\"']?(.+?)[\"']?$"
                    r"|^forget how to (?:say|pronounce)\s+[\"']?(.+?)[\"']?$")
CHANGE = re.compile(r"^(?:change|update)\s+the\s+[\"']?(.+?)[\"']?\s+(?:fix|correction)\s+to\s+[\"']?(.+?)[\"']?$")

PREV = {"text": "", "at": 0.0}       # the last thing heard, for "no, I said ..."
_ready = set()


def _db(memory):
    if id(memory.db) not in _ready:
        memory.db.execute("CREATE TABLE IF NOT EXISTS learned (id INTEGER PRIMARY KEY, kind TEXT, wrong TEXT, "
                          "right TEXT, created REAL)")
        memory.db.commit()
        _ready.add(id(memory.db))
    return memory.db


def _rows(memory, kind=None):
    q = "SELECT id, kind, wrong, right FROM learned" + (" WHERE kind = ?" if kind else "") + " ORDER BY id"
    return _db(memory).execute(q, (kind,) if kind else ()).fetchall()


def _sub(wrong, right, text):
    return re.sub(rf"(?<![a-z0-9]){re.escape(wrong)}(?![a-z0-9])", right, text, flags=re.I)


def apply(text, memory):
    """Every heard sentence: learned fixes are applied. The voice also gets the learned pronunciations."""
    try:
        rows = _rows(memory)
    except Exception:
        return text
    try:
        import tts
        tts.PRONOUNCE.clear()
        for _, kind, wrong, right in rows:
            if kind == "say":
                for w in {wrong, wrong.lower(), wrong.title(), wrong.upper()}:
                    tts.PRONOUNCE[w] = right
    except Exception:
        pass
    if re.search(r"\b(?:fix|fixes|correction|learn|learned|learnt|i said|i meant|i mean|means|pronounced?)\b", text, re.I):
        return text                                         # talking about the fixes themselves: leave the words alone
    new = text
    for _, kind, wrong, right in rows:
        if kind == "heard":
            new = _sub(wrong, right, new)
    if new != text:
        print(f"   (learned fix: {text!r} -> {new!r})")
    return new


def remember_heard(text):
    PREV.update(text=text, at=time.time())


def _norm(s):
    return " ".join(re.findall(r"[a-z0-9']+", s.lower()))


def _misheard_part(prev, right):
    """The words in the last sentence that sound most like what the user meant."""
    words = _norm(prev).split()
    r = _norm(right)
    n = len(r.split())
    best, score = None, 0.0
    for size in range(max(1, n - 1), n + 2):
        for i in range(0, len(words) - size + 1):
            cand = " ".join(words[i:i + size])
            if cand == r:
                continue
            s = SequenceMatcher(None, cand, r).ratio()
            if s > score:
                best, score = cand, s
    return best if score >= 0.6 else None


def _ok_pair(wrong, right):
    if not wrong or not right or _norm(wrong) == _norm(right):
        return "I didn't catch what to change."
    if len(wrong.split()) > 6 or len(right.split()) > 8:
        return "That's a bit long to learn. Say just the words I got wrong."
    if len(wrong.split()) == 1 and (len(wrong) < 4 or wrong.lower() in COMMON):
        return (f"\"{wrong}\" is a very common word, so I'd rather not change it everywhere. "
                "Say the whole phrase instead, for example: when I say P rent, I mean pay rent.")
    return None


def _save(memory, kind, wrong, right):
    db = _db(memory)
    db.execute("DELETE FROM learned WHERE kind = ? AND lower(wrong) = ?", (kind, wrong.lower()))
    db.execute("INSERT INTO learned (kind, wrong, right, created) VALUES (?, ?, ?, ?)", (kind, wrong, right, time.time()))
    db.commit()


def _fix_recent(memory, reminders, wrong, right, seconds=180):
    """What was just saved with the misheard words gets fixed too. Returns how many things changed."""
    since, changed = time.time() - seconds, 0
    db = memory.db
    for table, col in (("todos", "text"), ("list_items", "item"), ("memories", "text")):
        try:
            rows = db.execute(f"SELECT id, {col} FROM {table} WHERE created >= ?", (since,)).fetchall()
        except Exception:
            continue
        for rid, val in rows:
            new = _sub(wrong, right, val)
            if new != val:
                db.execute(f"UPDATE {table} SET {col} = ? WHERE id = ?", (new, rid))
                if table == "memories":
                    try:
                        import memory as _mem
                        db.execute("UPDATE memories SET vec = ? WHERE id = ?",
                                   (_mem._embed(re.sub(r"\s*\[date:.*?\]", "", new.split(" (saved on")[0])).tobytes(), rid))
                    except Exception:
                        pass
                changed += 1
    touched = getattr(reminders, "touched", None) if reminders is not None else None
    if touched and touched[1] >= since:
        row = db.execute("SELECT text FROM reminders WHERE id = ?", (touched[0],)).fetchone()
        if row and _sub(wrong, right, row[0]) != row[0]:
            db.execute("UPDATE reminders SET text = ? WHERE id = ?", (_sub(wrong, right, row[0]), touched[0]))
            changed += 1
    db.commit()
    return changed


def handle(text, memory, reminders=None, is_unsafe=lambda t: False):
    """{"say": ..., "rerun": corrected sentence or None}, or None if this isn't about learning."""
    raw = text.strip().rstrip(".!? ")
    low = re.sub(r"^(?:hey |ok |okay )?jarvis,? ", "", raw.lower()).replace("’", "'")

    if SHOW.search(low):
        rows = _rows(memory)
        if not rows:
            return {"say": "I haven't learned any fixes yet. If I mishear you, just say: no, I said, and the right words."}
        heard = [f"{w} means {r}" for _, k, w, r in rows if k == "heard"]
        said = [f"I say {w} as {r}" for _, k, w, r in rows if k == "say"]
        return {"say": "I've learned: " + "; ".join(heard + said) + ". You can say: forget the fix for, and the words."}

    if FORGET_ALL.match(low):
        n = _db(memory).execute("DELETE FROM learned").rowcount
        memory.db.commit()
        return {"say": f"Done, I've forgotten all {n} fixes." if n else "There was nothing learned to forget."}

    m = FORGET.match(low)
    if m:
        what = _norm(next(g for g in m.groups() if g))
        kind = "say" if low.startswith("forget how") else None
        hits = [r for r in _rows(memory, kind) if _norm(r[2]) == what or _norm(r[3]) == what]
        if not hits:
            return None if low.startswith("stop") else {"say": f"I don't have a fix for {what}."}
        for r in hits:
            memory.db.execute("DELETE FROM learned WHERE id = ?", (r[0],))
        memory.db.commit()
        r = hits[0]
        return {"say": (f"Done. I'll say {r[2]} the normal way again." if r[1] == "say"
                        else f"Done. I won't change {r[2]} to {r[3]} any more.")}

    m = CHANGE.match(low)
    if m:
        what, new = _norm(m.group(1)), raw[m.start(2):m.end(2)]
        hits = [r for r in _rows(memory) if _norm(r[2]) == what] or [r for r in _rows(memory) if _norm(r[3]) == what]
        if not hits:
            return None
        if is_unsafe(new):
            return {"say": "I can't learn that."}
        memory.db.execute("UPDATE learned SET right = ? WHERE id = ?", (new, hits[0][0]))
        memory.db.commit()
        return {"say": f"Updated: from now on, {hits[0][2]} means {new}. It was {hits[0][3]}."}

    m = SAY_AS.match(low)
    if m:
        word = raw[m.start(1):m.end(1)] if m.group(1) else raw[m.start(3):m.end(3)]
        sound = raw[m.start(2):m.end(2)] if m.group(2) else raw[m.start(4):m.end(4)]
        if len(word.split()) <= 3 and len(sound.split()) <= 5 and not is_unsafe(sound):
            _save(memory, "say", word, sound)
            apply("", memory)                                   # the voice uses it straight away
            return {"say": f"Okay, from now on I'll say {word} like this: {word}."}

    m = TEACH.match(low)
    if m:
        wrong = raw[m.start(1):m.end(1)] if m.group(1) else raw[m.start(3):m.end(3)]
        right = raw[m.start(2):m.end(2)] if m.group(2) else raw[m.start(4):m.end(4)]
        problem = _ok_pair(wrong, right)
        if problem:
            return {"say": problem}
        if is_unsafe(right):
            return {"say": "I can't learn that."}
        _save(memory, "heard", wrong.lower(), right)
        return {"say": f"Got it. From now on, {wrong} means {right}."}

    m = CORRECT.match(low)
    if m and not re.match(r"^(?:no|nope|wrong|sorry|oh)\b|^i meant\b", low):
        m = None                                            # "I said hello to my friend" is just talk
    m = m or CORRECT_NOT.match(low)
    if m and PREV["text"] and time.time() - PREV["at"] < 120:
        right = raw[m.start(1):m.end(1)].strip(" ,")
        wrong = m.group(2) or _misheard_part(PREV["text"], right)
        if not wrong:
            return None
        problem = _ok_pair(wrong, right)
        if problem:
            return {"say": problem}
        if is_unsafe(right):
            return {"say": "I can't learn that."}
        _save(memory, "heard", wrong.lower(), right)
        changed = _fix_recent(memory, reminders, wrong, right)
        if changed:
            return {"say": f"Got it: {right}. I fixed it, and from now on, {wrong} means {right}."}
        corrected = _sub(wrong, right, PREV["text"])
        if corrected == PREV["text"]:
            return {"say": f"Got it. From now on, {wrong} means {right}."}
        return {"say": f"Got it. From now on, {wrong} means {right}.", "rerun": corrected}
    return None
