"""Notes (fix 82): longer notes you speak, kept inside Jarvis's encrypted memory.

  "Take a note"                      -> Jarvis listens; speak as long as you like; "end note" saves it
  "Take a note called meeting prep"  -> the same, with a name
  "Take a note: buy a gift for Riya" -> saved straight away
  "Read my notes" / "read my meeting prep note" / "read the last note"
  "Add to my meeting prep note: ..." / "add to the note" (then speak) / "end note"
  "In my meeting prep note, change budget to final budget"   "Rename the note to client call"   "Undo"
  "Delete my meeting prep note"      -> needs the PIN
  "Send my meeting prep note to my phone" / "PDF of my note" -> QR code on the screen; save as PDF on the phone
  "Send my to-do list to my phone"   -> the same for the to-do list
Notes never leave the device by themselves: only when you ask, only over your own Wi-Fi, for 10 minutes.
"""
import re
import time

_ready = set()
_dictating = None        # {"id": note id, "t": last time something was said, "new": bool}
_undo = None             # {"t", "id", "title", "body"}
END = re.compile(r"^(?:ok(?:ay)?,?\s+)?(?:end(?: the)? note|stop(?: the)? note|save(?: the)? note|save it|finish(?: the)? note"
                 r"|that'?s (?:it|all)|i'?m done|done|end|stop|full stop that'?s it)$")
CANCEL = re.compile(r"^(?:cancel|discard|delete|forget)(?: the| this)? note$|^cancel(?: it)?$|^never ?mind$")
NEW = re.compile(r"^(?:please\s+)?(?:can you\s+)?(?:take|make|create|start|write|new|add)(?: (?:a|me a|one))?(?: new)? note"
                 r"(?:\s+(?:called|named|titled|about)\s+(.+?))?(?:\s*[:,]\s*|\s+(?:that|saying|says)\s+|$)(.*)$")
SHORT = re.compile(r"^note\s*[:,-]\s*(.+)$")
LIST = re.compile(r"\b(?:read|show|list|tell me|what are|what're|which)\b.*\b(?:my|the|all)\s+notes\b|^(?:my )?notes$"
                  r"|\bhow many notes\b|\bwhat notes\b")
READ = re.compile(r"^(?:please\s+)?(?:read|open|show|what'?s in|what is in|tell me)(?: me| out)? (?:my |the )?"
                  r"(?:(last|latest|newest|first|\w+ \w+ |\w+ )?)\s*note(?: called (.+))?$|^(?:read|open) (?:my |the )?note (?:called|about|on) (.+)$")
ADD = re.compile(r"^(?:please\s+)?(?:add|append|put)(?: this| that)? (?:to|in|into) (?:my |the )?(.+?)?\s*note"
                 r"(?:\s*[:,]\s*|\s+that\s+|\s+saying\s+|$)(.*)$|^continue (?:my |the )?(.+?)?\s*note$")
EDIT = re.compile(r"^(?:in|on) (?:my |the )?(.+?)?\s*note,?\s+(?:change|replace) (.+?) (?:to|with) (.+)$"
                  r"|^(?:change|replace) (.+?) (?:to|with) (.+?) in (?:my |the )?(.+?)?\s*note$")
RENAME = re.compile(r"^(?:rename|call) (?:my |the |this )?(.+?)?\s*note (?:to |as )?(.+)$")
DELETE = re.compile(r"^(?:please\s+)?(?:delete|remove|erase) (?:my |the )?(.+?)?\s*note$")
SHARE_NOTE = re.compile(r"\b(?:send|share|export|download|print|pdf|save)\b.*\bnote\b|\bnote\b.*\b(?:pdf|to my phone)\b")
SHARE_TODO = re.compile(r"\b(?:send|share|export|download|print|pdf)\b.*\b(?:to-?\s?do|todo|tasks?)\b"
                        r"|\b(?:to-?\s?do|todo|tasks?)\b.*\b(?:pdf|to my phone)\b")
UNDO = re.compile(r"^(?:no,? )?(?:undo(?: (?:it|that))?|change it back|put it back)$")


def _db(memory):
    if id(memory.db) not in _ready:
        memory.db.execute("CREATE TABLE IF NOT EXISTS notes (id INTEGER PRIMARY KEY, title TEXT, body TEXT, "
                          "created REAL, updated REAL)")
        memory.db.commit()
        _ready.add(id(memory.db))
    return memory.db


def _all(memory):
    return [dict(zip(("id", "title", "body", "created", "updated"), r))
            for r in _db(memory).execute("SELECT id, title, body, created, updated FROM notes ORDER BY id")]


def _words(s):
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2} - {"the", "and", "note", "notes", "my"}


def _find(memory, said):
    notes = _all(memory)
    if not notes:
        return []
    said = (said or "").strip().lower()
    if not said or said in ("last", "latest", "newest", "this", "that"):
        return [max(notes, key=lambda n: n["updated"])]
    if said == "first":
        return [notes[0]]
    w = _words(said)
    scored = [(len(w & _words(n["title"])) * 2 + len(w & _words(n["body"])), n) for n in notes]
    best = max(s for s, _ in scored)
    return [n for s, n in scored if s == best and s > 0]


