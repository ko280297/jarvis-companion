"""Jarvis in Hindi (fix 97). Everything runs on this device. Nothing is sent online, not even to download.

How one Hindi turn works:
  1. HEAR        Vosk Hindi turns your voice into Hindi text                 (on device, about 1 s)
  2. NUMBERS     the code turns Hindi number words into digits              "साढ़े छह बजे" -> "6:30 बजे"
  3. OWN WORDS   Jarvis's words from hindi_words.txt are kept exact          "उसे टिक कर दो" -> "tick it off"
  4. UNDERSTAND  IndicTrans2 (AI4Bharat, IIT Madras) translates to English   (an on-device translation AI)
  5. CHECK       the code checks that every number, time and day survived.
                 If something got lost, Jarvis says what it heard and asks you to say it again (never guesses).
  6. JARVIS      the SAME English features run: reminders, lists, to-do, notes, memory, the on-device AI...
  7. REPLY       the answer is translated back in short pieces (each list item alone) and checked again.
                 A piece that fails the check stays in English, so Jarvis never says a wrong number.
  8. SPEAK       the Piper Hindi voice (rohan).

Phone typing in Hinglish ("kal shaam 5 baje bank jaana hai") is first written in Hindi letters
(our own offline word list + spelling rules), then goes through the same steps.

Edit options:  "speak English" / "इंग्लिश में बात करो" switches back.  "speak in Hindi" switches on.
               hindi_words.txt (plain text, next to this file): add your own words, one per line.
"""
import json
import os
import re
import threading
import time
import unicodedata
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models_hi"                                  # downloaded once by hindi_bench.py --setup
VOSK_DIR = MODELS / "vosk-model-small-hi-0.22"
VOICE = MODELS / "hi_IN-rohan-medium"                        # + .onnx and .onnx.json
IT_HI_EN = "ai4bharat/indictrans2-indic-en-dist-200M"
IT_EN_HI = "ai4bharat/indictrans2-en-indic-dist-200M"
WORDS_FILE = HERE / "hindi_words.txt"

DEV = re.compile(r"[ऀ-ॿ]")                         # any Hindi (Devanagari) letter
_mode = {"wanted": False}
_parts = {"vosk": None, "to_en": None, "to_hi": None}        # filled by _load(), in the background
_load_state = {"busy": False, "error": "", "secs": 0.0}
_cache = {}                                                   # English piece -> Hindi (this session only, never saved)
LAST = {"heard": "", "english": ""}                           # this turn: what was heard in Hindi, what it became

SETUP_HELP = ("Hindi needs its on-device models first. On the laptop, run once: "
              ".venv\\Scripts\\python.exe hindi_bench.py --setup")


# ------------------------------------------------------------------ mode and loading
def screen_lang():
    """fix 102: tell the screen which language Jarvis speaks (the 'हिंदी' badge, namaste, Hindi status words)."""
    import sys
    d = sys.modules.get("dashboard")
    if d:
        d.update(lang="hi" if on() else "en")


def models_present():
    """Are the Hindi models on this device? (Checked offline: only the folders are looked at.)"""
    hf = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    have_it = all((hf / ("models--" + r.replace("/", "--"))).exists() for r in (IT_HI_EN, IT_EN_HI))
    return VOSK_DIR.exists() and Path(str(VOICE) + ".onnx").exists() and have_it


def ready():
    return all(_parts.values())


def on():
    """Hindi is used only when it was asked for AND everything has loaded."""
    return _mode["wanted"] and ready()


def start(memory):
    """At start-up: if Hindi was on last time, load it again in the background."""
    if (memory.get_setting("language") or "en") == "hi" and models_present():
        _mode["wanted"] = True
        _load_async()


def _load_async():
    if ready() or _load_state["busy"]:
        return
    _load_state["busy"] = True
    threading.Thread(target=_load, daemon=True).start()


def _load():
    t = time.perf_counter()
    try:
        # never contact the internet: the models are already on this device
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import vosk
        vosk.SetLogLevel(-1)
        _parts["vosk"] = vosk.Model(str(VOSK_DIR))
        _parts["to_en"] = _translator(IT_HI_EN, "hin_Deva", "eng_Latn")
        _parts["to_hi"] = _translator(IT_EN_HI, "eng_Latn", "hin_Deva")
        _load_state["error"] = ""
        print(f"   🇮🇳 Hindi ready (loaded in {time.perf_counter() - t:.0f}s)")
        screen_lang()
    except Exception as e:
        _load_state["error"] = f"{type(e).__name__}: {e}"
        print(f"   ⚠️ Hindi could not load: {_load_state['error'][:200]}")
    finally:
        _load_state["busy"] = False
        _load_state["secs"] = time.perf_counter() - t


