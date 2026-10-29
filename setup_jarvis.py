"""Get everything Jarvis needs, in one go, and check it.      .venv\\Scripts\\python.exe setup_jarvis.py
Run it once after installing the packages (see SETUP.md). Safe to run again: whatever is already there is skipped,
and a download that was cut off continues. At the end you get a ✅ / ❌ list of what's ready.

  .venv\\Scripts\\python.exe setup_jarvis.py            everything (English + Hindi)
  .venv\\Scripts\\python.exe setup_jarvis.py --check    only check, download nothing
  .venv\\Scripts\\python.exe setup_jarvis.py --no-hindi English only (Hindi can be added later)

What it gets (all from public places; nothing about you is sent anywhere):
  - English voices (Piper: ryan = male, lessac = female) ............ Hugging Face, rhasspy/piper-voices
  - Speech-to-text model (Whisper base.en) ........................... pywhispercpp downloads it
  - Wake word models ("Hey Jarvis") .................................. openwakeword downloads them
  - Word meanings for the dictionary (WordNet) ....................... nltk
  - Hindi: Vosk Hindi (hearing), rohan voice, IndicTrans2 x2 (translation)
           IndicTrans2 needs a free Hugging Face account: see SETUP.md, step 6
  - It also checks Ollama and its two models (qwen2.5:1.5b, all-minilm), and tells you the command if one is missing.
"""
import importlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
HERE = Path(__file__).resolve().parent
os.chdir(HERE)                                   # the voices must sit next to main.py
CHECK_ONLY = "--check" in sys.argv
HINDI = "--no-hindi" not in sys.argv
PIPER = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
MODELS_HI = HERE / "models_hi"
RESULTS = []                                      # (ok, what, how to fix)


def result(ok, what, fix=""):
    RESULTS.append((ok, what, fix))
    print(("✅ " if ok else "❌ ") + what + ("" if ok or not fix else f"\n     -> {fix}"))


def download(url, dest, name):
    """Download with a progress line and a 60-second 'no answer' limit. Ctrl+C skips just this file."""
    dest = Path(dest)
    if dest.exists() and dest.stat().st_size > 0:
        return True
    if CHECK_ONLY:
        return False
    print(f"⬇️  {name} ...  (Ctrl+C skips it; run this script again later and it continues)")
    socket.setdefaulttimeout(60)
    tmp = dest.with_name(dest.name + ".part")
    last = [0.0]

    def show(blocks, size, total):
        if time.time() - last[0] > 1:
            last[0] = time.time()
            pct = f"{min(100, 100 * blocks * size / total):.0f}%" if total > 0 else ""
            print(f"\r   {blocks * size / 1e6:.1f} MB {pct}   ", end="", flush=True)
    try:
        urllib.request.urlretrieve(url, tmp, reporthook=show)
        tmp.replace(dest)
        print()
        return True
    except KeyboardInterrupt:
        print(f"\n⏭  {name}: skipped")
    except Exception as e:
        print(f"\n   {name}: {type(e).__name__}: {e}")
    if tmp.exists():
        tmp.unlink()
    return False


