"""'Look it up?': a careful, consent-based online search for facts Jarvis isn't sure about.
Only a short topic leaves the device (never personal details), every call is logged on the dashboard,
and the answer is read straight from Wikipedia: the LLM never reasons over online text."""
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from safety import is_unsafe

import requests

LOG_FILE = Path("online_log.jsonl")
HOST = "en.wikipedia.org"
HEADERS = {"User-Agent": "JarvisCompanion/1.0 (private on-device hackathon assistant)"}
MAX_WORDS = 10             # longest topic we send
MAX_ANSWER_WORDS = 50      # how much of the summary we read out

DIRECT = re.compile(r"^(?:please )?(?:look up|search(?: online)?(?: for)?|find out about)\s+(.+)$")
YES = re.compile(r"\b(?:yes|yeah|yep|sure|ok|okay|please|go ahead|look it up|do it)\b")
NO = re.compile(r"^(?:no|nope|not now|never ?mind|don'?t)\b")
PERSONAL = re.compile(r"\b(?:my|me|mine|i|i'm|i am|our|we|us)\b")
LEAD = re.compile(r"^(?:who|what|when|where|which|why|how)(?:'s| is| are| was| were| did| does| do)?\s+"
                  r"|^(?:tell me about|what about)\s+")
MEANING = re.compile(r"\b(?:meaning of|what does)(?: the name)? ([a-z]+)(?: mean)?\b")
STOP = {"the", "meaning", "given", "name", "what", "about", "with", "from", "that", "this", "won", "who", "when"}

_pending = None     # the question Jarvis just offered to look up


def offer_lookup(question):
    """Called after 'I'm not sure': remember the question in case the user says yes."""
    global _pending
    _pending = None if is_unsafe(question) else question

def cancel_lookup():
    """Forget any pending 'want me to look it up?' offer."""
    global _pending
    _pending = None


def _log(status, sent=None, attempted=None):
    entry = {"time": datetime.now().isoformat(timespec="seconds"), "tool": "search",
             "host": HOST if sent else None, "sent": sent, "status": status}
    if attempted:
        entry["attempted"] = attempted
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _topic(question, private_names):
    """'Who is the president of France?' -> 'the president of france', or (None, reason) if it can't leave."""
    q = question.lower().strip(" ?.!")
    if is_unsafe(q):
        return None, "unsafe"
    if PERSONAL.search(q) or any(n and re.search(rf"\b{re.escape(n.lower())}\b", q) for n in private_names):
        return None, "personal"
    m = MEANING.search(q)
    if m:                                            # "meaning of Sarita" -> search the name itself
        return f"{m.group(1)} given name", None
    topic = re.sub(r"[^a-z0-9 '\-]", " ", LEAD.sub("", q)).split()
    if not topic or len(topic) > MAX_WORDS:
        return None, "too long"
    return " ".join(topic), None


def _short(extract):
    """The first sentence or two, without brackets (pronunciations, dates of birth...)."""
    text = re.sub(r"\s*\([^)]*\)", "", extract)
    out = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if out and len(" ".join(out + [sentence]).split()) > MAX_ANSWER_WORDS:
            break
        out.append(sentence)
    return " ".join(out)


def _search(topic):
    """Returns (title, extract), None if nothing was found, or '' if offline."""
    try:
        r = requests.get(f"https://{HOST}/w/api.php", headers=HEADERS, timeout=8,
                         params={"action": "query", "list": "search", "srsearch": topic,
                                 "srlimit": 1, "format": "json"})
        r.raise_for_status()
        _log("ok", sent=topic)
        hits = r.json()["query"]["search"]
        if not hits:
            return None
        title = hits[0]["title"]
        s = requests.get(f"https://{HOST}/api/rest_v1/page/summary/{quote(title.replace(' ', '_'))}",
                         headers=HEADERS, timeout=8)
        s.raise_for_status()
        _log("ok", sent=title)
        data = s.json()
        return title, ("" if data.get("type") == "disambiguation" else data.get("extract", ""))
    except (requests.RequestException, ValueError, KeyError):
        _log("failed", sent=topic)
        return ""

def _lookup(question, private_names):
    topic, why = _topic(question, private_names)
    if not topic:
        if why == "unsafe":
            _log("blocked", attempted="(an unsafe request)")
            return "I won't look that up. I can't help with anything that could hurt someone.", "🛡️ search blocked (safety)"
        if why == "personal":
            _log("blocked", attempted="(a personal question)")
            return ("That sounds personal, so I won't send it online. Your private things stay on this device.",
                    "🔒 search blocked (personal)")
        return "That's a bit long to search. Could you ask it in a few words?", "🔎 search"

    print(f"   🔎 Searching Wikipedia for: {topic}")
    result = _search(topic)
    if result == "":
        return ("I can't reach the internet right now, so I can't look that up. Everything else still works offline.",
                "🌐 search failed (offline)")
    if result is None:
        return f"I couldn't find anything about {topic}.", "🌐 internet (search)"

    title, extract = result
    if not extract:
        return (f"I found a page called {title}, but it covers several things. Could you be more specific?",
                "🌐 internet (search)")

    keys = [w for w in topic.split() if len(w) > 3 and w not in STOP]
    if keys and not any(k in f"{title} {extract}".lower() for k in keys):   # a page about something else
        return (f"I looked it up, but I couldn't find a clear answer about {keys[0].title()}.",
                "🌐 internet (search)")
    return f"According to Wikipedia: {_short(extract)}", "🌐 internet (search: Wikipedia)"




def handle_lookup(text, private_names):
    """Returns (reply, route) if this was about looking something up, otherwise None."""
    global _pending
    lower = text.lower().strip(" .!?,")
    if _pending:
        question, _pending = _pending, None
        if YES.search(lower) and len(lower.split()) <= 8:
            return _lookup(question, private_names)
        if NO.search(lower):
            return "Okay, no problem.", "🔎 search skipped"
        # anything else: carry on normally
    m = DIRECT.match(lower)
    if m:
        return _lookup(m.group(1), private_names)
    return None