def _translator(repo, src, tgt):
    """IndicTrans2 for one direction. Takes a list of pieces, returns the list translated (one batch = faster)."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(repo, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(repo, trust_remote_code=True)
    model.eval()
    lock = threading.Lock()
    fast = {"ok": False}

    def slow(enc, limit):
        # use_cache=False: IndicTrans2's own code doesn't work with the newest transformers' cache
        return model.generate(**enc, num_beams=1, max_length=limit, use_cache=False)

    def quick(enc, limit):
        """fix 99: the same greedy translation, but remembering the earlier words (the 'cache') ourselves, in the
        old format IndicTrans2's code understands. Much faster on long replies. Only used if it gives exactly
        the same result as the slow way (checked once, at loading)."""
        cfg = model.config
        start = cfg.decoder_start_token_id
        start = cfg.eos_token_id if start is None else start
        enc_out = model.get_encoder()(input_ids=enc["input_ids"], attention_mask=enc["attention_mask"], return_dict=True)
        n = enc["input_ids"].shape[0]
        seq = torch.full((n, 1), start, dtype=torch.long)
        done = torch.zeros(n, dtype=torch.bool)
        past = None
        for _ in range(limit - 1):
            out = model(encoder_outputs=enc_out, attention_mask=enc["attention_mask"],
                        decoder_input_ids=seq if past is None else seq[:, -1:], past_key_values=past,
                        use_cache=True, return_dict=True)
            past = out.past_key_values
            nxt = out.logits[:, -1, :].argmax(-1)
            nxt = torch.where(done, torch.full_like(nxt, cfg.pad_token_id), nxt)
            seq = torch.cat([seq, nxt[:, None]], 1)
            done |= nxt == cfg.eos_token_id
            if bool(done.all()):
                break
        return seq

    def run(pieces):
        batch = [f"{src} {tgt} " + " ".join(unicodedata.normalize("NFC", p).split()) for p in pieces]
        with lock, torch.inference_mode():
            enc = tok(batch, padding="longest", truncation=True, max_length=256, return_tensors="pt")
            # The answer can't be much longer than the question: a stuck translation stops early (was 50 s once)
            limit = min(256, 2 * enc["input_ids"].shape[1] + 16)
            out = None
            if fast["ok"]:
                try:
                    out = quick(enc, limit)
                except Exception as e:
                    fast["ok"] = False
                    print(f"   (fast translation off: {type(e).__name__}: {e})")
            if out is None:
                out = slow(enc, limit)
        out = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        return [re.sub(r"\s+([,.!?।:;])", r"\1", " ".join(o.split())) for o in out]

    # check the fast way once: it must give exactly the same words as the slow way, else it's never used
    sample = (["मुझे शाम 6 बजे मम्मी को फ़ोन करने की याद दिलाना", "आज मौसम कैसा है"] if src == "hin_Deva" else
              ["You have 2 tasks left today.", "In Hardoi it is 24 degrees right now, overcast."])
    try:
        a = run(sample)
        fast["ok"] = True
        t = time.perf_counter()
        b = run(sample)
        fast["ok"] = fast["ok"] and a == b          # (an error in the fast way already turned it off)
        print(f"   (fast translation {'on' if fast['ok'] else 'OFF: it gave different words'}"
              f"{f', {time.perf_counter() - t:.2f}s for the test' if fast['ok'] else ''})")
    except Exception as e:
        fast["ok"] = False
        print(f"   (fast translation off: {type(e).__name__}: {e})")
    return run


# ------------------------------------------------------------------ switching (with edit options)
TO_HINDI = re.compile(
    r"\b(?:(?:speak|talk|reply|answer|chat)\s+(?:to me\s+|with me\s+)?(?:in\s+)?(?:hindi|indian)"
    r"|switch (?:to|into) hindi|hindi mode(?: on)?|turn on hindi"
    r"|hindi (?:me|mein|main|mai|mei)\s+(?:baat|bolo|bol|baatein|baaten))\b", re.I)
TO_ENGLISH = re.compile(
    r"\b(?:(?:speak|talk|reply|answer|chat)\s+(?:to me\s+|with me\s+)?(?:in\s+)?english"
    r"|switch (?:back )?(?:to|into) english|hindi mode off|turn off hindi|stop (?:speaking )?hindi"
    r"|english (?:me|mein|main|mai|mei)\s+(?:baat|bolo|bol))\b", re.I)
HI_TO_ENGLISH = re.compile(r"(?:इंग्लिश|इंगलिश|अंग्रेज़ी|अंग्रेजी|अंग्रेज़ी)\s*(?:में|मे|मैं)|हिंदी\s*(?:मोड)?\s*बंद")
HI_MEETING_OFF = re.compile(r"(?:मीटिंग\w*|नीति)\s*(?:मोड|मौत|बोर्ड|मूड|मोर)\s*(?:ऑफ़?|बंद)")
HI_TO_HINDI = re.compile(r"हिंदी\s*(?:में|मे|मैं)\s*(?:बात|बोल)")


def command(text, memory):
    """'speak in Hindi' / 'speak English' (in English or Hindi). Returns what to say, or None."""
    low = text.lower().strip(" .!?")
    if TO_ENGLISH.search(low):
        _mode["wanted"] = False
        memory.set_setting("language", "en")
        screen_lang()
        return "Okay, I'll speak English now. To switch back, say: speak in Hindi."
    if TO_HINDI.search(low) or HI_TO_HINDI.search(text):
        if on():
            return "मैं पहले से हिंदी में बात कर रहा हूँ। अंग्रेज़ी के लिए कहिए: इंग्लिश में बात करो।"
        if not models_present():
            return SETUP_HELP
        _mode["wanted"] = True
        memory.set_setting("language", "hi")
        if ready():
            screen_lang()
            return "ठीक है, अब मैं हिंदी में बात करूँगा। वापस अंग्रेज़ी के लिए कहिए: इंग्लिश में बात करो।"
        _load_async()
        return ("Okay! Hindi is getting ready on this device, about 20 seconds. "
                "Until then I'll understand English. Then just speak Hindi.")
    return None


# ------------------------------------------------------------------ 1. hearing
def hear(audio):
    """Hindi speech -> Hindi text, with Vosk, on this device. audio: float32, 16 kHz, mono."""
    import numpy as np
    rec = __import__("vosk").KaldiRecognizer(_parts["vosk"], 16000)
    rec.SetWords(True)                                       # fix 102: how sure Vosk is about each word
    rec.AcceptWaveform((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    res = json.loads(rec.FinalResult())
    return unsure_start(res.get("result") or [], res.get("text", "")).strip()


def unsure_start(words, text):
    """fix 102: 'Jarvis' (or a noise) said first comes out as unsure words: 'चार बीस', 'लखनऊ चार वे'.
    Words at the very start (first 1.5 s) that Vosk is unsure about are dropped, if a real sentence follows."""
    if not words:
        return text
    k = 0
    while k < len(words) - 2 and words[k].get("conf", 1) < UNSURE and words[k].get("start", 9) < 1.5:
        k += 1
    if k:
        print(f"   (dropped unsure words at the start: {' '.join(w['word'] for w in words[:k])})")
    return " ".join(w["word"] for w in words[k:])


UNSURE = 0.6


# ------------------------------------------------------------------ 2. numbers (code, not AI)
HI_NUM = {"शून्य": 0, "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "पंच": 5, "छह": 6, "छः": 6,
          "छे": 6, "छै": 6, "छ": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13,
          "चौदह": 14, "पंद्रह": 15, "पन्द्रह": 15, "सोलह": 16, "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20,
          "पच्चीस": 25, "तीस": 30, "पैंतीस": 35, "चालीस": 40, "पैंतालीस": 45, "पचास": 50, "पचपन": 55, "साठ": 60,
          "सत्तर": 70, "अस्सी": 80, "नब्बे": 90, "सौ": 100}
UNITS = r"(?:बजे|बजा|बजकर|घंटे|घंटा|घंटों|मिनट|मिनिट|सेकंड|दिन|दिनों|हफ्ते|हफ़्ते|हफ्ता|महीने|महीना|साल|किलो|लीटर|बार|ग्राम|दर्जन)"
NOT_A_NUMBER_AFTER = {"ना", "न", "ही", "जी", "तो", "भी"}
VERB_BEFORE_DO = {"कर", "दे", "लगा", "जोड़", "जोड", "बता", "दिला", "हटा", "रख", "सुना", "भेज", "लिख", "बना", "पढ़",
                  "पढ", "मिटा", "चला", "बंद", "खोल", "सेट", "ऐड", "डाल", "बुला", "दिखा", "समझा", "उठा", "रोक"}


def numbers_to_digits(hi):
    """Hindi number words -> digits, so the translation keeps them exactly. Pure code, checked by tests."""
    s = hi.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    words = list(HI_NUM)

    def num(w):
        return str(HI_NUM[w]) if w in HI_NUM else (w if w.isdigit() else None)

    # "एक" also means "a", and "दो" also means "give/do": numbers only when a unit or a noun follows
    toks = s.split()
    out = []
    for i, w in enumerate(toks):
        core = w.strip(",.?!।")
        nxt = toks[i + 1].strip(",.?!।") if i + 1 < len(toks) else ""
        ends_clause = w != core and w.endswith((",", ".", "?", "!", "।"))
        if core == "एक" and not re.fullmatch(UNITS, nxt):
            out.append(w)
        elif core == "दो" and (not nxt or ends_clause or nxt in NOT_A_NUMBER_AFTER or not DEV.search(nxt)
                              or (i > 0 and toks[i - 1].strip(",.?!।") in VERB_BEFORE_DO)):
            out.append(w)
        elif core in HI_NUM:
            out.append(w.replace(core, str(HI_NUM[core])))
        else:
            out.append(w)
    s = " ".join(out)
    # clock and duration words
    s = re.sub(r"(\d+)\s+बजकर\s+(\d+)\s+(?:मिनट|मिनिट)", lambda m: f"{m[1]}:{int(m[2]):02d} बजे", s)
    s = re.sub(r"साढ़े\s+(\d+)\s+(बजे|बजा)", lambda m: f"{m[1]}:30 बजे", s)
    s = re.sub(r"सवा\s+(\d+)\s+(बजे|बजा)", lambda m: f"{m[1]}:15 बजे", s)
    s = re.sub(r"पौने\s+(\d+)\s+(बजे|बजा)", lambda m: f"{(int(m[1]) - 1) or 12}:45 बजे", s)
    s = re.sub(r"डेढ़\s+(बजे|बजा)", "1:30 बजे", s)
    s = re.sub(r"ढाई\s+(बजे|बजा)", "2:30 बजे", s)
    s = re.sub(r"साढ़े\s+(\d+)\s+(?:घंटे|घंटा)", lambda m: f"{int(m[1]) * 60 + 30} मिनट", s)
    s = re.sub(r"सवा\s+(?:घंटे|घंटा)", "75 मिनट", s)
    s = re.sub(r"डेढ़\s+(?:घंटे|घंटा)", "90 मिनट", s)
    s = re.sub(r"ढाई\s+(?:घंटे|घंटा)", "150 मिनट", s)
    s = re.sub(r"(?:आधे|आधा)\s+(?:घंटे|घंटा)", "30 मिनट", s)
    return s


EN_NUM = {"zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
          "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13", "fourteen": "14",
          "fifteen": "15", "sixteen": "16", "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20",
          "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100", "half": "30",
          "noon": "12", "midnight": "12"}


def _digits(s):
    s = s.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    found = set(re.findall(r"\d+", s))
    found |= {str(int(x)) for x in found}                        # "06" = "6"
    return found


def _numbers_in_english(en):
    low = en.lower()
    found = _digits(low)
    for w, d in EN_NUM.items():
        if re.search(rf"\b{w}\b", low):
            found.add(d)
    if re.search(r"\bquarter\b", low):
        found |= {"15", "45"}
    if re.search(r"\b(?:a|an|one) (?:hour|day|week|month|year|minute|kilo|kg|litre|liter|time)\b", low):
        found.add("1")
    return found


# words whose meaning must survive (a wrong day or part of the day is a wrong reminder)
MUST_KEEP = [
    ("कल", ("tomorrow", "yesterday")), ("परसों", ("day after tomorrow", "day before yesterday")),
    ("सुबह", ("morning", "am", "a.m")), ("शाम", ("evening", "pm", "p.m")),
    ("दोपहर", ("afternoon", "noon", "pm", "p.m")), ("रात", ("night", "pm", "p.m")),
    ("सोमवार", ("monday",)), ("मंगलवार", ("tuesday",)), ("बुधवार", ("wednesday",)), ("गुरुवार", ("thursday",)),
    ("शुक्रवार", ("friday",)), ("शनिवार", ("saturday",)), ("रविवार", ("sunday",)),
]


def check_english(hi, en):
    """The code check after translating Hindi -> English. Returns '' if all good, else what got lost."""
    lost = sorted(_digits(hi) - _numbers_in_english(en), key=lambda x: int(x))
    if lost:
        return "number " + ", ".join(lost)
    low = en.lower()
    for word, english in MUST_KEEP:
        if re.search(rf"(?<![ऀ-ॿ]){word}(?![ऀ-ॿ])", hi) and not any(e in low for e in english):
            return f"“{word}”"
    return ""


# ------------------------------------------------------------------ 3. Jarvis's own words (editable file)
DEFAULT_WORDS = """# Jarvis's own words in Hindi. Edit freely: one per line. Lines starting with # are notes.
#   हिंदी => english         a command at the END of a sentence: removed before translating, added after
#   बुकिश ~ आम बोलचाल        in Jarvis's replies: a bookish word -> the word people really say ("वर्षा ~ बारिश")
#                            ("पौधों को पानी देना टिक कर दो" -> "tick off watering the plants")
टिक कर दो => tick off
टिक करो => tick off
टिक कर दीजिए => tick off
टिक लगा दो => tick off
टिक मार दो => tick off
"""


def own_words():
    """(keep, end) from hindi_words.txt. The file is made the first time, so it can be edited."""
    try:
        if not WORDS_FILE.exists():
            WORDS_FILE.write_text(DEFAULT_WORDS, encoding="utf-8")
        lines = WORDS_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = DEFAULT_WORDS.splitlines()
    keep, end = [], []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "~" in line:
            continue
        if "=>" in line:
            a, b = line.split("=>", 1)
            end.append((a.strip(), b.strip()))
        elif "=" in line:
            a, b = line.split("=", 1)
            keep.append((a.strip(), b.strip()))
    keep.sort(key=lambda kv: -len(kv[0]))                   # longest first: "टू डू लिस्ट" before "टू डू"
    return keep, end


EN_FIX = [(r"^jarvis\s*[:,]\s*", ""), (r"\bp\.\s*m\b\.?", "p.m."), (r"\ba\.\s*m\b\.?", "a.m."),
          (r"\bto do list\b", "to-do list"), (r"^(?:it\s+)?reminds?\s+(?:you\s+|me\s+)?to\b", "Remind me to"),
          (r"\btimer of\b", "timer for"), (r"^remember to\b", "Remind me to"),
          (r"^(write|take|make)\s+(?:a\s+|down\s+a\s+)?note\s+(?![:,])", r"\1 a note: "),
          (r"^what (?:did|have) (?:i|we) (?:add|added|put)(?: to| in| on)? (?:my |the )?(\w+) list\b", r"what's on my \1 list")]
DONE_SAID = re.compile(r"\b(?:is|was|are|were|has been|have been|got)\s+(?:done|completed|finished|over)\b", re.I)


def _as_reminder(en):
    """'I have to go to the bank tomorrow at 5 pm' -> 'Remind me to go to the bank tomorrow at 5 pm'."""
    en = en.strip()
    m = re.match(r"^(?:I (?:have|need|want|ought|got) to|I've got to|I must|I should|remember to|don't forget to)\s+(.+)",
                 en, re.I)
    rest = m.group(1) if m else en[:1].lower() + en[1:]
    return "Remind me to " + rest


# "Jarvis" isn't a Hindi word, so Vosk writes what it sounds like (from the 29 Oct test): "चार बीस", "चार वे",
# "हज़ार बीस", "जार फेस", "जानवर" (in "शुक्रिया जानवर")... It's only you calling Jarvis: never a number or an item.
_UNIT_AFTER = rf"(?=\s+{UNITS}(?=\s|$|[,.?!।]))"
JARVIS_START = re.compile(
    r"^(?:(?:ओके|ठीक है|ठीक ही|हाँ|हां|अच्छा|सुनो|हेलो|हैलो)\s+)?"
    rf"(?:जार्विस|जारविस|जर्विस|जार\s*फेस|हज़ार\s*बीस|हजार\s*बीस|चार\s*(?:बीस|वे|के|इस|ही|वीस)(?!{_UNIT_AFTER})"
    rf"|चार(?!{_UNIT_AFTER})(?=\s)|चालीस(?!{_UNIT_AFTER})(?=\s)|चौबिस(?!{_UNIT_AFTER})(?=\s)|चौबीस(?!{_UNIT_AFTER})(?=\s))[,\s।]*")
JARVIS_AFTER = re.compile(r"(शुक्रिया|धन्यवाद|थैंक यू|ठीक है|ठीक ही|हाँ|हां|नहीं|ओके|अच्छा|बाय)\s+"
                          r"(?:जार्विस|जारविस|जर्विस|जानवर|चार\s*बीस|चार|चौबिस|चौबीस)(?=\s|$|[,.?!।])")
JARVIS_ANY = re.compile(r"\s*(?:जार्विस|जारविस|जर्विस)[,।]?\s*")


REQUEST = re.compile(r"दिला|बताओ|बताइए|बताना|सुनाओ|सुना|करो|कर दो|करना|जोड़|हटा|लगा|पढ़|लिखो|रहेगा|रहेंगे|होगी|होगा"
                     r"|कैसा|कैसी|कैसे|क्या|कितन|कब|कहाँ|कौन|ख़बर|खबर|मौसम|लिस्ट|रिमाइंड|टाइमर")


def strip_jarvis(hi):
    """'चार बीस आज का मौसम कैसा है' -> 'आज का मौसम कैसा है';  'नहीं जार्विस' -> 'नहीं'."""
    hi = hi.strip()
    keep_hi = re.match(r"^(ओके|ठीक है|ठीक ही|हाँ|हां|अच्छा)\s", hi)
    m = JARVIS_START.match(hi)
    if m and m.end() < len(hi):
        hi = ((keep_hi.group(1) + " ") if keep_hi and m.group(0).startswith(keep_hi.group(1)) else "") + hi[m.end():]
    hi = JARVIS_AFTER.sub(r"\1", hi)
    hi = JARVIS_ANY.sub(" ", hi)
    hi = " ".join(hi.split()).replace("ठीक ही", "ठीक है")
    # fix 104: "शुक्रिया, शाम छह बजे … याद दिलाना" became "Thank you for reminding me…" (not a reminder!).
    # A thank-you in front of a real request is dropped; on its own it stays ("शुक्रिया" -> "Thank you").
    m = re.match(r"^(?:शुक्रिया|धन्यवाद|थैंक\s*यू|थैंक्यू|थैंक्स)(?:\s+(?:बहुत\s+)+(?:शुक्रिया|धन्यवाद))?[,।!\s]+", hi)
    if m and len(hi[m.end():].split()) >= 3 and REQUEST.search(hi[m.end():]):
        hi = hi[m.end():]                                     # "शुक्रिया, मैं अब ठीक हूँ" keeps its thank-you
    return hi


# fix 109: "मेरे साथी का नाम अर्जुन है" became "My name is Arjun." (Jarvis then offered to call YOU Arjun!).
# Whose name it is decides where it's saved, so the code reads that part itself, not the translator.
RELATIONS = {"brother": "भाई भैया", "sister": "बहन बहिन दीदी", "mother": "माँ मां मम्मी माता माताजी अम्मी",
             "father": "पापा पिता पिताजी पापाजी अब्बू", "friend": "दोस्त मित्र सहेली फ्रेंड फ्रैंड",
             "colleague": "साथी सहकर्मी सहयोगी कलीग कुलीग कॉलीग कलिग कॉलिग", "husband": "पति", "wife": "पत्नी वाइफ",
             "son": "बेटा बेटे", "daughter": "बेटी", "boss": "बॉस", "teacher": "टीचर शिक्षक अध्यापक",
             "manager": "मैनेजर", "neighbour": "पड़ोसी पड़ोसन पडोसी", "grandmother": "दादी नानी",
             "grandfather": "दादा दादाजी नाना नानाजी", "uncle": "चाचा मामा ताऊ", "aunt": "चाची मामी बुआ मौसी ताई",
             "nephew": "भतीजा भतीजे भांजा भांजे", "niece": "भतीजी भांजी", "doctor": "डॉक्टर डाक्टर",
             "dog": "कुत्ता कुत्ते", "cat": "बिल्ली", "village": "गाँव गांव", "school": "स्कूल", "college": "कॉलेज",
             "company": "कंपनी", "office": "ऑफिस ऑफ़िस"}
REL_HI = {w: en for en, ws in RELATIONS.items() for w in ws.split()}
REL_HI["साथ ही"] = "colleague"                                # how Vosk hears "साथी"
WHOSE = re.compile(r"(?:^|\s)(?:मेरे|मेरी|मेरा)\s+(?:(?:बड़े|बड़ी|बडे|बडी|छोटे|छोटी)\s+)?(\S+(?:\s+ही)?)\s+(?:का|की|के)\s+नाम\s+\S")
WHOSE_UNSURE = "whose name"
PLACES = {"village", "school", "college", "company", "office"}


def whose_name(hi, en):
    """(english, problem): 'my X's name is Y' with X read from the Hindi by code."""
    m = WHOSE.search(hi)
    if not m:
        return en, ""
    rel = REL_HI.get(m.group(1)) or REL_HI.get(m.group(1).split()[0])
    if m.group(1).endswith(" ही") and m.group(1) not in REL_HI:
        rel = None
    if re.search(r"नाम\s+(?:क्या|बता|याद|पता)", hi) or en.rstrip().endswith("?"):      # a question, not a new name
        return (f"What is my {rel}'s name?", "") if rel else (en, "")
    name = re.search(r"\b(?:name (?:is|was)|is named|is called|is)\s+([^.!?]+?)[.!]*$", en, re.I)
    if not rel or not name:
        return en, WHOSE_UNSURE                            # never guess whose name it is: ask
    if rel in PLACES:                                       # not a person: just remember it
        return f"Remember that my {rel}'s name is {name.group(1).strip()}", ""
    return f"My {rel}'s name is {name.group(1).strip()}", ""


