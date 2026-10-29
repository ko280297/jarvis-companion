"""Checks for Jarvis's Hindi (fix 97).        Stop Jarvis first.

  .venv\\Scripts\\python.exe hindi_check.py          the code parts, no models needed (a few seconds)
  .venv\\Scripts\\python.exe hindi_check.py --real   the REAL models: your 20 recordings go through the whole
                                                    Hindi input (Vosk -> numbers -> IndicTrans2 -> check), then
                                                    10 typical replies are translated back. Writes hindi_check_report.txt
Nothing is sent online. Your recordings are only read.
"""
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import hindi as h

ok = bad = 0
REPORT = []


def out(line=""):
    print(line)
    REPORT.append(line)


def check(name, cond, got=""):
    global ok, bad
    ok, bad = ok + bool(cond), bad + (not cond)
    out(f"  {'✅' if cond else '❌'} {name}" + ("" if cond else f"\n       got: {got}"))


def code_checks():
    out("== Hindi numbers become digits (code, not AI)")
    for said, want in [("मुझे शाम छह बजे मम्मी को फ़ोन करने की याद दिलाना", "शाम 6 बजे"),
                       ("ग्रोसरी लिस्ट में दो किलो आटा और दूध जोड़ दो।", "2 किलो"),
                       ("ग्रोसरी लिस्ट में दो किलो आटा और दूध जोड़ दो।", "जोड़ दो।"),
                       ("हर दो घंटे में पानी पीने की याद दिलाना", "हर 2 घंटे"),
                       ("दस मिनट का टाइमर लगा दो", "10 मिनट का टाइमर लगा दो"),
                       ("एक नोट लिखो", "एक नोट"), ("एक घंटे में", "1 घंटे"),
                       ("याद दिला दो कल सुबह", "दिला दो कल"),
                       ("साढ़े छह बजे", "6:30 बजे"), ("सवा पांच बजे", "5:15 बजे"), ("पौने सात बजे", "6:45 बजे"),
                       ("डेढ़ बजे", "1:30 बजे"), ("डेढ़ घंटे बाद", "90 मिनट बाद"), ("आधे घंटे में", "30 मिनट"),
                       ("पांच बजकर बीस मिनट पर", "5:20 बजे"), ("कल सुबह नौ बजे", "सुबह 9 बजे"),
                       ("२ किलो", "2 किलो")]:
        got = h.numbers_to_digits(said)
        check(f"{said}  ->  …{want}…", want in got, got)

    out("\n== The check after translating to English")
    for hi, en, good in [("शाम 6 बजे", "Remind me at 6 in the evening", True),
                         ("शाम 6 बजे", "Remind me at six in the evening", True),
                         ("शाम 6 बजे", "Remind me at 6 in the morning", False),
                         ("कल सुबह 9 बजे", "Submit the form at 9 a.m.", False),
                         ("कल सुबह 9 बजे", "Submit the form tomorrow at 9 a.m.", True),
                         ("2 किलो आटा", "Add two kilograms of flour", True),
                         ("हर 2 घंटे", "Remind me every hour", False),
                         ("6:30 बजे", "at half past six", True),
                         ("शुक्रवार को", "on Friday", True), ("शुक्रवार को", "on Thursday", False)]:
        problem = h.check_english(hi, en)
        check(f"{hi} -> {en}: {'passes' if good else 'stopped'}", (problem == "") == good, problem)

    out("\n== Hinglish typed in English letters")
    for typed, want, is_hi in [("kal shaam 5 baje bank jaana hai, yaad dila dena", "कल शाम 5 बजे बैंक जाना है", True),
                               ("mera to-do list padh do", "मेरा टू-डू लिस्ट पढ़ दो", True),
                               ("grocery list mein do kilo aata add karo", "ग्रोसरी लिस्ट में दो किलो आटा ऐड करो", True),
                               ("aaj baarish hogi kya?", "आज बारिश होगी क्या?", True),
                               ("do I have a meeting today", "", False), ("remind me to call mom at 5", "", False),
                               ("what is on my to do list", "", False)]:
        hl = h.looks_hinglish(typed)
        check(f"“{typed}” is {'Hinglish' if is_hi else 'English (left alone)'}", hl == is_hi, hl)
        if is_hi:
            got = h.roman_to_hindi(typed)
            check(f"   -> {want}", want in got, got)

    out("\n== Switching (and the edit options)")

    class Mem(dict):
        def get_setting(self, k):
            return self.get(k)

        def set_setting(self, k, v):
            self[k] = v
    m = Mem()
    saved = (h.models_present, dict(h._parts))
    h.models_present = lambda: True
    h._parts.update(vosk=1, to_en=lambda xs: xs, to_hi=lambda xs: xs)
    check("“speak in Hindi” switches on", "हिंदी" in (h.command("speak in Hindi", m) or "") and h.on())
    check("…and is remembered for next time", m.get("language") == "hi")
    check("“Hindi mein baat karo” (Whisper's spelling) too", h.TO_HINDI.search("hindi mein baat karo") is not None)
    check("“इंग्लिश में बात करो” said in Hindi -> back to English",
          h.understand("इंग्लिश में बात करो")["text"] == "speak in English")
    check("“speak English” switches off", "English" in (h.command("speak English", m) or "") and not h.on())
    check("…and is remembered", m.get("language") == "en")
    check("a normal sentence is not a switch", h.command("what is water in hindi", m) is None)
    check("English stays exactly English", h.understand("what's on my to-do list")["text"] == "what's on my to-do list")
    h._mode["wanted"] = True
    check("“हाँ” is yes (no AI needed)", h.understand("हाँ")["text"] == "yes")
    check("“नहीं” is no", h.understand("नहीं।")["text"] == "no")

    out("\n== Jarvis's own words and the 'say it again' safety")
    fake_en = {"मेरी टू डू लिस्ट में क्या है?": "What is on my to do list?",
               "कल शाम 5 बजे बैंक जाना है रिमाइंड कर": "Remind me to go to the bank tomorrow at 5 p. m.",
               "पौधों को पानी देने वाला काम हो गया": "The work of watering the plants is done",
               "मुझे शाम 6 बजे मम्मी को फ़ोन करने की याद दिलाना": "Remind me to call mom at 6 in the morning",
               "मेरी बहन का नाम रिया है": "My sister's name is Riya",
               "पौधों को पानी देना": "Watering the plants",
               "मुझे दस मिनट का टाइमर": "?", "10 मिनट का टाइमर लगा": "Set a timer of 10 minutes",
               "हर 2 घंटे में पानी पीने की याद दिलाना": "Reminds you to drink water every 2 hours."}
    h._parts["to_en"] = lambda xs: [fake_en.get(x, "?") for x in xs]
    r = h.understand("मेरी टू डू लिस्ट में क्या है?")
    check("“to do list” -> “to-do list”", r.get("text") == "What is on my to-do list?", r)
    r = h.understand("कल शाम पांच बजे बैंक जाना है रिमाइंड कर")
    check("“5 p. m.” (with a space) still counts as evening", r.get("text", "").endswith("5 p.m."), r)
    r = h.understand("पौधों को पानी देने वाला काम हो गया, उसे टिक कर दो")
    check("“…काम हो गया, उसे टिक कर दो” -> “tick off watering the plants”",
          r.get("text") == "tick off watering the plants", r)
    r = h.understand("पौधों को पानी देना टिक कर दो")
    check("“पौधों को पानी देना टिक कर दो” -> “tick off watering the plants”",
          r.get("text") == "tick off watering the plants", r)
    r = h.understand("टिक कर दो")
    check("just “टिक कर दो” -> “tick off it”… (Jarvis then asks which one)", r.get("text") == "tick off it", r)
    r = h.understand("हर दो घंटे में पानी पीने की याद दिलाना")
    check("“Reminds you to…” (how the translation says it) -> “Remind me to…”",
          r.get("text") == "Remind me to drink water every 2 hours.", r)
    r = h.understand("मुझे शाम छह बजे मम्मी को फ़ोन करने की याद दिलाना")
    check("evening became morning -> Jarvis asks again, never guesses", "फिर से" in r.get("say", ""), r)
    h._parts["to_en"] = lambda xs: [{"कल शाम 5 बजे बैंक जाना है, याद दिला देना": "I have to go to the bank tomorrow at 5 pm.",
                                     "पौधों को पानी देने वाला काम हो गया है":
                                         "Watering of plants has been completed",
                                     "एक नोट लिखो कल मीटिंग में बजट की बात करनी है":
                                         "write a note tomorrow meeting to talk about the budget"}.get(x, "?")
                                    for x in xs]
    r = h.understand("कल शाम 5 बजे बैंक जाना है, याद दिला देना")
    check("“…याद दिला देना” stays a reminder (the translation had dropped it)",
          r.get("text") == "Remind me to go to the bank tomorrow at 5 pm.", r)
    r = h.understand("पौधों को पानी देने वाला काम हो गया है उसे टिक कर दो")
    check("“…हो गया है उसे टिक कर दो” -> “tick off watering of plants”", r.get("text") == "tick off watering of plants", r)
    r = h.understand("एक नोट लिखो कल मीटिंग में बजट की बात करनी है")
    check("“write a note …” -> “write a note: …”", r.get("text", "").startswith("write a note: tomorrow"), r)

    out("\n== “Jarvis” heard as other words (from the 29 Oct test): never a number or an item")
    for said, want in [("चार बीस आज का मौसम कैसा है", "आज का मौसम कैसा है"), ("चार वे अपने बारे में बताओ", "अपने बारे में बताओ"),
                       ("चालीस मेरे पैर में मोच लग गई है", "मेरे पैर में मोच लग गई है"),
                       ("जार फेस शक्कर को मेरी किराने की सूची में जोड़ें", "शक्कर को मेरी किराने की सूची में जोड़ें"),
                       ("ठीक ही चार धन्यवाद", "ठीक है धन्यवाद"), ("शुक्रिया जानवर मैं अब ठीक हूं", "शुक्रिया मैं अब ठीक हूं"),
                       ("नहीं जार्विस", "नहीं"), ("चार इस मीटिंग मोड ऑन", "मीटिंग मोड ऑन"),
                       ("चार बजे याद दिलाना", "चार बजे याद दिलाना"), ("चालीस मिनट का टाइमर", "चालीस मिनट का टाइमर")]:
        got = h.strip_jarvis(said)
        check(f"{said}  ->  {want}", got == want, got)
    check("“नहीं जार्विस” is no", h.understand("नहीं जार्विस").get("text") == "no")
    for said, want in [("शुक्रिया जार्विस शाम छः बजे मम्मी को फोन करने की याद दिलाना",
                        "शाम छः बजे मम्मी को फोन करने की याद दिलाना"),
                       ("शुक्रिया चौबिस आज रात मौसम कैसा रहेगा", "आज रात मौसम कैसा रहेगा"),
                       ("शुक्रिया जार्विस लखनऊ की ताजा खबरें", "लखनऊ की ताजा खबरें"),
                       ("शुक्रिया जार्विस", "शुक्रिया"), ("चौबीस घंटे में", "चौबीस घंटे में")]:
        got = h.strip_jarvis(said)
        check(f"(fix 104) {said}  ->  {want}", got == want, got)
    for said in ["मीटिंग मोड ऑफ", "चार में मीटिंग मौत बंद करो", "नीति मौत बंद", "मीटिंग बोर्ड ऑफ"]:
        check(f"“{said}” ends the meeting", h.understand(said).get("text") == "meeting mode off")
    check("“speak in Indian” (Whisper's mishearing of Hindi) switches too", h.TO_HINDI.search("jarvis, speak in indian"))
    h._parts["to_en"] = lambda xs: ["what did i add to my grocery list" for x in xs]
    r = h.understand("मेरी किराने की सूची में क्या जोड़ा")
    check("“what did I add to my grocery list” -> “what's on my grocery list”",
          r.get("text") == "what's on my grocery list", r)

    out("\n== Replies: piece by piece, each piece checked")
    h._cache.clear()
    fake_hi = {"You have 2 tasks left today.": "आज आपके पास 2 काम बचे हैं।", "water the plants": "पौधों को पानी दें",
               "pay rent.": "किराया दें।", "I will remind you.": "मैं आपको याद दिलाऊंगा।",
               "You have 3 notes about the trip.": "यात्रा के बारे में आपके नोट्स हैं।",   # lost the 3
               "call mom": "मम्मी को फ़ोन करना", "Hardoi": "हरदोई",
               "I am not sure about that one.": "मुझे उसके बारे में पक्का नहीं पता।",
               "Should I look it up online?": "क्या मैं इसे ऑनलाइन देखूँ?",
               "Should I set it?": "क्या मुझे इसे सेट करना चाहिए", "Did you mean.": "क्या आपका मतलब था?",
               "Say yes or no.": "हाँ या नहीं कहें।", "water the plants.": "पौधों को पानी दें।",
               "water the plants": "पौधों को पानी दें"}
    h._parts["to_hi"] = lambda xs: [fake_hi.get(x, "") for x in xs]
    got = h.to_hindi("You have 2 tasks left today: water the plants; pay rent.")
    check("a list keeps every item (no item dropped)", got == "आज आपके 2 काम बाकी हैं: पौधों को पानी दें; किराया दें।", got)
    got = h.to_hindi("You have 3 notes about the trip.")
    check("a piece that lost a number stays in English", got == "You have 3 notes about the trip.", got)
    got = h.to_hindi("Hmm, I'm not sure about that one. Want me to look it up online?")
    check("“Want me to…?” asked as “Should I…?” (the meaning can't flip)", "क्या मैं" in got, got)
    check("Hindi text stays as it is", h.to_hindi("ठीक है।") == "ठीक है।")
    _hi = h._parts["to_hi"]
    h._parts["to_hi"] = lambda xs: [{"milk": "दूध", "grocery list": "किराने की सूची"}.get(x, _hi([x])[0]) for x in xs]
    got = h.to_hindi("Added milk to your grocery list.")
    check("“Added milk to your grocery list” keeps the milk", got == "ग्रोसरी लिस्ट में जोड़ दिया: दूध।", got)

    out("\n== Natural Hindi for Jarvis's common replies (fix 102)")
    for en, want in [
            ("In Hardoi, India it's 24 degrees right now, overcast. Today's high is 30 and the low is 22. "
             "Chance of rain is 16 percent.",
             "हरदोई में अभी 24 डिग्री है, बादल छाए हैं। आज ज़्यादा से ज़्यादा 30 और कम से कम 22 डिग्री रहेगा। "
             "बारिश की संभावना 16 परसेंट है।"),
            ("Tonight in Hardoi, India: mostly cloudy, between 22 and 25 degrees. No rain expected.",
             "आज रात हरदोई में ज़्यादातर बादल रहेंगे, तापमान 22 से 25 डिग्री के बीच रहेगा। बारिश की उम्मीद नहीं है।"),
            ("Tomorrow in Hardoi, India: partly cloudy, with a high of 31 and a low of 21 degrees.",
             "कल हरदोई में हल्के बादल रहेंगे, ज़्यादा से ज़्यादा 31 और कम से कम 21 डिग्री।"),
            ("Okay, I'll remind you: call mom, at 6 PM today.", "ठीक है, मैं आपको याद दिलाऊँगा: मम्मी को फ़ोन करना, आज शाम 6 बजे।"),
            ("Okay, I'll remind you: call mom, at 9:30 AM tomorrow.",
             "ठीक है, मैं आपको याद दिलाऊँगा: मम्मी को फ़ोन करना, कल सुबह 9:30 बजे।"),
            ("Okay, I'll remind you: call mom, in 5 minutes.", "ठीक है, मैं आपको याद दिलाऊँगा: मम्मी को फ़ोन करना, 5 मिनट में।"),
            ("Timer set. I'll let you know in 3 minutes.", "टाइमर लगा दिया। 3 मिनट में बता दूँगा।"),
            ("It is 6:17 PM.", "अभी शाम के 6:17 बजे हैं।"), ("It is 9:05 AM.", "अभी सुबह के 9:05 बजे हैं।")]:
        got = h.to_hindi(en)
        check(f"{en[:60]}…", got == want, got)
    check("“Anytime!” -> “कभी भी!” (was “किसी भी समय बड़ा”)", h.to_hindi("Anytime!") == "कभी भी!", h.to_hindi("Anytime!"))
    check("“Okay, talk soon!” -> “ठीक है, फिर बात करेंगे!”…", "फिर बात करेंगे" in h.to_hindi("Okay, talk soon!"),
          h.to_hindi("Okay, talk soon!"))
    _hi2 = h._parts["to_hi"]
    h._parts["to_hi"] = lambda xs: [{"heavy rain showers": "x"}.get(x, _hi2([x])[0]) for x in xs]
    got = h.to_hindi("Tomorrow in Hardoi, India: overcast, with a high of 25 and a low of 21 degrees.")
    check("tomorrow's weather in natural Hindi", got == "कल हरदोई में बादल छाए रहेंगे, ज़्यादा से ज़्यादा 25 और कम से कम 21 डिग्री।", got)
    h._parts["to_hi"] = _hi2
    check("bookish -> everyday words", h.everyday("किराने की सूची में वर्षा का अद्यतन, क्षमा करें") ==
          "ग्रोसरी लिस्ट में बारिश का अपडेट, माफ़ कीजिए")
    check("…but a longer word isn't cut (कार्यक्रम stays)", h.everyday("कार्यक्रम") == "कार्यक्रम")
    words = [{"word": "लखनऊ", "conf": 0.4, "start": 0.2}, {"word": "चार", "conf": 0.5, "start": 0.6},
             {"word": "वे", "conf": 0.3, "start": 0.9}, {"word": "अपने", "conf": 0.95, "start": 1.2},
             {"word": "बारे", "conf": 1.0, "start": 1.5}, {"word": "में", "conf": 1.0, "start": 1.7},
             {"word": "बताओ", "conf": 1.0, "start": 2.0}]
    check("unsure words at the start are dropped (“लखनऊ चार वे अपने बारे में बताओ”)",
          h.unsure_start(words, "") == "अपने बारे में बताओ")
    check("…but a short answer is never emptied (“हाँ”)",
          h.unsure_start([{"word": "हाँ", "conf": 0.4, "start": 0.2}], "हाँ") == "हाँ")
    check("…and unsure words later in the sentence stay", h.unsure_start(
        [{"word": "आज", "conf": 1, "start": 0.2}, {"word": "मौसम", "conf": 0.4, "start": 0.6},
         {"word": "कैसा", "conf": 1, "start": 1}, {"word": "है", "conf": 1, "start": 1.3}], "") == "आज मौसम कैसा है")
    got = h.to_hindi("Should I set it?")
    check("a question stays a question (“?”)", got.endswith("?"), got)
    got = h.to_hindi("Done! I ticked off water the plants.")
    check("“I ticked off X” -> “मैंने टिक कर दिया: X” (not “I watered the plants”)",
          got == "हो गया! मैंने टिक कर दिया: पौधों को पानी दें।", got)
    got = h.to_hindi("I didn't change anything. Did you mean: tick off water the plants? Say yes or no.")
    check("“Did you mean: tick off X?” -> “…: X टिक कर दूँ?”", "क्या आपका मतलब था: पौधों को पानी दें टिक कर दूँ? हाँ" in got, got)
    h.models_present, _ = saved[0], h._parts.update(saved[1])
    h._mode["wanted"] = False


