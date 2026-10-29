"""Fix 84/85 check: the phone display, without the microphone.   python phone_check.py
Starts only the phone server on this computer (stop Jarvis first), pairs with the 4-letter code and PIN 1234,
and checks the on-device / online tags. Uses a fake online log in a temporary folder, never your real one."""
import http.cookiejar
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tempfile.mkdtemp())
import phone  # noqa: E402

ok = bad = 0


def check(name, cond):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name)


def log(entry):
    with open("online_log.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


phone.start(check_pin=lambda p: p == "1234", wake=lambda: None)
time.sleep(0.3)
phone.pair_link("127.0.0.1")
code = phone.LAST["code"]
check(f"4-letter code made: {code}", re.fullmatch(r"[A-Z0-9]{4}", code))
web = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
B = f"http://127.0.0.1:{phone.PORT}"


def post(path, data):
    return web.open(B + path, urllib.parse.urlencode(data).encode()).read().decode()


check("no camera: the address opens the code page", "4-letter code" in web.open(B + "/").read().decode())
check("a wrong code is refused", "isn't right" in post("/code", {"code": "ZZZZ"}))
page = post("/code", {"code": code.lower()})
check("the right code (small letters too) leads to the PIN page", "Type the PIN" in page)
tok = re.search(r'name="t" value="([^"]+)"', page).group(1)
check("a wrong PIN is refused", "Wrong PIN" in post("/pair", {"t": tok, "pin": "9999"}))
page = post("/pair", {"t": tok, "pin": "1234"})
check("the right PIN shows Jarvis's screen", 'id="light"' in page and 'id="face"' in page)
check("the code works only once", "isn't right" in post("/code", {"code": code}))

phone.on_heard("what's my to-do list")
phone.on_reply("You have 2 tasks left today.", "📋 to-do (rule)")
phone.on_heard("what's the weather")
log({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": "weather", "sent": "Hardoi", "host": "api.open-meteo.com", "status": "ok"})
log({"time": "2020-01-01 10:00:00", "tool": "news", "sent": None, "host": "bbc", "status": "ok"})
phone.on_reply("In Hardoi it's 24 degrees.", "🌐 internet (rule)")
phone.on_heard("look up my bank password")
log({"tool": "search", "sent": None, "status": "blocked"})
phone.on_reply("I won't send that online.", "🛡️ privacy")
import online
phone.on_heard("what's my day")
online.SAVED_USE.update(t=time.time(), age=250, what="weather")
phone.on_reply("In Hardoi it's 24 degrees.", "📅 my day")
phone.on_heard("tell me a fun fact")
phone.on_reply("Octopuses have three hearts.", "🧠 local AI")
d = json.loads(web.open(B + "/poll?since=0").read())
tags = [m["tag"] for m in d["messages"] if m["who"] == "jarvis"]
for t in tags:
    print("     ", t)
check("on-device answer tagged 🔒", tags[1].startswith("🔒"))
check("weather tagged 🌐 with exactly what was sent", "weather" in tags[2] and "Hardoi" in tags[2])
check("a stopped look-up tagged 🛡️ kept private", tags[3].startswith("🛡️"))
check("saved weather reused: tagged nothing sent now, saved 4 minutes ago", "Nothing sent now" in tags[4] and "4 minutes" in tags[4])
check("AI answer tagged 🧠 on-device", tags[5].startswith("🧠"))
s = d["state"]
check("the phone gets only screen info (no memories, no lists, no log)",
      set(s) == {"status", "online", "offline_mode", "mic", "mic_muted", "mic_switch", "quiet", "kid_mode",
                 "silent", "net_calls"})
check("only today's online calls counted (not old days, not the stopped one)", s["net_calls"] == 1)
check("no sound is ever sent to the phone (no audio on the phone page)", "audio" not in phone.APP_PAGE.lower())
check("disconnect logs the phone out", phone.disconnect() == 1)
try:
    web.open(B + "/poll?since=0")
    check("after disconnect the phone is refused", False)
except urllib.error.HTTPError as e:
    check("after disconnect the phone is refused", e.code == 403)
print(f"\n{ok} passed, {bad} failed")