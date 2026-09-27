import math
import requests

QUESTIONS = [
    "What is the capital of France?",
    "Who invented the telephone?",
    "What is the meaning of the name Shubhangi?",
    "What is the meaning of the name Sarita?",
    "What is the meaning of the name Dinesh?",
]

for q in QUESTIONS:
    r = requests.post("http://localhost:11434/api/chat", json={
        "model": "qwen2.5:1.5b",
        "messages": [{"role": "user", "content": q + " Answer in one short sentence."}],
        "stream": False,
        "logprobs": True,
        "options": {"temperature": 0, "num_predict": 60},
    }).json()

    answer = r["message"]["content"].strip()
    lps = r.get("logprobs")
    if not lps:
        print("❌ This Ollama version does not return logprobs.")
        break
    conf = math.exp(sum(t["logprob"] for t in lps) / len(lps))
    print(f"\n{q}\n  -> {answer}\n  confidence: {conf:.2f}")