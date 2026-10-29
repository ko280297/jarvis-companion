"""'Look it up?': a careful, consent-based online search for facts Jarvis isn't sure about.
Only a short topic leaves the device (never personal details), every call is logged on the dashboard,
and the answer is read straight from Wikipedia: the LLM never reasons over online text."""
import json
import time   # fix 68
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
    if re.match(r"(?:how (?:to|do|can|should|would|could) |ways to |steps to |tips (?:to|for) )", q):
        return None, "howto"
    if PERSONAL.search(q) or any(n and re.search(rf"\b{re.escape(n.lower())}\b", q) for n in private_names):
        return None, "personal"
    m = MEANING.search(q)
    if m:                                            # "meaning of Sarita" -> search the name itself
        return f"{m.group(1)} given name", None
    topic = re.sub(r"[^a-z0-9 '\-]", " ", LEAD.sub("", q)).split()
    if not topic or len(topic) > MAX_WORDS:
        return None, "too long"
    return " ".join(topic), None


_ABBR = re.compile(r"(?:\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|Prof|Gen|Lt|Col|Capt|Mt|vs|etc|approx|No|Inc|Ltd|Co)"
                   r"|\b[A-Z]|\b(?:e\.g|i\.e|U\.S|U\.K))\.$")    # fix 66: "Mr." / "H. G." don't end a sentence


def _sentences(text):
    out = []
    for part in re.split(r"(?<=[.!?])\s+", text):
        if out and _ABBR.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return out


def _short(extract):
    """The first sentence or two, without brackets (pronunciations, dates of birth...)."""
    text = _nobrackets(extract)   # fix 68
    out = []
    for sentence in _sentences(text):                # fix 66
        if out and len(" ".join(out + [sentence]).split()) > MAX_ANSWER_WORDS:
            break
        out.append(sentence)
    return " ".join(out)


WORK = re.compile(r"\b(?:novel|film|movie|song|album|single|book|play|episode|tv series|television series|video game"
                  r"|band|magazine|newspaper|poem|opera|musical|short story|comic)\b")   # fix 66
PERSON = re.compile(r"\(\s*(?:born|\d{3,4}\s*[\u2013-]\s*\d{2,4})|\b(?:born|died)\b")


_NOT_NAME = {"The", "A", "An", "In", "It", "He", "She", "This", "On", "At", "His", "Her", "They", "During",
             "After", "Before", "From", "As", "By", "United", "New", "There", "These", "Its", "One", "Some", "Many",
             "Both", "When", "While", "Although", "However", "Commander", "Scottish", "American", "English", "Indian",
             "British", "French", "German", "January", "February", "March", "April", "May", "June", "July", "August",
             "September", "October", "November", "December"}
_STEMS = {"wrote": "writ|wrote|author", "written": "writ|wrote|author", "invented": "invent", "invent": "invent",
          "discovered": "discover", "founded": "found", "built": "buil", "made": "made|creat", "created": "creat",
          "painted": "paint", "composed": "compos", "directed": "direct", "first": "first"}


def _nobrackets(text):
    """fix 68: remove (...) and [...] even when nested: "(/taj/; Hindi: [ta(ɛ)l])" -> "" """
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"\s*\([^()]*\)|\s*\[[^\[\]]*\]", "", text)
    return text


def _get(params):
    """fix 68: one quiet retry, so a single slow answer doesn't mean 'no internet'."""
    for attempt in (1, 2):
        try:
            r = requests.get(f"https://{HOST}/w/api.php", headers=HEADERS, timeout=8, params=params)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            code = getattr(e.response, "status_code", None)
            if code == 429 and attempt == 1:                  # fix 69: "too many requests": wait as asked (max 3 s)
                try:
                    time.sleep(min(float(e.response.headers.get("Retry-After", 2)), 3))
                except ValueError:
                    time.sleep(2)
                continue
            if attempt == 2:
                print(f"   (look-up failed: {type(e).__name__} {getattr(e.response, 'status_code', '')})")
                raise
            time.sleep(1.5)


FILMISH = re.compile(r"\b(?:film|movie|tv series|television|album|song|soundtrack|video game|episode|ballet|opera)\b")


def _best_sentence(topic, leads, person_words=(), descs=None):
    """fix 67: for 'who' questions, the sentence in the top pages that answers it best (no AI, just matching words)."""
    words = [w for w in topic.split() if len(w) > 2 and w not in STOP | {"the", "and", "was", "is", "are", "of"}]
    pats = [_STEMS.get(w, re.escape(w[:5]) + rf"\w{{0,{max(3, len(w) - 3)}}}\b" if len(w) > 5 else re.escape(w) + r"\w{0,2}\b")
            for w in words]
    qwords = set(topic.split())
    qstems = {w[:5] for w in qwords}
    best, best_score = None, 0
    for title, text in leads:
        title_words = set(re.findall(r"[a-z]+", title.lower())) - {"the", "of", "and", "a", "an"}
        bonus = 1 if title_words and title_words <= qwords else 0    # "Romeo and Juliet" beats a film's title
        if FILMISH.search((descs or {}).get(title, "").lower()) and not FILMISH.search(topic):
            bonus -= 3                                     # fix 69: "who wrote...": the play, not the 1996 film
        clean = _nobrackets(text)
        surnames = {m.group(2).lower() for m in re.finditer(r"(?=\b([A-Z][a-z]+) ([A-Z][a-z]+)\b)", clean)
                    if m.group(1) not in _NOT_NAME}            # "Neil Armstrong" earlier -> "Armstrong became..."
        for sent in _sentences(clean):
            low = sent.lower()
            hits = sum(1 for p in pats if re.search(rf"\b(?:{p})", low))
            names = [m.group(1) for m in re.finditer(
                         r"(?=\b([A-Z][a-z]+)(?:\s+(?:[A-Z]\.|da|de|di|del|della|van|von|der|bin|al|la|le|du|dos))*"
                         r"\s+([A-Z][a-z]+))", sent)                        # "Alexander Fleming", "Leonardo da Vinci"
                     if m.group(1) not in _NOT_NAME and m.group(2) not in _NOT_NAME
                     and m.group(1).lower()[:5] not in qstems and m.group(2).lower()[:5] not in qstems]   # not "Mona Lisa"
            names += [w for w in re.findall(r"\b[A-Z][a-z]+", sent)
                      if w.lower() in person_words or (w.lower() in surnames and w not in _NOT_NAME
                                                        and w.lower()[:5] not in qstems)]        # "Gandhi"
            if not names:                                    # a "who" answer must name someone
                continue
            score = hits * 2 + 1 + bonus
            if hits and score > best_score and len(sent.split()) <= 60:
                best, best_score = (title, sent), score
    if best and best_score >= 2 * max(1, len(pats) * 0.6):
        return best
    return None