def to_english(hi):
    """Hindi text -> (English, problem). problem = '' when the code check passed."""
    hi = strip_jarvis(hi)
    hi = numbers_to_digits(hi)
    keep, end = own_words()
    tail = ""
    for phrase, english in end:
        m = re.search(rf"[,\s]*(?:(?:उसे|इसे|उसको|इसको|वो|ये|वह|यह)\s+)?{re.escape(phrase)}[\s.!।]*$", hi)
        if m:
            hi, tail = hi[:m.start()].strip(" ,"), english
            break
    if not hi:
        return (tail + " it" if tail else ""), ""
    en = _parts["to_en"]([hi])[0].strip()                 # (fix 98: no more <ID> markers: they made it slow)
    for pat, rep in EN_FIX:                                 # how the translation says things -> how Jarvis expects them
        en = re.sub(pat, rep, en, flags=re.I)
    if re.search(r"याद\s*दिला|रिमाइंड", hi) and not re.search(r"\bremind", en, re.I):
        en = _as_reminder(en)                               # fix 98: "...याद दिला देना" must stay a reminder
    if tail:                                                # you said "टिक कर दो": always "tick off X" (fix 98)
        thing = DONE_SAID.split(en)[0] if DONE_SAID.search(en) else en
        thing = re.sub(r"^(?:the )?(?:work|job|task) of\s+", "", thing.strip(" ,.!"), flags=re.I)
        en = f"{tail} {thing[:1].lower() + thing[1:]}".rstrip(" .!") if thing else f"{tail} it"
    en, unsure = whose_name(hi, en)                         # fix 109
    return en, unsure or check_english(hi, en)