# ------------------------------------------------------------------ 0. Python and packages
def check_python():
    v = sys.version_info
    result((3, 10) <= (v.major, v.minor) <= (3, 12), f"Python {v.major}.{v.minor}.{v.micro}",
           "Use Python 3.12 (64-bit). Newer versions may not have all packages yet.")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    result(in_venv, "running inside the .venv",
           "Run it as  .venv\\Scripts\\python.exe setup_jarvis.py  (not plain 'python')")
    needed = ["numpy", "requests", "sounddevice", "openwakeword", "pywhispercpp", "piper", "cryptography",
              "qrcode", "nltk", "onnxruntime"]
    if HINDI:
        needed += ["vosk", "torch", "transformers", "sentencepiece", "huggingface_hub"]
    missing = []
    for m in needed:
        try:
            importlib.import_module(m)
        except Exception:
            missing.append(m)
    result(not missing, "Python packages" + (f" (missing: {', '.join(missing)})" if missing else ""),
           ".venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    return not missing


# ------------------------------------------------------------------ 1. English
def english():
    for name, path in [("en_US-ryan-medium", "en/en_US/ryan/medium/"), ("en_US-lessac-medium", "en/en_US/lessac/medium/")]:
        ok = download(PIPER + path + name + ".onnx", HERE / f"{name}.onnx", f"English voice {name}") and \
            download(PIPER + path + name + ".onnx.json", HERE / f"{name}.onnx.json", f"{name} settings")
        result(ok, f"English voice {name}", "Run setup_jarvis.py again (internet needed once)")
    try:
        from pywhispercpp.model import Model
        if CHECK_ONLY:
            try:
                from pywhispercpp.constants import MODELS_DIR
                ok = (Path(MODELS_DIR) / "ggml-base.en.bin").exists()
            except ImportError:                         # an older pywhispercpp: just load it (downloads if needed)
                Model("base.en", print_progress=False, print_realtime=False)
                ok = True
        else:
            print("⬇️  Whisper base.en (about 150 MB, only the first time) ...")
            Model("base.en", print_progress=False, print_realtime=False)
            ok = True
        result(ok, "speech-to-text model (Whisper base.en)", "Run setup_jarvis.py again (internet needed once)")
    except Exception as e:
        result(False, f"speech-to-text model: {type(e).__name__}: {str(e)[:100]}", "Is pywhispercpp installed?")
    try:
        import openwakeword
        if not CHECK_ONLY:
            openwakeword.utils.download_models()
        from openwakeword.model import Model as Wake
        Wake(wakeword_models=["hey_jarvis"], inference_framework="onnx")
        result(True, "wake word model (Hey Jarvis)")
    except Exception as e:
        result(False, f"wake word model: {type(e).__name__}: {str(e)[:100]}", "Run setup_jarvis.py again")
    try:
        import nltk
        if not CHECK_ONLY:
            for pkg in ("wordnet", "omw-1.4"):
                nltk.download(pkg, quiet=True)
        from nltk.corpus import wordnet
        wordnet.synsets("happy")
        result(True, "dictionary words (WordNet)")
    except Exception as e:
        result(False, f"dictionary words: {type(e).__name__}", "Run setup_jarvis.py again (optional feature)")


# ------------------------------------------------------------------ 2. Hindi
def hindi():
    MODELS_HI.mkdir(exist_ok=True)
    vosk_dir = MODELS_HI / "vosk-model-small-hi-0.22"
    if not vosk_dir.exists():
        z = MODELS_HI / "vosk-small-hi.zip"
        if download("https://alphacephei.com/vosk/models/vosk-model-small-hi-0.22.zip", z, "Vosk Hindi (42 MB)"):
            zipfile.ZipFile(z).extractall(MODELS_HI)
            z.unlink()
    result(vosk_dir.exists(), "Hindi hearing (Vosk Hindi small)", "Run setup_jarvis.py again")
    base = PIPER + "hi/hi_IN/rohan/medium/hi_IN-rohan-medium"
    ok = download(base + ".onnx", MODELS_HI / "hi_IN-rohan-medium.onnx", "Hindi voice rohan (60 MB)") and \
        download(base + ".onnx.json", MODELS_HI / "hi_IN-rohan-medium.onnx.json", "Hindi voice settings")
    result(ok, "Hindi voice (rohan)", "Run setup_jarvis.py again")
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        result(False, "Hindi translation (IndicTrans2)", ".venv\\Scripts\\python.exe -m pip install -r requirements.txt")
        return
    for repo in ("ai4bharat/indictrans2-indic-en-dist-200M", "ai4bharat/indictrans2-en-indic-dist-200M"):
        try:
            if CHECK_ONLY:
                snapshot_download(repo, local_files_only=True)
            else:
                print(f"⬇️  {repo} (about 1 GB, only once; Ctrl+C and run again later if it's slow) ...")
                # the same model is stored twice (.safetensors and .bin): only one copy, half the download
                snapshot_download(repo, ignore_patterns=["pytorch_model.bin", "*.msgpack", "*.h5"])
            result(True, f"Hindi translation {repo.split('/')[1]}")
        except KeyboardInterrupt:
            result(False, f"Hindi translation {repo.split('/')[1]}: skipped", "Run setup_jarvis.py again")
        except Exception as e:
            kind = type(e).__name__
            fix = ("Open its Hugging Face page while logged in, click 'Agree and access repository', then log in here "
                   "(SETUP.md, step 6)") if ("Gated" in kind or "401" in str(e) or "403" in str(e)) else \
                "Run setup_jarvis.py again (internet needed once)"
            result(False, f"Hindi translation {repo.split('/')[1]} ({kind})", fix)


# ------------------------------------------------------------------ 3. Ollama (the on-device AI)
def ollama():
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            have = {m["name"] for m in json.load(r).get("models", [])}
    except Exception:
        result(False, "Ollama is running", "Install Ollama from ollama.com, open it, then run this script again")
        return
    result(True, "Ollama is running")
    for model in ("qwen2.5:1.5b", "all-minilm"):
        ok = any(h == model or h.startswith(model + ":") or h == model + ":latest" for h in have)
        if not ok and not CHECK_ONLY:
            print(f"⬇️  ollama pull {model} ...")
            try:
                ok = subprocess.run(["ollama", "pull", model]).returncode == 0
            except FileNotFoundError:
                ok = False
        result(ok, f"Ollama model {model}", f"In a terminal:  ollama pull {model}")


if __name__ == "__main__":
    print("Jarvis setup" + (" (checking only)" if CHECK_ONLY else "") + "\n")
    check_python()
    english()
    if HINDI:
        hindi()
    ollama()
    bad = [r for r in RESULTS if not r[0]]
    print("\n" + ("🎉 Everything is ready. Next: SETUP.md, step 8 (the tests)." if not bad else
                  f"⚠️ {len(bad)} thing(s) still missing (above, with what to do). Fix them and run this again."))
