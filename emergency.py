"""Emergency help and basic first aid: fast, offline, no LLM.
Jarvis never calls or messages anyone (that would break the offline boundary). It says the right
numbers clearly, shows them big on the screen, and reads out the user's own emergency contacts.
First-aid tips come from a fixed, checked file (first_aid.json), never from the LLM."""
import json
import re
from pathlib import Path

HERE = Path(__file__).parent
DATA = json.loads((HERE / "emergency_numbers.json").read_text(encoding="utf-8-sig"))
FIRST_AID = json.loads((HERE / "first_aid.json").read_text(encoding="utf-8-sig"))
MAX_CONTACTS = 3

# ----- What counts as which kind of situation -----
CRISIS = re.compile(
    r"\b(hurt|harm|cut|kill)\s+(myself|me)\b|\bsuicid\w*|\bwant to die\b|\bend (?:my life|it all)\b"
    r"|\bdon'?t want to (?:live|be alive|be here)\b|\bno reason to live\b|\bself[- ]harm\b")
ACCIDENTAL = re.compile(
    r"\b(?:accidentally|by mistake|by accident|while (?:cooking|cutting|chopping|shaving)|knife slipped|paper cut)\b")
SEVERE = re.compile(
    r"\b(?:can'?t (?:move|stand|walk|feel)|a lot of blood|lots of blood|won'?t stop bleeding|bone"
    r"|deep cut|unconscious|passed out|hit my head|head injury|can'?t breathe)\b")
WOMEN_SAFETY = re.compile(
    r"\bsomeone is (?:following|harassing|stalking) me\b|\bi(?:'m| am| was) being (?:followed|harassed|stalked)\b"
    r"|\bi(?:'m| am) not safe\b|\bdomestic violence\b|\bmolest\w*")
MEDICAL = re.compile(
    r"\b(?:call (?:an )?ambulance|i (?:fell|fainted|collapsed)|i(?:'m| am) (?:hurt|bleeding|injured)"
    r"|can'?t breathe|chest pain|heart attack|unconscious|accident)\b")
FIRE = re.compile(r"\b(?:there'?s a fire|on fire|fire in|smell (?:of )?smoke)\b")
GENERAL = re.compile(
    r"\bemergency\b(?!\s+contacts?)|\b(?:somebody help|someone help|i need help|please help)\b|^help(?: me)?[!. ]*$")


REPEAT = re.compile(r"\b(?:repeat|again|say that again|what were the numbers)\b")
CALM = re.compile(
    r"\b(?:i'?m|i am)\s+(?:okay|ok|okie|okey|fine|safe|alright|better)\b"
    r"|\b(?:cancel|false alarm|never ?mind|all good)\b")
YES = re.compile(r"^(?:yes|yeah|yep|sure|please|ok|okay|tell me)\b")
NO = re.compile(r"^(?:no|nope|not now|i'?m fine|i'?m okay|i'?ll be fine)\b")

# ----- Emergency contacts -----
ADD_CONTACT = re.compile(r"\b(?:my emergency contact is|add (?:an )?emergency contact|emergency contact)\s+(.+)$")
CONTACT_NUMBER = re.compile(r"\bmy (\w+)'s (?:phone |mobile )?number is\s+(.+)$")   # "My Papa's number is ..."
LIST_CONTACTS = re.compile(r"\b(?:who are|what are|tell me|list) my emergency contacts?\b")
REMOVE_CONTACT = re.compile(r"\b(?:remove|delete) (?:my )?emergency contact\s+(.+)$")
DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}

_active = None        # (kind, spoken text, banner) while an emergency is open
_offer_help = False   # after first aid: waiting for "yes / no" to "who can I call?"


def _reply(say, tone="calm", banner=None, private=False):
    return {"say": " ".join(say.split()), "tone": tone, "banner": banner, "private": private}


def _numbers(memory):
    country = memory.get_setting("home_country") or DATA["default_country"]
    return DATA["countries"].get(country) or DATA["countries"][DATA["default_country"]]