def _search(topic, who=False):
    """Returns (title, text to read), None if nothing was found, or '' if offline.
    fix 66: skips novels / films / songs unless asked.
    fix 68: nested brackets, real names only, one retry.
    fix 67: 2 calls only (search + the top 3 intros together); 'who' questions get the sentence that answers them."""
    try:
        r = _get({"action": "query", "list": "search", "srsearch": topic, "srlimit": 3, "format": "json"})
        _log("ok", sent=topic)
        hits = [h["title"] for h in r.json()["query"]["search"]]
        if not hits:
            return None
        p = _get({"action": "query", "prop": "extracts|description|pageprops", "ppprop": "disambiguation",
                  "exintro": 1, "explaintext": 1, "exlimit": 3, "redirects": 1,
                  "titles": "|".join(hits), "format": "json", "formatversion": 2})
        _log("ok", sent=" | ".join(hits))
        pages = {pg.get("title"): pg for pg in p.json()["query"].get("pages", [])}
        redirect = {x["from"]: x["to"] for x in p.json()["query"].get("redirects", [])}
        asks_work = WORK.search(topic) or re.search(r"\b(?:wrote|written|author|directed|composed|sang|sung|starred)\b", topic)
        leads, works, person_words, descs = [], [], set(), {}
        for t in hits:                                        # in search order
            pg = pages.get(redirect.get(t, t)) or {}
            extract = (pg.get("extract") or "").strip()
            if not extract or "disambiguation" in (pg.get("pageprops") or {}) or "may refer to" in extract[:200]:
                continue
            descs[pg.get("title", t)] = pg.get("description") or ""
            if not asks_work and WORK.search((pg.get("description") or "").lower()):   # "1901 novel by H. G. Wells"
                works.append((pg.get("title", t), extract))
                continue
            if PERSON.search((pg.get("description") or "").lower()):        # a page about a person
                person_words |= set(re.findall(r"[a-z]+", pg.get("title", t).lower()))
            leads.append((pg.get("title", t), extract))
        leads = leads or works                                # only novels / films found: use them
        if not leads:
            return hits[0], ""
        if who:
            return _best_sentence(topic, leads, person_words, descs) or (leads[0][0], None)   # None: no sentence really answers "who"
        return leads[0]
    except requests.HTTPError as e:
        _log("failed", sent=topic)
        return "busy" if getattr(e.response, "status_code", None) == 429 else ""   # fix 69
    except (requests.RequestException, ValueError, KeyError):
        _log("failed", sent=topic)
        return ""

def _lookup(question, private_names):
    topic, why = _topic(question, private_names)
    if not topic:
        if why == "unsafe":
            _log("blocked", attempted="(an unsafe request)")
            return "I won't look that up. I can't help with anything that could hurt someone.", "🛡️ search blocked (safety)"
        if why == "howto":
            return ("I only look up facts, like who or what something is, not how-to steps. "
                    "Try asking: who is, or what is, and a topic.", "🔎 search")
        if why == "personal":
            _log("blocked", attempted="(a personal question)")
            return ("That sounds personal, so I won't send it online. Your private things stay on this device.",
                    "🔒 search blocked (personal)")
        return "That's a bit long to search. Could you ask it in a few words?", "🔎 search"

    print(f"   🔎 Searching Wikipedia for: {topic}")
    _q = question.lower().strip()                    # fix 69: who-question that names nobody yet
    _who = bool(re.match(r"who\b", _q)) and not re.match(r"who (?:is|was|are|were) (?!the |a |an |that |this )", _q)
    result = _search(topic, who=_who)   # fix 66
    if result == "busy":                             # fix 69
        return ("Wikipedia is busy right now. Please ask me again in a minute.", "🌐 search failed (busy)")
    if result == "":
        return ("I can't reach the internet right now, so I can't look that up. Everything else still works offline.",
                "🌐 search failed (offline)")
    if result is None:
        return f"I couldn't find anything about {topic}.", "🌐 internet (search)"

    title, extract = result
    if extract is None:                                   # fix 67: found pages, but none says who
        return (f"I looked it up, but Wikipedia didn't give me a clear answer to that. The closest page was {title}.",
                "🌐 internet (search: Wikipedia)")
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
        if (YES.search(lower) and len(lower.split()) <= 8) or (len(lower.split()) <= 16 and re.search(   # fix 51
                r"\b(?:look (?:it|that) up|search (?:it|that|for it|online)|check online|look online)\b", lower)):
            return _lookup(question, private_names)
        if NO.search(lower):
            return "Okay, no problem.", "🔎 search skipped"
        # anything else: carry on normally
    m = DIRECT.match(lower)
    if m:
        return _lookup(m.group(1), private_names)
    return None