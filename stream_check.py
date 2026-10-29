"""Does streaming change the AI's confidence? Asks the same questions both ways and compares.
Needs Ollama running. Changes nothing.   python stream_check.py"""
import json, math, sys, requests
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
URL, MODEL = "http://127.0.0.1:11434/api/chat", "qwen2.5:1.5b"
SYSTEM = ("You are Jarvis, a warm, friendly companion who lives on this device. "
          "Reply in one or two short spoken sentences, with no lists, emojis or symbols. "
          "Never make up facts: if you are not sure, say you don't know.")
QUESTIONS = ["who was the first person on the moon", "what is photosynthesis", "tell me a fun fact about honey",
             "what is the capital of japan", "who wrote romeo and juliet", "how many legs does a spider have"]
OPTS = {"temperature": 0, "num_predict": 45, "num_ctx": 2048}

def conf(lps):
    return math.exp(sum(t["logprob"] for t in lps) / len(lps)) if lps else None

for q in QUESTIONS:
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
    d = requests.post(URL, json={"model": MODEL, "messages": msgs, "stream": False, "logprobs": True,
                                 "keep_alive": "24h", "options": OPTS}, timeout=120).json()
    a_text, a_lps = d["message"]["content"].strip(), d.get("logprobs") or []
    b_text, b_lps, first_keys, done_lps = "", [], None, 0
    with requests.post(URL, json={"model": MODEL, "messages": msgs, "stream": True, "logprobs": True,
                                  "keep_alive": "24h", "options": OPTS}, stream=True, timeout=120) as r:
        for line in r.iter_lines():
            if not line:
                continue
            c = json.loads(line)
            first_keys = first_keys or sorted(c.keys())
            if c.get("done"):
                done_lps = len(c.get("logprobs") or [])
            b_text += (c.get("message") or {}).get("content") or ""
            b_lps += c.get("logprobs") or []
    ca, cb = conf(a_lps), conf(b_lps)
    print(f"\n{q}\n  normal:    {ca if ca is None else round(ca, 2)}  ({len(a_lps)} tokens)  {a_text[:70]}")
    print(f"  streaming: {cb if cb is None else round(cb, 2)}  ({len(b_lps)} tokens, {done_lps} in the last part)  "
          f"{b_text.strip()[:70]}")
print("\nfirst streamed part has:", first_keys)