def _spoken(number):
    """'112' -> '1 1 2'; '9876543210' -> '9 8 7 6 5, 4 3 2 1 0' (said digit by digit, in small groups)."""
    groups = [number[i:i + 5] for i in range(0, len(number), 5)]
    return ", ".join(" ".join(g) for g in groups)


def _digits(text):
    """Phone number from speech: digits or number words ('nine eight seven...')."""
    out = []
    for tok in re.findall(r"[a-z]+|\d+", text.lower()):
        if tok.isdigit():
            out.append(tok)
        elif tok in DIGIT_WORDS:
            out.append(DIGIT_WORDS[tok])
    return "".join(out)


def _contacts(memory):
    return json.loads(memory.get_setting("emergency_contacts") or "[]")


def _save_contacts(memory, contacts):
    memory.set_setting("emergency_contacts", json.dumps(contacts))


def _contact_sentence(contacts):
    if not contacts:
        return ""
    parts = [f"{c['name']}, {_spoken(c['number'])}" for c in contacts]
    if len(parts) == 1:
        return f"Your emergency contact is {parts[0]}."
    return "Your emergency contacts are " + "; ".join(parts[:-1]) + "; and " + parts[-1] + "."


def _first_aid_for(lower):
    """Which first-aid topic does this sentence talk about? (from first_aid.json)"""
    for key, info in FIRST_AID.items():
        if any(re.search(rf"\b{t}\b", lower) for t in info["triggers"]):
            return info
    return None


def _build(kind, memory):
    nums = _numbers(memory)
    contacts = _contacts(memory)
    name = memory.get_setting("user_name")
    emerg = nums["emergency"]

    if kind == "crisis":
        mh = nums["mental_health"]
        speech = (f"I'm really sorry you're feeling this way{', ' + name if name else ''}. "
                  "You don't have to go through this alone. "
                  f"Please talk to someone you trust, or call {mh['label']} on {_spoken(mh['number'])}. "
                  "They're there to listen, any time. "
                  f"If you might act on these thoughts right now, please call {_spoken(emerg['number'])}. ")
        if contacts:
            c = contacts[0]
            speech += f"You could also reach {c['name']} on {_spoken(c['number'])}. "
        speech += "I'm right here with you."
        lines = [mh, emerg]
        title = "You're not alone. Help is here."
    else:
        extra = {"medical": ["ambulance"], "fire": ["fire"],
                 "women": ["women", "women_police"], "general": []}[kind]
        lines = [emerg] + [nums[k] for k in extra]
        speech = f"Okay, I'm here with you. If you're in danger, call {_spoken(emerg['number'])} right now. "
        speech += " ".join(f"{nums[k]['label']}: {_spoken(nums[k]['number'])}." for k in extra) + " "
        speech += _contact_sentence(contacts) + " Should I repeat the numbers?"
        title = "Emergency"

    lines = [[n["label"], n["number"]] for n in lines] + [[c["name"], c["number"]] for c in contacts]
    return " ".join(speech.split()), {"kind": kind, "title": title, "lines": lines}