# ------------------------------------------------------------------ 4. Hinglish typed in English letters
HINGLISH_MARK = {
    "hai", "hain", "hoga", "hogi", "karo", "kardo", "kar", "karna", "karni", "karke", "dena", "dijiye", "do", "mein",
    "mujhe", "mera", "meri", "mere", "kya", "kaise", "kaisa", "kitne", "kitna", "kal", "aaj", "baje", "shaam",
    "subah", "raat", "dopahar", "yaad", "dila", "dilana", "batao", "bata", "sunao", "padh", "padho", "likho",
    "jodo", "hatao", "baaki", "baki", "kaam", "nahi", "nahin", "haan", "theek", "thik", "bhi", "aur", "ko",
    "ka", "ki", "ke", "se", "wala", "wali", "abhi", "baarish", "barish", "mausam", "ghante", "jaana", "jana",
    "hua", "gaya", "gayi", "lagao", "laga", "lagado", "chahiye", "tha", "thi", "bahut", "accha", "acha",
}
STRONG = HINGLISH_MARK - {"do", "kal", "se", "ka", "ki", "ke", "ko", "aur", "bhi"}   # common in English too / tiny

ROMAN_WORDS = {
    # everyday Hinglish words (spelt the usual ways)
    "hai": "है", "hain": "हैं", "ho": "हो", "hoga": "होगा", "hogi": "होगी", "hua": "हुआ", "huaa": "हुआ",
    "gaya": "गया", "gayi": "गई", "gyi": "गई", "tha": "था", "thi": "थी", "mein": "में",
    "main": "मैं", "mai": "मैं", "mujhe": "मुझे", "mujhko": "मुझको", "mera": "मेरा", "meri": "मेरी",
    "mere": "मेरे", "kya": "क्या", "kyaa": "क्या", "kaise": "कैसे", "kaisa": "कैसा", "kaisi": "कैसी",
    "kitne": "कितने", "kitna": "कितना", "kitni": "कितनी", "kab": "कब", "kahan": "कहाँ", "kaun": "कौन",
    "kal": "कल", "aaj": "आज", "abhi": "अभी", "parso": "परसों", "parson": "परसों", "baje": "बजे", "baj": "बज",
    "shaam": "शाम", "sham": "शाम", "subah": "सुबह", "subha": "सुबह", "raat": "रात", "rat": "रात",
    "dopahar": "दोपहर", "dopehar": "दोपहर", "yaad": "याद", "yad": "याद", "dila": "दिला", "dilana": "दिलाना",
    "dilao": "दिलाओ", "dena": "देना", "do": "दो", "de": "दे", "dijiye": "दीजिए", "karo": "करो", "kar": "कर",
    "kardo": "कर दो", "karna": "करना", "karni": "करनी", "karke": "करके", "batao": "बताओ", "bata": "बता",
    "bataiye": "बताइए", "sunao": "सुनाओ", "suna": "सुना", "padh": "पढ़", "padho": "पढ़ो", "padhkar": "पढ़कर",
    "likho": "लिखो", "likh": "लिख", "jodo": "जोड़ो", "jod": "जोड़", "hatao": "हटाओ", "hata": "हटा",
    "baaki": "बाकी", "baki": "बाकी", "kaam": "काम", "nahi": "नहीं", "nahin": "नहीं", "na": "ना", "haan": "हाँ",
    "han": "हाँ", "theek": "ठीक", "thik": "ठीक", "bhi": "भी", "aur": "और", "ko": "को", "ka": "का", "ki": "की",
    "ke": "के", "se": "से", "par": "पर", "pe": "पे", "wala": "वाला", "wali": "वाली", "wale": "वाले",
    "baarish": "बारिश", "barish": "बारिश", "mausam": "मौसम", "ghante": "घंटे", "ghanta": "घंटा",
    "minute": "मिनट", "minat": "मिनट", "din": "दिन", "jaana": "जाना", "jana": "जाना", "jaa": "जा", "ja": "जा",
    "lagao": "लगाओ", "laga": "लगा", "lagado": "लगा दो", "chahiye": "चाहिए", "bahut": "बहुत", "accha": "अच्छा",
    "acha": "अच्छा", "achha": "अच्छा", "mummy": "मम्मी", "mumma": "मम्मी", "mammi": "मम्मी", "papa": "पापा",
    "behen": "बहन", "bahan": "बहन", "bhai": "भाई", "naam": "नाम", "nam": "नाम", "rakhna": "रखना", "rakho": "रखो",
    "khabar": "ख़बर", "khabrein": "ख़बरें", "taaza": "ताज़ा", "taza": "ताज़ा", "aata": "आटा", "atta": "आटा",
    "doodh": "दूध", "dudh": "दूध", "tamatar": "टमाटर", "pyaaz": "प्याज़", "pyaz": "प्याज़", "aloo": "आलू",
    "paani": "पानी", "pani": "पानी", "peena": "पीना", "pine": "पीने", "paudhon": "पौधों", "paudhe": "पौधे",
    "har": "हर", "thoda": "थोड़ा", "tanav": "तनाव", "pehle": "पहले", "baad": "बाद", "tak": "तक",
    "ek": "एक", "teen": "तीन", "char": "चार", "chaar": "चार", "paanch": "पाँच", "panch": "पाँच", "chhe": "छह",
    "chah": "छह", "saat": "सात", "aath": "आठ", "nau": "नौ", "das": "दस", "kilo": "किलो",
    # English words people mix in, written the way Vosk writes them (so the translation sees the same thing)
    "list": "लिस्ट", "to-do": "टू-डू", "todo": "टू-डू", "grocery": "ग्रोसरी", "remind": "रिमाइंड",
    "reminder": "रिमाइंडर", "add": "ऐड", "timer": "टाइमर", "task": "टास्क", "tasks": "टास्क", "note": "नोट",
    "notes": "नोट्स", "bank": "बैंक", "meeting": "मीटिंग", "phone": "फ़ोन", "call": "कॉल", "tick": "टिक",
    "delete": "डिलीट", "remove": "रिमूव", "jarvis": "जार्विस", "news": "न्यूज़", "weather": "वेदर",
    "budget": "बजट", "form": "फ़ॉर्म", "office": "ऑफ़िस", "plants": "प्लांट्स", "done": "डन",
}
_V = [("aa", "आ", "ा"), ("ee", "ई", "ी"), ("ii", "ई", "ी"), ("oo", "ऊ", "ू"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"),
      ("a", "अ", ""), ("i", "इ", "ि"), ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो")]
_C = [("chh", "छ"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"), ("ph", "फ"),
      ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("c", "क"), ("j", "ज"), ("t", "त"), ("d", "द"), ("n", "न"),
      ("p", "प"), ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"),
      ("h", "ह"), ("z", "ज़"), ("f", "फ़"), ("q", "क"), ("x", "क्स")]


def _spell(word):
    """Spelling rules for a word not in the list: 'jaldi' -> 'जल्दी'. Close enough for the translation AI."""
    out, i, prev_cons = "", 0, False
    while i < len(word):
        for rom, full, sign in _V:
            if word.startswith(rom, i):
                if rom in ("a", "i") and i == len(word) - 1 and prev_cons and len(word) > 2:
                    out += "ा" if rom == "a" else "ी"         # Hinglish: a final 'a'/'i' is long ("dena", "jaldi")
                else:
                    out += sign if prev_cons else full
                i += len(rom)
                prev_cons = False
                break
        else:
            for rom, dev in _C:
                if word.startswith(rom, i):
                    nxt_is_cons = i + len(rom) < len(word) and word[i + len(rom)] not in "aeiou"
                    if rom == "n" and prev_cons is False and out and nxt_is_cons:
                        out += "ं"                            # "ganda" -> गंदा
                    else:
                        out += ("्" if prev_cons else "") + dev
                    i += len(rom)
                    prev_cons = not (rom == "n" and out.endswith("ं"))
                    break
            else:
                out += word[i]
                i += 1
                prev_cons = False
    return out


def looks_hinglish(text):
    words = re.findall(r"[a-z]+(?:-[a-z]+)?", text.lower())
    strong = sum(w in STRONG for w in words)
    weak = sum(w in HINGLISH_MARK for w in words)
    return bool(words) and not DEV.search(text) and (strong >= 2 or (strong >= 1 and weak >= 2)
                                                    or (strong >= 1 and len(words) <= 3))


def roman_to_hindi(text):
    """'kal shaam 5 baje bank jaana hai' -> 'कल शाम 5 बजे बैंक जाना है' (offline: word list + spelling rules)."""
    def one(m):
        w = m.group(0)
        low = w.lower()
        return ROMAN_WORDS.get(low) or (w if low.isdigit() else _spell(low))
    return re.sub(r"[A-Za-z]+(?:-[A-Za-z]+)?", one, text)


# ------------------------------------------------------------------ what comes in
YES_NO = {"हाँ": "yes", "हां": "yes", "हाँ जी": "yes", "हां जी": "yes", "जी हाँ": "yes", "जी हां": "yes", "जी": "yes",
          "ठीक है": "yes", "हाँ ठीक है": "yes", "बिल्कुल": "yes", "नहीं": "no", "ना": "no", "नहीं जी": "no",
          "जी नहीं": "no", "रहने दो": "no", "मत करो": "no", "बस": "stop", "रुको": "stop", "बंद करो": "stop",
          "haan": "yes", "haa": "yes", "han": "yes", "nahi": "no", "nahin": "no"}


def understand(text):
    """Every turn: Hindi (spoken or typed) -> English for Jarvis. Returns {'text': english} or {'say': ...}.
    English stays exactly as it was."""
    LAST.update(heard="", english="")
    raw = (text or "").strip()
    if not raw:
        return {"text": ""}
    if HI_TO_ENGLISH.search(raw):
        LAST.update(heard=raw, english="speak in English")
        return {"text": "speak in English"}
    if HI_TO_HINDI.search(raw):
        LAST.update(heard=raw, english="speak in Hindi")
        return {"text": "speak in Hindi"}
    if HI_MEETING_OFF.search(raw):                         # Vosk hears "मोड" as "मौत", "बोर्ड"... (29 Oct test)
        LAST.update(heard=raw, english="meeting mode off")
        return {"text": "meeting mode off"}
    short = strip_jarvis(raw).strip(" ,.!?।") if DEV.search(raw) else raw.strip(" ,.!?।")
    if short in YES_NO and (DEV.search(short) or on()):   # the quick answers: no AI needed, never wrong
        LAST.update(heard=raw, english=YES_NO[short])
        return {"text": YES_NO[short]}
    if DEV.search(raw):
        hi = raw
    elif on() and looks_hinglish(raw):
        hi = roman_to_hindi(raw)
    else:
        return {"text": raw}
    if not ready():
        return {"say": "I heard Hindi, but Hindi isn't on yet. Say: speak in Hindi."
                if models_present() else SETUP_HELP}
    t = time.perf_counter()
    try:
        en, problem = to_english(hi)
    except Exception as e:
        print(f"   ⚠️ Hindi translation failed: {type(e).__name__}: {e}")
        return {"say": "माफ़ कीजिए, मैं यह समझ नहीं पाया। कृपया फिर से बोलिए।"}
    LAST.update(heard=hi, english=en)
    print(f"   🇮🇳 {hi}  ->  {en}   [{time.perf_counter() - t:.2f}s]" + (f"  ✗ lost: {problem}" if problem else ""))
    if problem == WHOSE_UNSURE:                             # fix 109
        return {"say": f"मैंने सुना: “{hi}”। यह किसका नाम है, मुझे पक्का समझ नहीं आया। "
                       "ऐसे कहिए: मेरे भाई का नाम अर्जुन है, या: मेरे सहकर्मी का नाम अर्जुन है।"}
    if problem:
        return {"say": f"माफ़ कीजिए, मुझे पक्का समझ नहीं आया। मैंने सुना: “{hi}”। कृपया फिर से बोलिए।"}
    return {"text": en}


def shown(text):
    """What to show for 'You:' this turn: the Hindi that was heard, with the English Jarvis understood."""
    return f"{LAST['heard']}  ({LAST['english']})" if LAST["heard"] else text


# ------------------------------------------------------------------ 5. replies
CONTRACTIONS = [(r"\bI'll\b", "I will"), (r"\bI'm\b", "I am"), (r"\bI've\b", "I have"), (r"\bI'd\b", "I would"),
                (r"\bdidn't\b", "did not"), (r"\bdon't\b", "do not"), (r"\bdoesn't\b", "does not"),
                (r"\bisn't\b", "is not"), (r"\baren't\b", "are not"), (r"\bwasn't\b", "was not"),
                (r"\bcan't\b", "cannot"), (r"\bwon't\b", "will not"), (r"\bcouldn't\b", "could not"),
                (r"\bit's\b", "it is"), (r"\bthat's\b", "that is"), (r"\bthere's\b", "there is"),
                (r"\bwhat's\b", "what is"), (r"\byou're\b", "you are"), (r"\byou've\b", "you have"),
                (r"\bhere's\b", "here is"), (r"\blet's\b", "let us"), (r"\bhaven't\b", "have not"),
                (r"^Want me to\b", "Should I"), (r"(?<=[.!?] )Want me to\b", "Should I"),
                (r"^(?:Hmm|Hm|Oh|Ah|Okay|OK|Ok|Sure|Alright|Got it)[,.!]\s+", "")]
SKIP = re.compile(r"https?://|\S+@\S+|^[\W\d_]+$")             # links, emails, only numbers: never translated
OK_WORDS = {"Okay", "OK", "Ok", "Sure", "Alright", "Got it", "Done", "Hmm"}
SMALL = {"Okay.": "ठीक है।", "Okay!": "ठीक है!", "Done.": "हो गया।", "Done!": "हो गया!", "Sure.": "ज़रूर।",
         "Yes.": "हाँ।", "No.": "नहीं।", "Got it.": "समझ गया।", "Hmm.": "हम्म।", "Sorry.": "माफ़ कीजिए।",
         # fix 104: Jarvis's small talk, said the way people say it ("Anytime!" was "किसी भी समय बड़ा")
         "You're welcome!": "आपका स्वागत है!", "Anytime!": "कभी भी!", "Happy to help!": "मदद करके ख़ुशी हुई!",
         "talk soon!": "फिर बात करेंगे!", "Talk soon!": "फिर बात करेंगे!",
         "Aww, that made my day!": "अरे वाह, आपने तो मेरा दिन बना दिया!",
         "Thank you, that means a lot!": "शुक्रिया, यह मेरे लिए बहुत मायने रखता है!",
         "That's so kind of you.": "आप बहुत अच्छे हैं।", "I am happy to help!": "मदद करके मुझे ख़ुशी होती है!",
         "How about you?": "और आप?", "How are you?": "आप कैसे हैं?"}


def _pieces(text):
    """A reply -> pieces to translate one by one: sentences, and each list item alone ('a; b; c').
    Returns [(prefix, piece, suffix)] so the reply can be put back together the same way."""
    out = []
    # a sentence ends at . ! ?  -- also after a number ("…the low is 22. Chance…"), but not a list's "1. "
    for sent in re.split(r"(?:(?<=[^\d\s][.!?])|(?<=[A-Za-z] \d[.!?])|(?<=[A-Za-z] \d\d[.!?]))\s+", text.strip()):
        if _form_for(sent):                                 # fix 102: Jarvis's own sentence, said naturally
            out.append(("", sent, " "))
            continue
        head, colon, tail = sent.partition(": ")
        if colon and tail:
            out.append(("", head + ".", ": "))
            items = re.split(r";\s+", tail)
            for k, item in enumerate(items):
                num = re.match(r"^(\d+\.\s*)", item)
                pre = num.group(1) if num else ""
                end = "; " if k < len(items) - 1 else " "
                out.append((pre, item[len(pre):], end))
        else:
            out.append(("", sent, " "))
    return out


def _check_hindi(en, hi):
    """The code check after translating a reply: every number kept, nothing empty, nothing cut short."""
    if not hi or not DEV.search(hi):
        return False
    if not _digits(en) <= _digits(hi):
        return False
    return len(hi) >= 0.35 * len(en)


# ---- fix 102: natural Hindi for Jarvis's most common replies (only the outputs: understanding is still the AI) ----
def _clock_hi(h, mins=0, day=""):
    """6 PM today -> 'आज शाम 6 बजे';  9:30 AM tomorrow -> 'कल सुबह 9:30 बजे'."""
    part = ("रात" if h < 4 or h >= 20 else "सुबह" if h < 12 else "दोपहर" if h < 16 else "शाम")
    h12 = h % 12 or 12
    day_hi = {"today": "आज", "tomorrow": "कल", "": ""}.get(day, "")
    if day.startswith("on "):
        day_hi = DAYS_HI.get(day[3:].strip().lower(), day[3:]) + " को"
    return f"{day_hi} {part} {h12}{f':{mins:02d}' if mins else ''} बजे".strip()


DAYS_HI = {"monday": "सोमवार", "tuesday": "मंगलवार", "wednesday": "बुधवार", "thursday": "गुरुवार",
           "friday": "शुक्रवार", "saturday": "शनिवार", "sunday": "रविवार"}
UNITS_HI = {"minute": "मिनट", "minutes": "मिनट", "hour": "घंटे", "hours": "घंटे", "second": "सेकंड",
            "seconds": "सेकंड", "day": "दिन", "days": "दिन"}


def when_hi(en):
    """English time words from Jarvis -> natural Hindi. None if it isn't one we know (then it's translated)."""
    en = en.strip(" .,")
    m = re.fullmatch(r"(?:at )?(\d{1,2})(?::(\d{2}))? ?(AM|PM)(?: (today|tomorrow|on \w+))?", en, re.I)
    if m:
        h = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
        return _clock_hi(h, int(m[2] or 0), (m[4] or "").lower())
    m = re.fullmatch(r"in (\d+) (minutes?|hours?|seconds?|days?)", en, re.I)
    if m:
        return f"{m[1]} {UNITS_HI[m[2].lower()]} में"
    if en.lower() in ("now", "right now"):
        return "अभी"
    return None


SKY_NOW = {"clear sky": "आसमान साफ़ है", "mainly clear": "आसमान ज़्यादातर साफ़ है", "partly cloudy": "हल्के बादल हैं",
           "overcast": "बादल छाए हैं", "fog": "कोहरा है", "light drizzle": "हल्की बूँदा-बाँदी हो रही है",
           "drizzle": "बूँदा-बाँदी हो रही है", "heavy drizzle": "तेज़ बूँदा-बाँदी हो रही है",
           "light rain": "हल्की बारिश हो रही है", "rain": "बारिश हो रही है", "heavy rain": "तेज़ बारिश हो रही है",
           "rain showers": "बौछारें पड़ रही हैं", "heavy rain showers": "तेज़ बौछारें पड़ रही हैं",
           "a thunderstorm": "आँधी-तूफ़ान है", "a thunderstorm with hail": "ओलों के साथ तूफ़ान है"}
SKY_NOW.update({"heavy rain showers": "तेज़ बौछारें पड़ रही हैं", "mixed conditions": "मौसम मिला-जुला है",
                "heavy drizzle": "तेज़ बूँदा-बाँदी हो रही है"})
SKY_LATER = {"clear sky": "आसमान साफ़ रहेगा", "mainly clear": "आसमान ज़्यादातर साफ़ रहेगा",
             "partly cloudy": "हल्के बादल रहेंगे", "overcast": "बादल छाए रहेंगे", "fog": "कोहरा रहेगा",
             "light drizzle": "हल्की बूँदा-बाँदी हो सकती है", "drizzle": "बूँदा-बाँदी हो सकती है",
             "light rain": "हल्की बारिश हो सकती है", "rain": "बारिश हो सकती है", "heavy rain": "तेज़ बारिश हो सकती है",
             "rain showers": "बौछारें पड़ सकती हैं", "a thunderstorm": "आँधी-तूफ़ान आ सकता है",
             "clear": "आसमान साफ़ रहेगा", "mostly cloudy": "ज़्यादातर बादल रहेंगे",
             "cloudy all night": "पूरी रात बादल रहेंगे", "heavy rain showers": "तेज़ बौछारें पड़ सकती हैं",
             "heavy drizzle": "तेज़ बूँदा-बाँदी हो सकती है", "a thunderstorm with hail": "ओलों के साथ तूफ़ान आ सकता है",
             "mixed conditions": "मौसम मिला-जुला रहेगा"}


def _city(label):
    return label.split(",")[0].strip()                     # "Hardoi, India" -> "Hardoi" (then translated)


def _f_now(m):
    city, temp, sky = m[1], m[2], m[3].lower()
    if sky not in SKY_NOW:                                   # fix 104: an unknown sky is translated on its own
        return [_city(city), sky], lambda t: f"{t[0]} में अभी {temp} डिग्री है, {t[1]}।"
    return [_city(city)], lambda t: f"{t[0]} में अभी {temp} डिग्री है, {SKY_NOW[sky]}।"


def _f_tomorrow(m):
    city, sky, hi_t, lo_t = m[1], m[2].lower(), m[3], m[4]
    if sky not in SKY_LATER:                                 # fix 104: an unknown sky is translated on its own
        return [_city(city), sky], lambda t: (f"कल {t[0]} में {t[1]}, ज़्यादा से ज़्यादा {hi_t} और "
                                              f"कम से कम {lo_t} डिग्री।")
    return [_city(city)], lambda t: (f"कल {t[0]} में {SKY_LATER[sky]}, ज़्यादा से ज़्यादा {hi_t} और "
                                     f"कम से कम {lo_t} डिग्री।")


def _f_tonight(m):
    city, sky, rain, a, b = m[1], m[2].lower(), m[3], m[4], m[5]
    if sky not in SKY_LATER:
        return None
    extra = ", और कुछ बारिश भी हो सकती है" if rain else ""
    return [_city(city)], lambda t: (f"आज रात {t[0]} में {SKY_LATER[sky]}{extra}, तापमान {a} से {b} डिग्री "
                                     f"के बीच रहेगा।")


def _f_reminder(m):
    task, when = m[1], when_hi(m[2])
    if not when:
        return None
    return [task], lambda t: f"ठीक है, मैं आपको याद दिलाऊँगा: {t[0]}, {when}।"


def _f_every(m):
    n, unit, task = m[1], UNITS_HI.get(m[2].lower(), m[2]), m[3]
    return [task], lambda t: f"ठीक है, मैं आपको हर {n} {unit} याद दिलाऊँगा: {t[0]}।"


def _f_first(m):
    when = when_hi(m[1])
    return ([], lambda t: f"पहला रिमाइंडर {when} है।") if when else None


def _f_letknow(m):
    when = when_hi(m[1])
    return ([], lambda t: f"{when} बता दूँगा।") if when else None


def _f_time(m):
    h = int(m[1]) % 12 + (12 if m[3].upper() == "PM" else 0)
    part = _clock_hi(h, 0).split()[0]
    return [], lambda t: f"अभी {part} के {int(m[1])}:{m[2]} बजे हैं।"


def _list_hi(name):
    return LIST_HI.get(name.lower().strip())


LIST_HI = {"grocery list": "ग्रोसरी लिस्ट", "shopping list": "शॉपिंग लिस्ट", "to-do list": "टू-डू लिस्ट",
           "schedule list": "शेड्यूल", "schedule": "शेड्यूल", "inbox list": "इनबॉक्स लिस्ट", "idea list": "आइडिया लिस्ट"}


def _f_list_has(m):
    name, items = m[1], m[2]
    ln = _list_hi(name)
    bits = [items] + ([] if ln else [name])
    return bits, lambda t: f"आपकी {ln or t[1]} में है: {t[0]}।"


def _f_added(m):
    item, name = m[1], m[2]
    ln = _list_hi(name)
    bits = [item] + ([] if ln else [name])
    return bits, lambda t: f"{ln or t[1]} में जोड़ दिया: {t[0]}।"


def _f_removed(m):
    item, name = m[1], m[2]
    ln = _list_hi(name)
    bits = [item] + ([] if ln else [name])
    return bits, lambda t: f"{ln or t[1]} से हटा दिया: {t[0]}।"


def _f_tasks_left(m):
    n = int(m[1])
    return [], lambda t: f"आज आपके {n} काम बाकी हैं।"


REPLY_FORMS = [                                          # Jarvis's own sentences, said the Hindi way (fix 98, 99, 102)
    (re.compile(r"^In (.+?) it'?s (\d+) degrees right now, (.+?)\.$", re.I), _f_now),
    (re.compile(r"^Today'?s high is (\d+) and the low is (\d+)\.$", re.I),
     lambda m: ([], lambda t: f"आज ज़्यादा से ज़्यादा {m[1]} और कम से कम {m[2]} डिग्री रहेगा।")),
    (re.compile(r"^Chance of rain is (\d+) percent\.$", re.I),
     lambda m: ([], lambda t: f"बारिश की संभावना {m[1]} परसेंट है।")),
    (re.compile(r"^Tomorrow in (.+?): (.+?), with a high of (\d+) and a low of (\d+) degrees\.$", re.I), _f_tomorrow),
    (re.compile(r"^Tonight in (.+?): (.+?)(, with some rain)?, between (\d+) and (\d+) degrees\.$", re.I), _f_tonight),
    (re.compile(r"^No rain expected( today)?\.$", re.I),
     lambda m: ([], lambda t: f"{'आज ' if m[1] else ''}बारिश की उम्मीद नहीं है।")),
    (re.compile(r"^(?:Okay, )?I'?ll remind you: (.+), ((?:at \d{1,2}(?::\d{2})? ?[AP]M(?: today| tomorrow| on \w+)?)|in \d+ \w+|now)\.$", re.I), _f_reminder),
    (re.compile(r"^(?:Okay, )?I'?ll remind you every (\d+) (minutes?|hours?|days?): (.+?)\.$", re.I), _f_every),
    (re.compile(r"^The first one is (.+?)\.$", re.I), _f_first),
    (re.compile(r"^I'?ll let you know (in \d+ \w+)\.$", re.I), _f_letknow),
    (re.compile(r"^It is (\d{1,2}):(\d{2}) ([AP]M)\.$", re.I), _f_time),
    (re.compile(r"^Timer set\.$", re.I), lambda m: ([], lambda t: "टाइमर लगा दिया।")),
    # fix 108: "Prepare me before things"
    (re.compile(r"^In (\d+) minutes?: (.+?)\.$", re.I), lambda m: ([m[2]], lambda t: f"{m[1]} मिनट बाद: {t[0]}।")),
    (re.compile(r'^Your note "(.+?)" says: (.+?)\.?$', re.I),
     lambda m: ([m[1], m[2]], lambda t: f"आपके नोट “{t[0]}” में लिखा है: {t[1]}।")),
    (re.compile(r"^Also on your to-do list: (.+?)\.$", re.I), "आपकी टू-डू लिस्ट में यह भी है: {0}।"),
    # fix 107: "What should I do first?"
    (re.compile(r"^Start with: (.+?)\.$", re.I), "पहले यह कीजिए: {0}।"),
    (re.compile(r"^Because it'?s due today(, and it'?s marked important)?\.$", re.I),
     lambda m: ([], lambda t: "क्योंकि यह आज ही करना है" + (", और ज़रूरी भी है।" if m[1] else "।"))),
    (re.compile(r"^Because it'?s marked important\.$", re.I), lambda m: ([], lambda t: "क्योंकि यह ज़रूरी है।")),
    (re.compile(r"^Because it'?s due tomorrow\.$", re.I), lambda m: ([], lambda t: "क्योंकि यह कल तक करना है।")),
    (re.compile(r"^Because it'?s the first one on today'?s list\.$", re.I),
     lambda m: ([], lambda t: "क्योंकि आज की लिस्ट में यह सबसे पहले है।")),
    (re.compile(r"^Say: next one, if you'?d rather do something else\.$", re.I),
     lambda m: ([], lambda t: "कुछ और करना हो, तो कहिए: अगला।")),
    (re.compile(r"^Your (.+? list) has: (.+?)\.$", re.I), _f_list_has),
    (re.compile(r"^You have (\d+) tasks? left today\.?$", re.I), _f_tasks_left),
    (re.compile(r"^(\d+) left\.$", re.I), lambda m: ([], lambda t: f"{m[1]} बाकी।")),
    (re.compile(r"^(\d+) (?:is|are) done\.$", re.I), lambda m: ([], lambda t: f"{m[1]} हो गए।")),
    (re.compile(r"^(?:I(?: have|'ve)? )?ticked off (.+?)[.!]?$", re.I), "मैंने टिक कर दिया: {0}।"),
    (re.compile(r"^(?:tick off|ticked off) (.+?)\?$", re.I), "{0} टिक कर दूँ?"),
    (re.compile(r"^(?:tick off|ticked off) (.+?)[.!]?$", re.I), "टिक कर दिया: {0}।"),
    # "Added milk to your grocery list." -> the item and the list each on their own, so neither gets lost
    (re.compile(r"^Added (.+?) to your (.+? list)[.!]?$", re.I), _f_added),
    (re.compile(r"^Removed (.+?) from your (.+? list)[.!]?$", re.I), _f_removed),
]


def _form_for(piece):
    """The natural form for this English sentence: (bits to translate, build) or None."""
    for rx, template in REPLY_FORMS:
        m = rx.match(piece.strip())
        if not m:
            continue
        if isinstance(template, str):
            return list(m.groups()), (lambda t, tpl=template: tpl.format(*t))
        got = template(m)
        if got:
            return got
    return None


# ---- fix 102: everyday words instead of bookish ones (the translation AI writes like a textbook) ----
EVERYDAY = [("किराने की सूची", "ग्रोसरी लिस्ट"), ("किराना सूची", "ग्रोसरी लिस्ट"), ("कार्य सूची", "टू-डू लिस्ट"),
            ("सूची", "लिस्ट"), ("वर्षा", "बारिश"), ("अद्यतन", "अपडेट"), ("क्षमा करें", "माफ़ कीजिए"),
            ("क्षमा कीजिए", "माफ़ कीजिए"), ("सहायता", "मदद"), ("प्रदान करने", "देने"), ("प्रदान करता", "देता"),
            ("प्रदान कर", "दे"), ("उच्च स्तर", "सबसे ज़्यादा तापमान"), ("निम्न स्तर", "सबसे कम तापमान"),
            ("अनुस्मारक", "रिमाइंडर"), ("ध्यान केंद्रित करना", "फोकस"), ("ध्यान केंद्रित", "फोकस"),
            ("वास्तविक समय", "रियल-टाइम"), ("प्रतिशत", "परसेंट"), ("दूरभाष", "फ़ोन"), ("सुनिश्चित करें", "पक्का करें"),
            ("प्रश्नों", "सवालों"), ("प्रश्न", "सवाल"), ("उत्तर", "जवाब"), ("आवश्यकता", "ज़रूरत"),
            ("कृपया इसे", "इसे"), ("अत्यधिक", "बहुत"), ("सम्पर्क", "संपर्क"), ("विद्युत", "बिजली"),
            # fix 104, from the 29 Oct evening test
            ("फोकस सत्र", "फोकस सेशन"), ("सत्र", "सेशन"), ("बैठकों", "मीटिंग्स"), ("बैठक", "मीटिंग"),
            ("समाचार", "ख़बरें"), ("तिथियों", "तारीख़ों"), ("तिथि", "तारीख़"), ("निर्धारित करता", "सेट करता"),
            ("निर्धारित कर", "सेट कर"), ("आपात स्थिति", "इमरजेंसी"), ("आपातकालीन", "इमरजेंसी"),
            ("शुभ संध्या", "नमस्ते"), ("शुभ प्रभात", "सुप्रभात"), ("कार्यों", "कामों")]


def everyday(hi):
    """Bookish Hindi -> the words people really say. Your own swaps: lines like  'वर्षा ~ बारिश'  in hindi_words.txt."""
    swaps = EVERYDAY + _own_swaps()
    for a, b in swaps:
        hi = re.sub(rf"(?<![\u0900-\u097F]){re.escape(a)}(?![\u0900-\u097F])", b, hi)
    return hi


def _own_swaps():
    try:
        lines = WORDS_FILE.read_text(encoding="utf-8").splitlines() if WORDS_FILE.exists() else []
    except OSError:
        return []
    return [tuple(x.strip() for x in l.split("~", 1)) for l in lines if "~" in l and not l.strip().startswith("#")]


_OLD_FORMS = [

]   # (the fix-98/99 forms now live in REPLY_FORMS above)


def to_hindi(text):
    """English reply -> Hindi, piece by piece, each piece checked. Text already in Hindi stays as it is."""
    if not text or DEV.search(text) or not ready():
        return text
    if text in _cache:
        return _cache[text]
    parts = _pieces(text)
    units, forms = [], []                                    # per piece: the bits to translate, and its form
    for pre, piece, end in parts:
        bits, form = [piece], None
        got = _form_for(piece)
        if got:
            bits, form = got                                 # only the things themselves are translated
        clean_bits = []
        for b in bits:
            for pat, rep in CONTRACTIONS:
                b = re.sub(pat, rep, b, flags=re.I if pat.startswith("^(?:Hmm") else 0)
            clean_bits.append(re.sub(r"[^\w\s.,!?;:'’\"“”()%₹°/+-]", "", b).strip())   # emojis: not words
        units.append(clean_bits)
        forms.append(form)
    flat = [b for bits in units for b in bits]
    need = [p for p in dict.fromkeys(flat) if p and p not in _cache and p not in SMALL and not SKIP.search(p)]
    if need:
        t = time.perf_counter()
        try:
            for p, h in zip(need, _parts["to_hi"](need)):
                _cache[p] = h if _check_hindi(p, h) else p          # failed the check: keep this piece in English
                if _cache[p] == p:
                    print(f"   ⚠️ kept in English (the Hindi lost something): {p}  ->  {h}")
        except Exception as e:
            print(f"   ⚠️ Hindi reply translation failed: {type(e).__name__}: {e}")
            return text
        print(f"   🇮🇳 reply in Hindi [{time.perf_counter() - t:.2f}s]")

    def hindi_of(b, original):
        return SMALL.get(b) or (_cache.get(b, original) if b and not SKIP.search(b) else original)
    out = ""
    for (pre, piece, end), bits, form in zip(parts, units, forms):
        if form:
            hi = form([hindi_of(b, b).rstrip("।.!? ") for b in bits])
        else:
            hi = hindi_of(bits[0], piece)
            if piece.rstrip().endswith("?") and not hi.rstrip().endswith("?"):
                hi = hi.rstrip("।. ") + "?"                        # a question stays a question
        if end in (": ", "; "):
            hi = hi.rstrip("।.!? ")                             # "…बचे हैं: पौधों को पानी दें; किराया दें।"
        out += pre + hi + end
    out = everyday(out.strip())                              # fix 102: the words people really say
    _cache[text] = out
    return out


# fix 102: how the Hindi voice sounds. Picked by listening: .venv\Scripts\python.exe hindi_voice_try.py
# speed: 1.0 = as now (bigger = slower);  expression: 1.0 = as now (bigger = livelier, more ups and downs)
VOICE_STYLE = {"speed": 0.92, "expression": 1.2}     # fix 103: style D, picked by listening


def speak(text, tone="calm"):
    """Say a reply in Hindi with the rohan voice (translated first, if it's English)."""
    import tts
    tts.VOICES["hindi"] = str(VOICE)
    old, old_tone = tts._current, tts.TONES.get(tone)
    tts._current = "hindi"
    try:
        if old_tone is not None and VOICE_STYLE != {"speed": 1.0, "expression": 1.0}:
            tts.TONES[tone] = tts.SynthesisConfig(
                length_scale=old_tone.length_scale * VOICE_STYLE["speed"],
                noise_scale=old_tone.noise_scale * VOICE_STYLE["expression"],
                noise_w_scale=old_tone.noise_w_scale * VOICE_STYLE["expression"])
        tts.speak(to_hindi(text), tone)
    finally:
        tts._current = old
        if old_tone is not None:
            tts.TONES[tone] = old_tone
