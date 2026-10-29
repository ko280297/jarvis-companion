"""Measured numbers for the slide (fix 58). Reads jarvis_timing.csv (written while you talk to Jarvis).
  python timing_report.py
Shows, per kind of answer (on-device code / AI chat / online), the median and the slower 90% value of each step."""
import csv
import statistics
from pathlib import Path

path = Path(__file__).with_name("jarvis_timing.csv")
if not path.exists():
    raise SystemExit("No jarvis_timing.csv yet: talk to Jarvis by voice for a while (20+ questions), then run this again.")
rows = list(csv.DictReader(path.open(encoding="utf-8")))
cols = [("speech_to_text_s", "speech to text"), ("ai_s", "AI model"), ("reply_text_ready_s", "reply text ready"),
        ("voice_synthesis_s", "voice synthesis"), ("until_speaking_s", "TOTAL until Jarvis speaks")]
names = {"code": "On-device code (time, reminders, lists...)", "ai": "AI chat (Qwen 2.5 1.5B)",
         "online": "Online facts (weather, air, news)"}


def p90(v):
    v = sorted(v)
    return v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]


print(f"Measured on this device, {len(rows)} spoken turns\n")
for kind in ("code", "ai", "online"):
    rs = [r for r in rows if r["kind"] == kind]
    if not rs:
        continue
    print(f"{names[kind]}: {len(rs)} turns")
    for key, label in cols:
        vals = [float(r[key]) for r in rs if r.get(key) not in ("", None)]
        vals = [x for x in vals if x > 0 or key != "ai_s"]
        if vals:
            print(f"   {label:<28} median {statistics.median(vals):5.2f} s   90% under {p90(vals):5.2f} s")
    print()
share = {k: sum(1 for r in rows if r["kind"] == k) * 100 // max(1, len(rows)) for k in names}
print("Share of turns: " + ", ".join(f"{names[k].split(' (')[0]} {v}%" for k, v in share.items()))