def handle_emergency(text, memory):
    """Returns a dict {say, tone, banner, private} if this was about an emergency or first aid,
    otherwise None. banner=None clears the banner on the screen."""
    global _active, _offer_help
    lower = text.lower().strip(" .!?,")

    # 0. Answer to "Would you like me to tell you who you can call?"
    if _offer_help:
        _offer_help = False
        if YES.search(lower):
            nums = _numbers(memory)
            contacts = _contacts(memory)
            amb = nums["ambulance"]
            say = (_contact_sentence(contacts) or "You haven't added any emergency contacts yet.") + \
                  f" If it gets worse, call {_spoken(amb['number'])} for an ambulance."
            lines = [[c["name"], c["number"]] for c in contacts] + [[amb["label"], amb["number"]]]
            return _reply(say, banner={"kind": "first_aid", "title": "People you can call", "lines": lines})
        
        if NO.search(lower) and len(lower.split()) <= 3:      # just "no" / "no thanks"
            return _reply("Okay. Take care, and tell me if it gets worse.", tone="gentle", banner=None)
        # "No, who are my emergency contacts?" -> carry on and answer the rest
        # anything else: carry on normally

    # 1. Accidents first, so "I accidentally cut myself while cooking" is first aid, not a crisis
    accidental = bool(ACCIDENTAL.search(lower))
    aid = _first_aid_for(lower)

    # 2. Feeling unsafe with yourself: gentle, and never saved anywhere
    if CRISIS.search(lower) and not accidental:
        speech, banner = _build("crisis", memory)
        _active = ("crisis", speech, banner)
        return _reply(speech, "gentle", banner, private=True)

    # 3. Serious signs always go straight to emergency numbers
    if SEVERE.search(lower):
        speech, banner = _build("medical", memory)
        _active = ("medical", speech, banner)
        return _reply(speech, "urgent", banner)

    # 4. Small injuries: short first-aid tips, then offer help
    if aid:
        _offer_help = True
        say = f"Oh no, I'm sorry. {aid['advice']} Would you like me to tell you who you can call for help?"
        banner = {"kind": "first_aid", "title": aid["title"], "text": aid["advice"],
                  "lines": [], "note": "General first-aid tips, not medical advice."}
        return _reply(say, "gentle", banner)

    # 5. While an emergency is open: repeat, or calm down
    if _active:
        if REPEAT.search(lower):
            kind, speech, banner = _active
            return _reply(speech, "gentle" if kind == "crisis" else "urgent", banner, private=kind == "crisis")
        if CALM.search(lower):
            _active = None
            return _reply("I'm glad you're okay. I'm here if you need me.", "gentle", None)

    # 6. Managing emergency contacts
    m = REMOVE_CONTACT.search(lower)
    if m:
        target = m.group(1).strip()
        contacts = _contacts(memory)
        kept = [c for c in contacts if c["name"].lower() not in target]
        if len(kept) == len(contacts):
            return _reply("I couldn't find that emergency contact.")
        _save_contacts(memory, kept)
        return _reply("Done, I've removed that emergency contact.")

    if LIST_CONTACTS.search(lower):
        return _reply(_contact_sentence(_contacts(memory)) or "You haven't added any emergency contacts yet.")
    
    m = ADD_CONTACT.search(lower)
    m2 = CONTACT_NUMBER.search(lower) if not m else None
    if m or m2:
        if m:
            rest = m.group(1)
            name = re.split(r"\d|\b(?:" + "|".join(DIGIT_WORDS) + r")\b", rest)[0].strip(" ,.-").title()
        else:
            name, rest = m2.group(1).title(), m2.group(2)
        number = _digits(rest)
        if len(number) < 3 or not name:
            return _reply("Please tell me a name and a number, like: my emergency contact is Papa, 98765 43210.")
        expected = _numbers(memory).get("phone_digits")
        if expected and len(number) > 5 and len(number) != expected:
            return _reply(f"I heard {len(number)} digits: {_spoken(number)}. A mobile number here has {expected}. "
                          "Could you say it again, slowly?")
        contacts = [c for c in _contacts(memory) if c["name"].lower() != name.lower()]   # same name = update
        if len(contacts) >= MAX_CONTACTS:
            return _reply(f"You already have {MAX_CONTACTS} emergency contacts. "
                          "You can remove one first, by saying: remove emergency contact, and the name.")
        contacts.append({"name": name, "number": number})
        _save_contacts(memory, contacts)
        return _reply(f"Saved. {name}: {_spoken(number)}. If that number isn't right, just tell me again.")
    

    # 7. Other emergencies
    for kind, pattern in (("women", WOMEN_SAFETY), ("medical", MEDICAL), ("fire", FIRE), ("general", GENERAL)):
        if pattern.search(lower):
            speech, banner = _build(kind, memory)
            _active = (kind, speech, banner)
            return _reply(speech, "urgent", banner)

    return None