def _title_from(body):
    words = re.sub(r"[^\w\s']", "", body).split()[:5]
    t = " ".join(words)
    return (t[:1].upper() + t[1:]) if t else "Note"


def _remember_undo(n):
    global _undo
    _undo = {"t": time.time(), "id": n["id"], "title": n["title"], "body": n["body"]}
    for mod, attr in (("memfix", "LAST"), ("todo", "_undo"), ("lists", "_undo")):
        try:
            setattr(__import__(mod), attr, None)
        except ImportError:
            pass


def _save(memory, title, body):
    now = time.time()
    cur = _db(memory).execute("INSERT INTO notes (title, body, created, updated) VALUES (?, ?, ?, ?)",
                              (title, body, now, now))
    memory.db.commit()
    return cur.lastrowid


def _set(memory, nid, title=None, body=None):
    db = _db(memory)
    if title is not None:
        db.execute("UPDATE notes SET title = ?, updated = ? WHERE id = ?", (title, time.time(), nid))
    if body is not None:
        db.execute("UPDATE notes SET body = ?, updated = ? WHERE id = ?", (body, time.time(), nid))
    db.commit()


def _get(memory, nid):
    return next((n for n in _all(memory) if n["id"] == nid), None)


def delete(memory, nid):
    """After the PIN was typed."""
    n = _get(memory, nid)
    if not n:
        return "That note is already gone."
    _db(memory).execute("DELETE FROM notes WHERE id = ?", (nid,))
    memory.db.commit()
    return f"Deleted the note: {n['title']}."


def _join(body, more):
    more = more.strip()
    if not more:
        return body
    if more[-1] not in ".!?":
        more += "."
    return (body + " " + more[:1].upper() + more[1:]).strip()


def dictating():
    return bool(_dictating) and time.time() - _dictating["t"] < 180


