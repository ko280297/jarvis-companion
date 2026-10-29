"""Try the online look-up without speaking. Sends only short topics to Wikipedia (same as Jarvis does).
Waits a few seconds between questions, so Wikipedia doesn't think we're spamming it (that was the 429 error).
   python lookup_check.py"""
import json, sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import lookup
QUESTIONS = ["who wrote romeo and juliet", "who is the father of the nation of india", "what is a black hole",
             "who discovered penicillin", "who painted the mona lisa",
             "who is elon musk", "who was the first person on the moon"]
for q in QUESTIONS:
    reply, route = lookup._lookup(q, [])
    print(f"\n{q}\n  -> {reply}")
    try:                                   # which pages were looked at (the last line of the online log)
        last = json.loads(lookup.LOG_FILE.read_text(encoding="utf-8").splitlines()[-1])
        print(f"     pages checked: {last.get('sent')}")
    except Exception:
        pass
    time.sleep(6)