def real_checks():
    import numpy as np
    import wave
    out("Loading the Hindi models (Vosk, IndicTrans2 x2)…")
    h._load()
    if not h.ready():
        out(f"❌ could not load: {h._load_state['error']}\n   Run: .venv\\Scripts\\python.exe hindi_bench.py --setup")
        return
    out(f"   loaded in {h._load_state['secs']:.0f}s")
    h._mode["wanted"] = True
    samples = sorted((HERE / "hindi_samples").glob("*/sentences.txt"))
    for st in samples[:1]:
        out(f"\n== Your recordings ({st.parent.name}): voice -> Hindi text -> English -> the check")
        for line in st.read_text(encoding="utf-8").splitlines():
            name, _, ref = line.partition("\t")
            wav = st.parent / name
            if not wav.exists():
                continue
            with wave.open(str(wav)) as w:
                audio = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
            t = time.perf_counter()
            heard = h.hear(audio)
            t1 = time.perf_counter()
            r = h.understand(heard)
            t2 = time.perf_counter()
            out(f"  {name}  said:  {ref}\n         heard: {heard}   [{t1 - t:.2f}s]\n"
                f"         Jarvis gets: {r.get('text') or '(asks again) ' + r.get('say', '')}   [{t2 - t1:.2f}s]")
    out("\n== Typed Hinglish (phone)")
    for typed in ["kal shaam 5 baje bank jaana hai, yaad dila dena", "mera to-do list padh do",
                  "grocery list mein do kilo aata add karo", "aaj baarish hogi kya?", "pani peene ki yaad dilana"]:
        r = h.understand(typed)
        out(f"  {typed}\n     -> {h.LAST['heard']}\n     -> {r.get('text') or '(asks again)'}")
    out("\n== Replies in Hindi (each piece checked; ⚠️ = kept in English)")
    for en in ["Okay, I'll remind you: call mom, at 6 PM today.",
               "You have 2 tasks left today: water the plants; pay rent.",
               "In Hardoi it's 24 degrees right now, overcast. No rain expected today.",
               "Added to your grocery list: 2 kg flour and milk.",
               "I didn't change anything. Did you mean: tick off water the plants? Say yes or no.",
               "Hmm, I'm not sure about that one. Want me to look it up online?",
               "Should I set it?", "Done! I ticked off water the plants.",
               "Your sister is Riya. I'll remember that.", "Focus session started: 25 minutes. I'll tell you when it's time for a break."]:
        t = time.perf_counter()
        hi = h.to_hindi(en)
        out(f"  {en}\n     -> {hi}   [{time.perf_counter() - t:.2f}s]")
    (HERE / "hindi_check_report.txt").write_text("\n".join(REPORT), encoding="utf-8")
    out("\n📄 Saved hindi_check_report.txt. Send it to Claude.")


if __name__ == "__main__":
    if "--real" in sys.argv:
        real_checks()
    else:
        code_checks()
        out(f"\n{ok} passed, {bad} failed")
