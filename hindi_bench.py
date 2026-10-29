"""Hindi test bench: which options hear, understand and speak Hindi best and fastest, ON THIS DEVICE.
Nothing here changes Jarvis. Your recordings are only read, never copied anywhere, never sent online.

  Step 1 (once, needs internet):  .venv\\Scripts\\python.exe hindi_bench.py --setup
  Step 2 (offline after setup):   .venv\\Scripts\\python.exe hindi_bench.py Krati
  It reads hindi_samples\\Krati\\ (made by record_hindi.py) and writes hindi_bench_report.txt  -> send that file to Claude.

What it compares:
  1. HEARING Hindi (speech-to-text), on your own recordings:
       A) Whisper base (multilingual)     B) Whisper small (multilingual)
       C) Vosk Hindi small (42 MB)        D) Whisper small fine-tuned for Hindi (needs torch)
     -> how many characters it got right, and how long each sentence took
  2. UNDERSTANDING (Hindi -> English), on the correct sentences, so only the translation is tested:
       IndicTrans2 (AI4Bharat, 200M) vs the Qwen we already use (Ollama must be running)
     -> plus the CODE CHECK: did every number / time survive? (पाँच बजे must become 5)
     Hinglish typed in English letters ("kal shaam 5 baje ...") is tried with Qwen too.
  3. REPLYING (English -> Hindi) for 6 typical Jarvis answers, and the Hindi voice (rohan) speaks them so you can listen.
Every part is skipped (with a note) if its package or model isn't installed, so the rest still runs.
"""
import json
import os
import re
import sys
import time
import unicodedata
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models_hi"                 # all Hindi models live here (keep it out of GitHub)
VOSK_DIR = MODELS / "vosk-model-small-hi-0.22"
PIPER_HI = MODELS / "hi_IN-rohan-medium.onnx"
IT_HI_EN = "ai4bharat/indictrans2-indic-en-dist-200M"
IT_EN_HI = "ai4bharat/indictrans2-en-indic-dist-200M"
WHISPER_HI_FT = "vasista22/whisper-hindi-small"
OLLAMA = "http://127.0.0.1:11434/api/chat"
QWEN = "qwen2.5:1.5b"
REPORT = []


def say(line=""):
    print(line)
    REPORT.append(line)


# ---------------------------------------------------------------- setup (downloads, once)
def setup():
    import urllib.request
    import zipfile
    MODELS.mkdir(exist_ok=True)
    jobs = [
        ("Vosk Hindi small (42 MB)", "https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip",
         MODELS / "vosk-small-hi.zip", VOSK_DIR),
        ("Hindi voice rohan (about 60 MB)",
         "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx",
         PIPER_HI, PIPER_HI),
        ("Hindi voice settings",
         "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/rohan/medium/hi_IN-rohan-medium.onnx.json",
         Path(str(PIPER_HI) + ".json"), Path(str(PIPER_HI) + ".json")),
    ]
    for name, url, dest, done in jobs:
        if done.exists():
            print(f"✔  {name}: already there")
            continue
        print(f"⬇️  {name} ...  (Ctrl+C skips it; you can also download it in the browser, see the guide)")
        try:
            last = [0.0]

            def show(blocks, size, total):
                done_mb = blocks * size / 1e6
                if time.time() - last[0] > 1:
                    last[0] = time.time()
                    pct = f"{min(100, 100 * blocks * size / total):.0f}%" if total > 0 else ""
                    print(f"\r   {done_mb:.1f} MB {pct}   ", end="", flush=True)
            import socket
            socket.setdefaulttimeout(60)                          # no more silent waiting forever
            urllib.request.urlretrieve(url, dest, reporthook=show)
            print()
            if dest.suffix == ".zip":
                zipfile.ZipFile(dest).extractall(MODELS)
                dest.unlink()
            print(f"✅ {name}")
        except KeyboardInterrupt:
            print(f"\n⏭  {name}: skipped")
            if dest.exists() and dest != done:
                dest.unlink()
        except Exception as e:
            print(f"\n❌ {name}: {e}")
            if dest.exists() and dest.suffix == ".zip":
                dest.unlink()
    try:
        from pywhispercpp.model import Model
        for size in ("base", "small"):
            print(f"⬇️  Whisper {size} (multilingual) ...")
            Model(size, print_progress=False, print_realtime=False)
            print(f"✅ Whisper {size}")
    except Exception as e:
        print(f"❌ Whisper models: {e}")
    try:
        from huggingface_hub import snapshot_download
        for repo in (IT_HI_EN, IT_EN_HI):          # the 4th hearing model is dropped: Vosk already wins
            print(f"⬇️  {repo} ...  (if the internet is slow: Ctrl+C, run --setup again later, it continues)")
            try:
                # the same model is stored twice (.safetensors and .bin): fetch only one copy, half the download
                snapshot_download(repo, ignore_patterns=["pytorch_model.bin", "*.msgpack", "*.h5"])
                print(f"✅ {repo}")
            except Exception as e:
                print(f"❌ {repo}: {type(e).__name__}. For IndicTrans2: open its Hugging Face page, accept the terms, "
                      f"then run:  .venv\\Scripts\\huggingface-cli login")
    except ImportError:
        print("❌ huggingface_hub isn't installed (it comes with transformers). See the install line in the guide.")
    print("\nSetup finished. Now run:  .venv\\Scripts\\python.exe hindi_bench.py YourName")