def handle(text, memory, is_unsafe=lambda t: False):
    """{"say", "card"?, "share"?: (title, lines, checks), "pin"?: note id} or None if this isn't about notes."""
    global _dictating, _undo
    raw = text.strip()
    low = re.sub(r"^(?:hey |ok |okay )?jarvis,?\s*", "", raw.lower().strip(" .!?")).replace("’", "'")

    if dictating():                                       # everything said now goes into the note
        d = _dictating
        n = _get(memory, d["id"])
        if CANCEL.match(low):
            _dictating = None
            if d["new"] and n:
                _db(memory).execute("DELETE FROM notes WHERE id = ?", (n["id"],))
                memory.db.commit()
            return {"say": "Okay, I didn't keep that note." if d["new"] else "Okay, I stopped adding to it."}
        if END.match(low):
            _dictating = None
            if not n or not n["body"]:
                if n:
                    _db(memory).execute("DELETE FROM notes WHERE id = ?", (n["id"],))
                    memory.db.commit()
                return {"say": "The note was empty, so I didn't keep it."}
            if n["title"] == "Note":
                _set(memory, n["id"], title=_title_from(n["body"]))
                n = _get(memory, n["id"])
            return {"say": f"Saved your note: {n['title']}. {len(n['body'].split())} words. "
                           "Say: read my note, or send it to my phone.",
                    "card": {"title": "📝 " + n["title"], "lines": [n["body"]]}}
        if is_unsafe(raw):
            return {"say": "I can't write that down."}
        text_in = re.sub(r"\s*(?:,\s*)?(?:end note|end the note)[.!]*$", "", raw, flags=re.I)
        _set(memory, d["id"], body=_join(n["body"] if n else "", text_in))
        d["t"] = time.time()
        if text_in != raw:                                 # "...and that's the plan, end note"
            return handle("end note", memory, is_unsafe)
        n = _get(memory, d["id"])
        return {"say": "Got it. Keep going, or say: end note.",
                "card": {"title": "📝 " + n["title"] + " (listening…)", "lines": [n["body"]]}}

    if UNDO.match(low) and _undo and time.time() - _undo["t"] < 120:
        others = []
        for mod, attr in (("memfix", "LAST"), ("todo", "_undo"), ("lists", "_undo")):
            try:
                v = getattr(__import__(mod), attr)
                if v:
                    others.append(v.get("t", 0))
            except ImportError:
                pass
        if not others or max(others) < _undo["t"]:
            u, _undo = _undo, None
            _set(memory, u["id"], title=u["title"], body=u["body"])
            return {"say": f"Okay, I've put the note back the way it was: {u['title']}."}

    m = SHARE_TODO.search(low)
    if m and not re.search(r"\bnote\b", low):
        import todo
        rows = todo._today_tasks(memory) + todo._later(memory)
        if not rows:
            return {"say": "Your to-do list is empty, so there's nothing to send."}
        lines = [("✔ " if r["done"] else "") + todo._spoken(r) for r in rows]
        return {"say": "Scan the code on my screen with your phone. Your to-do list opens there, and you can save it "
                       "as a PDF. It travels only over your own Wi-Fi, for 10 minutes.",
                "share": ("To-do list", lines, True)}

    m = SHARE_NOTE.search(low)
    if m:
        said = re.sub(r"\b(?:send|share|export|download|print|pdf|save|as|a|an|of|my|the|note|to|phone|me|please"
                      r"|it|on|in)\b", " ", low)
        hits = _find(memory, said.strip())
        if not hits:
            return {"say": "You don't have any notes yet. Say: take a note."}
        n = hits[0]
        return {"say": f"Scan the code on my screen. Your note, {n['title']}, opens on your phone, and you can save it "
                       "as a PDF. Only over your own Wi-Fi, for 10 minutes.",
                "share": (n["title"], [p for p in re.split(r"(?<=[.!?])\s+", n["body"]) if p], False)}

    m = SHORT.match(low)
    if m:
        body = raw[m.start(1):].strip()
        if is_unsafe(body):
            return {"say": "I can't write that down."}
        nid = _save(memory, _title_from(body), _join("", body))
        n = _get(memory, nid)
        return {"say": f"Saved your note: {n['title']}.", "card": {"title": "📝 " + n["title"], "lines": [n["body"]]}}

    m = NEW.match(low)
    if m and not re.match(r"^(?:make|add) (?:a )?note (?:of|that)\b.*\b(?:list|reminder)\b", low):
        title = raw[m.start(1):m.end(1)].strip(" .,") if m.group(1) else ""
        body = raw[m.start(2):].strip(" .") if m.group(2) else ""
        if is_unsafe(body):
            return {"say": "I can't write that down."}
        if body:
            nid = _save(memory, title[:1].upper() + title[1:] if title else _title_from(body), _join("", body))
            n = _get(memory, nid)
            return {"say": f"Saved your note: {n['title']}.", "card": {"title": "📝 " + n["title"], "lines": [n["body"]]}}
        nid = _save(memory, title[:1].upper() + title[1:] if title else "Note", "")
        _dictating = {"id": nid, "t": time.time(), "new": True}
        return {"say": "Okay, I'm listening. Speak your note, and say: end note, when you're done.",
                "card": {"title": "📝 " + (title or "New note") + " (listening…)", "lines": ["Say: end note, to save it"]}}

    m = ADD.match(low)
    if m:
        hits = _find(memory, m.group(1) or m.group(3) or "")
        if not hits:
            return {"say": "I couldn't find that note. Say: read my notes, to hear them."}
        n = hits[0]
        more = raw[m.start(2):].strip() if m.group(2) else ""
        if more:
            _remember_undo(n)
            _set(memory, n["id"], body=_join(n["body"], more))
            return {"say": f"Added to your note: {n['title']}. Say undo if that's wrong."}
        _dictating = {"id": n["id"], "t": time.time(), "new": False}
        return {"say": f"Okay, I'm adding to {n['title']}. Go ahead, and say: end note, when you're done."}

    m = EDIT.match(low)
    if m:
        where, old, new = (m.group(1), m.group(2), m.group(3)) if m.group(2) else (m.group(6), m.group(4), m.group(5))
        hits = _find(memory, where or "")
        if not hits:
            return None
        n = hits[0]
        if not re.search(re.escape(old), n["body"], re.I):
            return {"say": f"I couldn't find \"{old}\" in your note, {n['title']}."}
        if is_unsafe(new):
            return {"say": "I can't write that down."}
        _remember_undo(n)
        _set(memory, n["id"], body=re.sub(re.escape(old), new, n["body"], count=1, flags=re.I))
        return {"say": f"Done: {new} instead of {old}, in your note {n['title']}. Say undo if that's wrong."}

    m = RENAME.match(low)
    if m and _all(memory):
        hits = _find(memory, m.group(1) or "")
        if hits:
            n = hits[0]
            new = raw[m.start(2):].strip(" .")
            _remember_undo(n)
            _set(memory, n["id"], title=new[:1].upper() + new[1:])
            return {"say": f"Renamed: {new}. It was {n['title']}."}

    m = DELETE.match(low)
    if m:
        hits = _find(memory, m.group(1) or "")
        if not hits:
            return {"say": "I couldn't find that note."} if _all(memory) else None
        return {"say": "", "pin": hits[0]["id"]}

    if LIST.search(low):
        notes = _all(memory)
        if not notes:
            return {"say": "You don't have any notes yet. Say: take a note."}
        return {"say": f"You have {len(notes)} note{'s' if len(notes) != 1 else ''}: "
                       + "; ".join(f"{i + 1}. {n['title']}" for i, n in enumerate(notes))
                       + ". Say: read, and the name.",
                "card": {"title": "📝 Notes", "lines": [n["title"] for n in notes]}}

    m = READ.match(low)
    if m:
        said = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        hits = _find(memory, said)
        if not hits:
            return {"say": "You don't have any notes yet. Say: take a note."} if not _all(memory) else \
                {"say": "I couldn't find that note. Say: read my notes, to hear them."}
        n = hits[0]
        return {"say": f"{n['title']}. {n['body']}", "card": {"title": "📝 " + n["title"], "lines": [n["body"]]}}
    return None
