"""Hindi voice samples for testing Jarvis's Hindi (team kit).   python record_hindi.py YourName

Shows 20 sentences one by one. For each: press Enter, speak normally (6 seconds), and it's saved.
  r + Enter  = record that sentence again      s + Enter = skip it      q + Enter = stop (saved ones stay)
Saved in:  hindi_samples/<YourName>/01.wav ... 20.wav  plus  sentences.txt (what each file should say)
Format: 16 kHz, mono, 16-bit WAV (what speech-to-text models expect).

Privacy: these are your voice recordings. They stay in this folder only. Never put hindi_samples/ on GitHub
(it's in .gitignore). Share them only with the team, and delete them after testing.
Tips: a quiet room, phone/laptop mic about one arm away, speak like you normally talk to a person.
Needs only:  pip install sounddevice numpy
"""
import sys
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

RATE = 16000
SECONDS = 6
SENTENCES = [
    "जार्विस, मुझे शाम छह बजे मम्मी को फ़ोन करने की याद दिलाना।",
    "मेरी टू-डू लिस्ट में क्या है?",
    "ग्रोसरी लिस्ट में दो किलो आटा और दूध जोड़ दो।",
    "आज मौसम कैसा है?",
    "कल बारिश होगी क्या?",
    "हर दो घंटे में पानी पीने की याद दिलाना।",
    "पौधों को पानी देने वाला काम हो गया, उसे टिक कर दो।",
    "मेरा आज का दिन कैसा है?",
    "दस मिनट का टाइमर लगा दो।",
    "मेरे नोट्स पढ़कर सुनाओ।",
    "एक नोट लिखो: कल मीटिंग में बजट की बात करनी है।",
    "मेरी बहन का नाम रिया है, याद रखना।",
    "मेरी बहन का नाम क्या है?",
    "आज की ताज़ा ख़बरें सुनाओ।",
    "मुझे आज थोड़ा तनाव हो रहा है।",
    "कल सुबह नौ बजे फ़ॉर्म जमा करने की याद दिलाना।",
    "Jarvis, mera to-do list padh do.",
    "Kal shaam paanch baje bank jaana hai, remind kar dena.",
    "Grocery list mein tamatar add karo.",
    "Aaj kitne task baaki hain?",
]


def save(path, audio):
    data = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(data.tobytes())


def main():
    if len(sys.argv) < 2:
        print("Usage: python record_hindi.py YourName")
        return
    name = "".join(c for c in sys.argv[1] if c.isalnum()) or "speaker"
    folder = Path("hindi_samples") / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sentences.txt").write_text(
        "\n".join(f"{i + 1:02d}.wav\t{s}" for i, s in enumerate(SENTENCES)) + "\n", encoding="utf-8")
    print(f"Recording for {name}. {len(SENTENCES)} sentences, {SECONDS} seconds each.\n")
    i = 0
    while i < len(SENTENCES):
        out = folder / f"{i + 1:02d}.wav"
        print(f"[{i + 1}/{len(SENTENCES)}]  {SENTENCES[i]}" + ("   (already recorded)" if out.exists() else ""))
        key = input("   Enter = record, s = skip, q = stop: ").strip().lower()
        if key == "q":
            break
        if key == "s":
            i += 1
            continue
        print("   🎙️ Speak now...")
        audio = sd.rec(int(SECONDS * RATE), samplerate=RATE, channels=1, dtype="float32")
        sd.wait()
        audio = audio[:, 0]
        level = float(np.sqrt(np.mean(audio ** 2)))
        if level < 0.003:
            print("   ⚠️ That was almost silent. Check the microphone and press r to record again.")
        save(out, audio)
        again = input(f"   ✅ Saved {out.name}. Enter = next, r = record again: ").strip().lower()
        if again != "r":
            i += 1
    done = sum(1 for k in range(len(SENTENCES)) if (folder / f"{k + 1:02d}.wav").exists())
    print(f"\nDone: {done} of {len(SENTENCES)} recorded in {folder}. Thank you!")


if __name__ == "__main__":
    main()