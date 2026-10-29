# Jarvis: setup guide (Windows laptop)

Jarvis is a private voice companion. Everything runs on your own computer: hearing, thinking, speaking,
Hindi. Only the weather and the news go online, and Jarvis shows exactly what it sent.

**Time:** about 45 to 60 minutes, mostly waiting for downloads (about 3.5 GB in total, with Hindi).
**Internet:** needed once, for the setup. After that Jarvis works offline, except weather and news.

Follow the steps in order. Every command is typed in the **Command Prompt** (press Windows key, type `cmd`, Enter),
inside the Jarvis folder.

> **The one rule that avoids most errors:** always start Python as `.venv\Scripts\python.exe`, never plain `python`.
> (`.venv` starts with a dot.)

---

## What you need

- Windows 10 or 11, 64-bit, at least 8 GB RAM (16 GB is smoother), about 6 GB free disk space
- A microphone and a speaker (a laptop's own are fine)
- These three programs (step 1): **Python 3.12**, **Git**, **Ollama**

---

## Step 1: Install the three programs (once)

1. **Python 3.12 (64-bit)** from python.org/downloads (choose a 3.12.x version).
   On the first screen of the installer, tick **"Add python.exe to PATH"**, then "Install Now".
2. **Git** from git-scm.com: "Next" on every screen is fine.
3. **Ollama** from ollama.com: install it and open it once. (It runs the AI on your computer.)

Check: close and reopen Command Prompt, then type `py -3.12 --version`. It should say `Python 3.12.x`.

## Step 2: Get the code

```
cd %USERPROFILE%\Desktop
git clone <the repository link Krati shares>
cd companion
```
(If the folder has another name, `cd` into that name instead.)

## Step 3: Make Jarvis's own Python folder (.venv)

```
py -3.12 -m venv .venv
```
This makes a folder called `.venv` (with the dot). Everything Jarvis installs goes in there, not into your Windows.

## Step 4: Install the Python packages

```
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
```
`requirements-lock.txt` has the exact versions Krati tested with. If it isn't there, or it fails, use:
```
.venv\Scripts\python.exe -m pip install -r requirements.txt
```
This takes 5 to 15 minutes (torch is big). Red "WARNING" lines are fine; a red "ERROR" at the end is not (see Problems).

## Step 5: The AI models in Ollama

Make sure the Ollama app is open (llama icon near the clock), then:
```
ollama pull qwen2.5:1.5b
ollama pull all-minilm
```
(About 1 GB. If you skip this, step 7 does it for you.)

## Step 6: Hindi access (a free Hugging Face account, once)

Jarvis's Hindi translator (IndicTrans2, by AI4Bharat at IIT Madras) is free, but you must accept its terms once.
Skip this step if you don't need Hindi, and use `--no-hindi` in step 7.

1. Make a free account at huggingface.co and log in.
2. Open these two pages, and on each click **"Agree and access repository"**:
   - huggingface.co/ai4bharat/indictrans2-indic-en-dist-200M
   - huggingface.co/ai4bharat/indictrans2-en-indic-dist-200M
3. Click your picture (top right) → **Settings** → **Access Tokens** → **Create new token** → type **Read** → Create.
   Copy the token (it starts with `hf_`).
4. In Command Prompt:
   ```
   .venv\Scripts\hf auth login --token PASTE_YOUR_TOKEN_HERE
   ```
   (If that says "not recognized", use `.venv\Scripts\huggingface-cli login --token PASTE_YOUR_TOKEN_HERE`.)
   If it asks "Add token as git credential?", type **n**.

> **Never share your token** (not in chat, not in screenshots) and never put it in a file in this folder.
> If it leaks: delete it on the same Access Tokens page and make a new one.

## Step 7: Download Jarvis's voices and models (one command)

```
.venv\Scripts\python.exe setup_jarvis.py
```
It downloads the English voices, the speech-to-text model, the "Hey Jarvis" wake word, and the Hindi models,
and checks Ollama. At the end you get a ✅ / ❌ list. Every ❌ says what to do.

- Slow internet? Press **Ctrl+C** and run the same command again later: it continues where it stopped.
- English only: `.venv\Scripts\python.exe setup_jarvis.py --no-hindi`
- Just check, download nothing: `.venv\Scripts\python.exe setup_jarvis.py --check`

When it says **"🎉 Everything is ready"**, go on.

## Step 8: Test it (about 5 minutes)

Jarvis must **not** be running during the tests. The tests never change your real memory.

| Command | You should see |
|---|---|
| `.venv\Scripts\python.exe text_mode.py --fast < scenarios.txt` | `SCORE: 506 / 506 passed` (the last lines) |
| `.venv\Scripts\python.exe hindi_check.py` | `105 passed, 0 failed` |
| `.venv\Scripts\python.exe hear_check.py` | `53 passed, 0 failed` |
| `.venv\Scripts\python.exe remind_check.py` | `33 passed, 0 failed` |
| `.venv\Scripts\python.exe phone_check.py` | `17 passed, 0 failed` |

- The big test (`text_mode.py`) needs Ollama open and takes a few minutes. Its weather and news lines need the
  internet; without internet they say "can't reach the internet" and still pass.
- If a number is different, send the last 40 lines of that test to Krati.
- Optional, the real Hindi models: `.venv\Scripts\python.exe hindi_check.py --real`
  (writes `hindi_check_report.txt`).

## Step 9: Run Jarvis

```
.venv\Scripts\python.exe main.py
```
- Wait for **"Ready!"**. Jarvis's face: open **http://localhost:8000** in the browser (press F11 for full screen).
- Say **"Hey Jarvis"**, wait for the sound, then speak. A few good first sentences:
  - "My name is …" and "My city is …" (for the weather)
  - "What's the weather?" · "Remind me in 5 minutes to drink water" · "Add milk to my grocery list"
  - "What can you do?"
- **Hindi:** say "Speak in Hindi". The first time it needs 20 to 45 seconds to load, then Jarvis does a namaste
  and a "हिंदी" badge appears. Then just talk in Hindi. Back to English: "Speak English" / "इंग्लिश में बात करो".
- **Typing instead of speaking:** `.venv\Scripts\python.exe text_mode.py`
- **Stop Jarvis:** Ctrl+C in the Command Prompt.

Everything you tell Jarvis is saved encrypted (AES-256) on your own computer, with a key that only works on that
computer. Each person starts with an empty memory.

---

## Problems and what to do

| You see | Do this |
|---|---|
| `'python' is not recognized` | Use `.venv\Scripts\python.exe …` (with the dot), from inside the Jarvis folder |
| `The system cannot find the path specified` with `venv\Scripts…` | The folder is `.venv` with a dot: `.venv\Scripts\python.exe` |
| `ModuleNotFoundError: No module named '…'` | Step 4 didn't finish: run it again, then step 7 |
| `IndicTransToolkit` fails to build | Not needed. Don't install it. |
| `GatedRepoError` in step 7 | Step 6.2: click "Agree and access repository" on both pages (logged in), then step 7 again |
| `Invalid user token` | The token was copied wrongly. Make a new Read token and log in again (step 6.4) |
| `symlinks … not supported` warning | Harmless on Windows. Ignore it. |
| A download stays at the same % for minutes | Ctrl+C, then run step 7 again (it continues) |
| Ollama ❌ in step 7 | Open the Ollama app (it must be running), then step 7 again |
| Jarvis doesn't hear you | Windows Settings → Privacy → Microphone: allow desktop apps. Check the right mic is the default. |
| The face screen looks old | Press F5 once on http://localhost:8000 |
| Hindi: "I heard Hindi, but Hindi isn't on yet" | Say "Speak in Hindi" first, and wait for the namaste |

---

## For Krati: putting it on GitHub safely

Private files must never go to GitHub. `.gitignore` already keeps these out:
your memory (`memories.db*`, `.jarvis_key`), the online log, timing logs, your voice recordings (`hindi_samples/`),
the big model files (`models_hi/`, `*.onnx`), test backups, and the `.venv` folder.

1. Save the exact package versions (so teammates get the same ones):
   ```
   .venv\Scripts\python.exe -m pip freeze > requirements-lock.txt
   ```
2. Check what would be uploaded:
   ```
   git status
   ```
   **None** of these may appear in the list: `memories.db`, `memories.db.enc`, `memories.db.unencrypted-backup`,
   `online_log.jsonl`, `jarvis_timing.csv`, `jarvis_run…log`, `.jarvis_key`, `hindi_samples`, `models_hi`,
   any `.onnx` file, `hindi_bench_report.txt`, `hindi_check_report.txt`, `hindi_words.txt`, `.venv`.
   If one does appear (it was uploaded before `.gitignore` knew it), stop it being tracked (this does NOT delete
   your file):
   ```
   git rm --cached NAME_OF_THE_FILE
   ```
3. Upload:
   ```
   git add -A
   git commit -m "Jarvis with Hindi, news for India and your city, tonight's weather"
   git push
   ```
4. Share the repository link with the team, and point them to this file (SETUP.md).
