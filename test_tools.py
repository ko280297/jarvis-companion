import requests
from main import SYSTEM_PROMPT, TOOLS   # uses the exact prompt and tools from main.py

Q = "Do I need an umbrella in Delhi tomorrow?"
TIME_MSG = {"role": "system", "content": "Current local date and time (reference only, this is NOT the user's plans): now"}


def test(name, messages, **extra):
    body = {"model": "qwen2.5:1.5b", "stream": False, "tools": TOOLS, "messages": messages}
    body.update(extra)
    r = requests.post("http://127.0.0.1:11434/api/chat", json=body).json()
    calls = r["message"].get("tool_calls")
    print(f"{'✅' if calls else '❌'} {name}: {calls[0]['function'] if calls else r['message'].get('content')}")


user = {"role": "user", "content": Q}
sys = {"role": "system", "content": SYSTEM_PROMPT}
opts = {"options": {"temperature": 0, "num_predict": 80}}

test("1. only question", [user])
test("2. + system prompt", [sys, user])
test("3. + system prompt + time", [sys, TIME_MSG, user])
test("4. + logprobs", [sys, TIME_MSG, user], logprobs=True)
test("5. exactly like main.py", [sys, TIME_MSG, user], logprobs=True, **opts)