# ---------------------------------------------------------------- helpers
def load_wav(path):
    with wave.open(str(path)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
    return data.astype(np.float32) / 32768.0, data.tobytes()


def norm_hi(s):
    """Compare fairly: no punctuation, no nukta / chandrabindu differences, one space."""
    s = unicodedata.normalize("NFC", s)
    s = s.replace("़", "").replace("ँ", "ं")          # nukta, chandrabindu -> anusvara
    s = re.sub(r"[।॥,.!?;:'\"()\[\]\-–—…]", " ", s)          # punctuation only (Hindi vowel signs must stay)
    return " ".join(s.lower().split())


def cer(ref, hyp):
    """Character error rate (0 = perfect)."""
    r, h = norm_hi(ref), norm_hi(hyp)
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i]
        for j, hc in enumerate(h, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc)))
        prev = cur
    return prev[-1] / len(r)


HI_NUM = {"एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पाँच": 5, "पांच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8,
          "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "पंद्रह": 15, "बीस": 20, "तीस": 30, "पैंतालीस": 45}
HI_KAAL = {"सुबह": "morning", "दोपहर": "afternoon", "शाम": "evening", "रात": "night"}
EN_NUM = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
          "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45}


def numbers_hi(s):
    s = s.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
    found = {int(n) for n in re.findall(r"\d+", s)}
    found |= {v for k, v in HI_NUM.items() if re.search(rf"(?<!\w){k}(?!\w)", s)}
    return found


def numbers_en(s):
    found = {int(n) for n in re.findall(r"\d+", s)}
    found |= {v for k, v in EN_NUM.items() if re.search(rf"\b{k}\b", s.lower())}
    return found


def code_check(hi, en):
    """The CODE CHECK Jarvis will do: every number and part of the day in the Hindi must be in the English."""
    lost = sorted(numbers_hi(hi) - numbers_en(en))
    lost_part = [e for h, e in HI_KAAL.items() if h in hi and e not in en.lower() and not
                 (e == "evening" and re.search(r"\bp\.?m\b", en.lower())) and not
                 (e == "morning" and re.search(r"\ba\.?m\b", en.lower()))]
    return ("✓ numbers & time kept" if not lost and not lost_part
            else "✗ LOST: " + ", ".join([str(x) for x in lost] + lost_part))


def timed(fn, *a):
    t = time.perf_counter()
    try:
        out = fn(*a)
    except Exception as e:                   # one failure is written in the report, it doesn't stop the bench
        out = f"⚠ ERROR {type(e).__name__}: {str(e)[:100]}"
    return out, time.perf_counter() - t


# ---------------------------------------------------------------- 1. hearing
def stt_whisper(size):
    from pywhispercpp.model import Model
    m = Model(size, print_progress=False, print_realtime=False, language="hi", translate=False)   # "hi", not "hindi": a wrong name made it guess Urdu
    return lambda audio, raw: " ".join(s.text.strip() for s in m.transcribe(audio))


def stt_vosk():
    import vosk
    vosk.SetLogLevel(-1)
    model = vosk.Model(str(VOSK_DIR))

    def run(audio, raw):
        rec = vosk.KaldiRecognizer(model, 16000)
        rec.AcceptWaveform(raw)
        return json.loads(rec.FinalResult()).get("text", "")
    return run


def stt_whisper_ft():
    from transformers import pipeline
    p = pipeline("automatic-speech-recognition", model=WHISPER_HI_FT, device="cpu")
    return lambda audio, raw: p({"raw": audio, "sampling_rate": 16000})["text"]   # Hindi-only model: no language setting


# ---------------------------------------------------------------- 2/3. translating
class SimpleProcessor:
    """What IndicProcessor does that matters most here: put the language tags in front of each sentence,
    normalise spaces before translating, and tidy spaces after."""
    def preprocess_batch(self, sentences, src_lang, tgt_lang, visualize=False):
        return [f"{src_lang} {tgt_lang} " + " ".join(unicodedata.normalize("NFC", s).split()) for s in sentences]

    def postprocess_batch(self, sentences, lang):
        return [re.sub(r"\s+([,.!?।])", r"\1", " ".join(s.split())) for s in sentences]


def make_indictrans(repo, src, tgt):
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    try:
        try:
            from IndicTransToolkit import IndicProcessor
        except ImportError:
            from IndicTransToolkit.IndicTransToolkit import IndicProcessor
        ip = IndicProcessor(inference=True)
    except ImportError:            # the toolkit doesn't build on Windows: a simple stand-in that adds the language tags
        ip = SimpleProcessor()
        say("      (IndicTransToolkit isn't installed, so a simple tag-only processor is used; "
            "results may be a little worse than on the Pi)")
    tok = AutoTokenizer.from_pretrained(repo, trust_remote_code=True)
    model = AutoModelForSeq2SeqLM.from_pretrained(repo, trust_remote_code=True)
    model.eval()

    def run(text):
        batch = ip.preprocess_batch([text], src_lang=src, tgt_lang=tgt, visualize=False)
        enc = tok(batch, padding="longest", truncation=True, max_length=256, return_tensors="pt")
        with torch.inference_mode():
            # use_cache=False: IndicTrans2's own code doesn't work with the newest transformers' memory cache
            out = model.generate(**enc, num_beams=1, max_length=256, use_cache=False)
        out = tok.batch_decode(out, skip_special_tokens=True, clean_up_tokenization_spaces=True)
        return ip.postprocess_batch(out, lang=tgt)[0]
    return run


def make_qwen(direction):
    import requests
    instr = ("Translate the user's Hindi or Hinglish sentence into simple English. Keep every number and time "
             "exactly. Reply with the English sentence only." if direction == "hi-en" else
             "Translate the user's English sentence into simple, natural spoken Hindi in Devanagari script. Keep every "
             "number and time exactly. Reply with the Hindi sentence only.")

    def run(text):
        r = requests.post(OLLAMA, json={"model": QWEN, "stream": False, "keep_alive": "24h",
                                        "options": {"temperature": 0, "num_predict": 80},
                                        "messages": [{"role": "system", "content": instr},
                                                     {"role": "user", "content": text}]}, timeout=60)
        r.raise_for_status()
        return r.json()["message"]["content"].strip().split("\n")[0]
    run("नमस्ते")                       # warm up (loads the model once)
    return run


def try_make(name, maker, *a):
    try:
        t = time.perf_counter()
        fn = maker(*a)
        say(f"   ✔ {name} loaded in {time.perf_counter() - t:.1f}s")
        return fn
    except Exception as e:
        say(f"   ⏭  {name} skipped: {type(e).__name__}: {str(e)[:120]}")
        return None


REPLIES = ["Okay, I'll remind you: call mom, at 6 PM today.",
           "You have 2 tasks left today: water the plants; pay rent.",
           "In Hardoi it's 24 degrees right now, overcast. No rain expected today.",
           "Added to your grocery list: 2 kg flour and milk.",
           "I didn't change anything. Did you mean: tick off water the plants? Say yes or no.",
           "Hmm, I'm not sure about that one. Want me to look it up online?"]
HINGLISH_TYPED = ["kal shaam 5 baje bank jaana hai, yaad dila dena",
                  "mera to-do list padh do",
                  "grocery list mein do kilo aata add karo",
                  "aaj baarish hogi kya?"]


def main():
    if "--setup" in sys.argv:
        return setup()
    who = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    folder = HERE / "hindi_samples" / (who or "")
    if not who or not (folder / "sentences.txt").exists():
        print("Usage: .venv\\Scripts\\python.exe hindi_bench.py YourName   (record first with record_hindi.py)")
        return
    pairs = []
    for line in (folder / "sentences.txt").read_text(encoding="utf-8").splitlines():
        name, _, ref = line.partition("\t")
        if (folder / name).exists():
            pairs.append((folder / name, ref))
    say("(bench version 4)")
    say(f"Hindi test bench, {time.strftime('%Y-%m-%d %H:%M')}, speaker: {who}, {len(pairs)} recordings")
    say(f"Computer: {sys.platform}, Python {sys.version.split()[0]}")

    # 1. HEARING
    say("\n========== 1. HEARING HINDI (speech-to-text) ==========")
    engines = [("A) Whisper base", stt_whisper, "base"), ("B) Whisper small", stt_whisper, "small"),
               ("C) Vosk Hindi small", stt_vosk)]
    results = {}
    if "--skip-hearing" in sys.argv:
        engines = []
        say("   (skipped with --skip-hearing)")
    for name, maker, *a in engines:
        fn = try_make(name, maker, *a)
        if not fn:
            continue
        rows = []
        for path, ref in pairs:
            audio, raw = load_wav(path)
            try:
                hyp, secs = timed(fn, audio, raw)
            except Exception as e:                  # one engine failing must not stop the whole bench
                say(f"   ⏭  {name} stopped: {type(e).__name__}: {str(e)[:120]}")
                rows = []
                break
            hindi = bool(re.search(r"[ऀ-ॿ]", ref))
            rows.append((path.name, ref, hyp, cer(ref, hyp) if hindi else None, secs))
        if not rows:
            continue
        scored = [r for r in rows if r[3] is not None]
        avg = sum(r[3] for r in scored) / max(1, len(scored))
        speed = sum(r[4] for r in rows) / max(1, len(rows))
        results[name] = (avg, speed, rows)
        say(f"\n--- {name}: {100 * (1 - avg):.0f}% of characters right (Hindi sentences), {speed:.2f}s per sentence")
        for fname, ref, hyp, c, secs in rows:
            mark = "  " if c is None else ("✅" if c < 0.15 else "🟡" if c < 0.35 else "❌")
            say(f"   {mark} {fname}  heard: {hyp}")
            if c is not None and c >= 0.15:
                say(f"        said: {ref}")
    if results:
        best = min(results, key=lambda k: results[k][0])
        say(f"\n>> Best hearing: {best} ({100 * (1 - results[best][0]):.0f}% right, {results[best][1]:.2f}s each)")

    # 2. UNDERSTANDING
    say("\n========== 2. UNDERSTANDING (Hindi -> English) + the code check ==========")
    hi_sents = [ref for _, ref in pairs if re.search(r"[ऀ-ॿ]", ref)]
    tr = [("IndicTrans2", try_make("IndicTrans2 Hindi->English", make_indictrans, IT_HI_EN, "hin_Deva", "eng_Latn")),
          ("Qwen", try_make("Qwen Hindi->English (Ollama)", make_qwen, "hi-en"))]
    for name, fn in tr:
        if not fn:
            continue
        say(f"\n--- {name}")
        total, ok = 0.0, 0
        for s in hi_sents:
            out, secs = timed(fn, s)
            total += secs
            chk = code_check(s, out)
            ok += chk.startswith("✓")
            say(f"   {s}\n      -> {out}   [{secs:.2f}s]  {chk}")
        say(f">> {name}: numbers kept in {ok}/{len(hi_sents)}, {total / max(1, len(hi_sents)):.2f}s per sentence")
        if name == "Qwen":
            say("\n--- Qwen with Hinglish typed in English letters (phone typing)")
            for s in HINGLISH_TYPED:
                out, secs = timed(fn, s)
                say(f"   {s}\n      -> {out}   [{secs:.2f}s]")

    # 3. REPLYING
    say("\n========== 3. REPLYING (English -> Hindi) + the Hindi voice ==========")
    back = [("IndicTrans2", try_make("IndicTrans2 English->Hindi", make_indictrans, IT_EN_HI, "eng_Latn", "hin_Deva")),
            ("Qwen", try_make("Qwen English->Hindi (Ollama)", make_qwen, "en-hi"))]
    spoken = []
    for name, fn in back:
        if not fn:
            continue
        say(f"\n--- {name}")
        for s in REPLIES:
            out, secs = timed(fn, s)
            lost = sorted(numbers_en(s) - numbers_hi(out))
            say(f"   {s}\n      -> {out}   [{secs:.2f}s]  " + ("✓ numbers kept" if not lost else f"✗ LOST: {lost}"))
            if name == "IndicTrans2" or not spoken:
                spoken.append(out)
    if PIPER_HI.exists() and spoken:
        try:
            import sounddevice as sd
            from piper import PiperVoice
            voice = PiperVoice.load(str(PIPER_HI))
            say("\n--- The Hindi voice (rohan) is speaking 3 replies now. Listen!")
            for s in spoken[:3]:
                t = time.perf_counter()
                chunks = list(voice.synthesize(s))
                audio = np.concatenate([c.audio_int16_array for c in chunks])
                say(f"   🔊 {s}   [voice made in {time.perf_counter() - t:.2f}s]")
                sd.play(audio, chunks[0].sample_rate)
                sd.wait()
        except Exception as e:
            say(f"   ⏭  voice skipped: {type(e).__name__}: {str(e)[:120]}")
    else:
        say("   ⏭  voice skipped: run --setup first")

    (HERE / "hindi_bench_report.txt").write_text("\n".join(REPORT), encoding="utf-8")
    print("\n📄 Saved hindi_bench_report.txt. Send it to Claude.")


if __name__ == "__main__":
    main()