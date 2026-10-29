"""Phone access (fix 83, display redesign in fix 84, fits every screen in fix 85): use Jarvis from your phone's browser, over your own Wi-Fi.
No app, no cloud. The phone shows Jarvis's own screen: the face, the listening light, the answer, and whether
the answer stayed on the device or went online (and exactly what was sent).

  "Connect my phone"      -> a QR code on Jarvis's screen (works for 3 minutes, once)
                             no camera? open the address shown and type the 4-letter code
                             -> on the phone: type your PIN -> Jarvis's screen, with a typing bar
  "Disconnect my phone"   -> every phone is logged out at once
  "Is my phone connected?"

Safety:
  - Only devices on your own Wi-Fi can open it (private addresses only); never the internet.
  - The QR link / code works once, for 3 minutes. Then the PIN is needed: 5 wrong PINs and that link is dead.
    10 wrong codes and every open code is cancelled.
  - A phone stays logged in for 8 hours at most, and only while Jarvis runs (nothing is saved).
  - The chat is kept in RAM only (the last 40 messages), and cleared when the phone disconnects.
  - Replies to the phone are shown on the phone, not spoken aloud (the phone may be in another room).
  - The phone can only type to Jarvis. It can't mute, unmute or close Jarvis: the switch stays on the device.
Honest limit: on the Wi-Fi the page is plain HTTP, like a home printer's page, so use it on a Wi-Fi you trust.
"""
import html
import io
import ipaddress
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

PORT = 8766
PAIR_SECONDS = 180
SESSION_SECONDS = 8 * 3600
CODE_LETTERS = "ACDEFHJKMNPRTUVWXY3479"      # no 0/O, 1/I/L, 5/S, 8/B, 2/Z: easy to read and type
_pairs = {}              # token -> {"until", "tries"}
_codes = {}              # short code -> token (for devices without a camera)
_code_fails = [0]
_sessions = {}           # token -> until
_chat = []               # [{"n", "who", "text", "t", "kind", "tag"}]
_n = [0]
_inbox = []              # texts typed on the phone, waiting for Jarvis
_lock = threading.Lock()
_server = None
ONLINE_LOG = Path("online_log.jsonl")
STATE = {"from_phone": False, "pin_hash": lambda: None, "check_pin": lambda pin: False, "wake": None,
         "log_pos": 0, "turn_t": 0.0, "saved_seen": 0.0}
LAST = {"code": "", "url": ""}

_BASE_CSS = """*{box-sizing:border-box}body{font-family:system-ui,"Segoe UI",sans-serif;background:#0f1115;color:#e7e9ee;
margin:0}.box{background:#171a21;border:1px solid #262b36;padding:28px;border-radius:18px;width:min(340px,92vw);
text-align:center}h1{font-size:22px;margin:0 0 6px}p{color:#8b93a7;font-size:14px}
.center{display:flex;min-height:100vh;align-items:center;justify-content:center;padding:16px}
input{font-size:28px;letter-spacing:8px;text-align:center;width:100%;padding:10px;border-radius:12px;
border:1px solid #3a4150;background:#0b0d11;color:#fff}button{margin-top:16px;width:100%;padding:14px;font-size:17px;
border:0;border-radius:12px;background:#2e8b72;color:#fff}.err{color:#ff8080;min-height:20px;margin-top:8px}
.eyes{display:flex;justify-content:center;gap:26px;margin:0 0 14px}.eyes i{width:30px;height:42px;border-radius:15px;
background:radial-gradient(circle at 35% 30%,#d6fff3,#8ff0d2 55%,#5fd0ad);box-shadow:0 0 16px rgba(143,240,210,.5)}"""

PAIR_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><title>Jarvis</title><style>""" + _BASE_CSS + """</style></head><body><div class="center">
<form class="box" method="post" action="/pair"><div class="eyes"><i></i><i></i></div>
<h1>Connect to Jarvis</h1><p>Type the PIN you use on Jarvis</p>
<input name="pin" type="password" inputmode="numeric" autocomplete="off" maxlength="8" autofocus>
<input type="hidden" name="t" value="{token}"><div class="err">{error}</div><button>Connect</button></form></div>
</body></html>"""

CODE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1"><title>Jarvis</title><style>""" + _BASE_CSS + """</style></head><body><div class="center">
<form class="box" method="post" action="/code"><div class="eyes"><i></i><i></i></div>
<h1>Connect to Jarvis</h1><p>Say <b>"connect my phone"</b> to Jarvis, then type the 4-letter code from its screen</p>
<input name="code" autocomplete="off" autocapitalize="characters" maxlength="4" autofocus style="text-transform:uppercase">
<div class="err">{error}</div><button>Next</button></form></div></body></html>"""

APP_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,
initial-scale=1,viewport-fit=cover"><meta name="theme-color" content="#0f1115"><title>Jarvis</title><style>
:root{--bg:#0f1115;--card:#171a21;--line:#262b36;--text:#e7e9ee;--muted:#8b93a7;--green:#3ddc84;--amber:#ffb020;
--blue:#4da3ff;--red:#ff5d5d;--eye:#8ff0d2;--glow:rgba(143,240,210,.5)}
*{box-sizing:border-box}html,body{height:100%;overflow:hidden}body{height:100dvh}
body{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,"Segoe UI",sans-serif;display:flex;
flex-direction:column;-webkit-tap-highlight-color:transparent}
.top{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 16px 0}
.clock{font-size:13px;color:var(--muted);letter-spacing:.04em}.clock b{color:var(--text);font-size:17px;margin-right:6px}
.icons{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.icons span{font-size:12px;color:var(--muted);white-space:nowrap}
.icons .warn{color:var(--amber)}
.main{flex:1;min-height:0;overflow-y:auto;padding:0 16px 8px;-webkit-overflow-scrolling:touch}
.light{display:flex;align-items:center;justify-content:center;gap:10px;margin:14px auto 0;padding:8px 16px;
border-radius:999px;background:var(--card);border:1px solid var(--line);width:max-content;max-width:100%;font-size:15px}
.light i{width:14px;height:14px;border-radius:50%;background:#3a4150;flex:none;transition:background .3s,box-shadow .3s}
.light.on i{background:var(--green);box-shadow:0 0 12px var(--green);animation:pulse 1.2s infinite}
.light.wait i{background:#2f7d63;box-shadow:0 0 6px #2f7d63}
.light.think i{background:var(--amber);box-shadow:0 0 12px var(--amber);animation:pulse 1.2s infinite}
.light.talk i{background:var(--blue);box-shadow:0 0 12px var(--blue)}
.light.off{border-color:var(--red)}.light.off i{background:var(--red);box-shadow:0 0 12px var(--red)}
@keyframes pulse{50%{opacity:.35}}
.face{position:relative;width:200px;height:150px;margin:18px auto 4px;transition:transform .4s,height .4s,margin .4s;transform-origin:top center}
body.long .face{transform:scale(.55);height:84px;margin:10px auto 0}
@media (max-height:700px){.face{transform:scale(.7);height:106px;margin-top:10px}body.long .face{transform:scale(.45);height:70px}}
.head{animation:breathe 4.5s ease-in-out infinite}@keyframes breathe{50%{transform:translateY(-4px) scale(1.015)}}
.eyes{display:flex;justify-content:center;gap:42px;margin-top:14px;transition:transform .6s}
.eye{position:relative;width:54px;height:74px;border-radius:27px;
background:radial-gradient(circle at 35% 30%,#d6fff3,var(--eye) 55%,#5fd0ad);box-shadow:0 0 22px var(--glow);
transition:height .16s,width .25s,border-radius .25s,box-shadow .3s}
.eye::after{content:"";position:absolute;top:11px;left:12px;width:12px;height:12px;border-radius:50%;
background:rgba(255,255,255,.9);transition:opacity .15s}
.face.blink .eye{height:6px}.face.blink .eye::after,.face.sleep .eye::after{opacity:0}
.face.listen .eye{width:60px;height:84px;box-shadow:0 0 34px var(--glow)}
.face.think .eyes{transform:translate(-8px,-9px)}.face.think .eye{height:62px}
.face.sleep .eye{height:6px;border-radius:4px;box-shadow:0 0 8px var(--glow)}
.mouth{width:26px;height:10px;margin:14px auto 0;border:4px solid var(--eye);border-top:none;border-radius:0 0 18px 18px;
transition:all .2s}
.face.sleep .mouth,.face.think .mouth{width:12px;height:4px;border:none;background:var(--eye);border-radius:4px}
.face.speak .mouth{width:24px;height:12px;border:none;background:var(--eye);border-radius:4px 4px 14px 14px;
animation:talk .3s ease-in-out infinite alternate}@keyframes talk{from{height:6px}to{height:14px}}
.zz{display:none;position:absolute;top:-22px;right:-4px;color:var(--muted);font-weight:700;animation:float 2.6s infinite}
.face.sleep .zz{display:block}@keyframes float{0%{transform:translateY(6px);opacity:0}40%{opacity:1}
100%{transform:translateY(-16px);opacity:0}}
.dots{display:none;position:absolute;top:-30px;right:-6px;padding:4px 10px;border-radius:18px;border:2px solid var(--eye);
color:var(--eye);background:var(--card);letter-spacing:4px;font-size:16px}.face.think .dots{display:block}
.dots span{animation:dot 1.2s infinite;opacity:.2}.dots span:nth-child(2){animation-delay:.2s}
.dots span:nth-child(3){animation-delay:.4s}@keyframes dot{30%{opacity:1}}
.said{max-width:560px;margin:10px auto 0;text-align:center}
.you{color:var(--muted);font-size:15px;min-height:20px;line-height:1.4}
.answer{font-size:clamp(17px,5.2vw,21px);line-height:1.45;margin-top:8px;white-space:pre-wrap;overflow-wrap:anywhere}
body.long .answer{font-size:clamp(15px,4.3vw,18px);text-align:left}body.long .said{text-align:left}
.tag{display:inline-block;margin-top:12px;padding:5px 12px;border-radius:999px;font-size:13px;border:1px solid var(--line);
color:var(--muted);max-width:100%}
.tag.online{color:var(--blue);border-color:var(--blue)}.tag.ai{color:var(--eye);border-color:#2f7d63}
.tag.blocked{color:var(--amber);border-color:var(--amber)}
details{max-width:560px;margin:22px auto 10px;color:var(--muted);font-size:14px}
summary{cursor:pointer;text-align:center;list-style:none}summary::-webkit-details-marker{display:none}
.old{border-top:1px solid var(--line);padding:10px 2px;line-height:1.4}.old b{color:var(--text);font-weight:500}
.old small{display:block;margin-top:4px;font-size:12px}
.quick{display:flex;gap:8px;overflow-x:auto;padding:8px 16px 0;scrollbar-width:none;flex:none}.quick::-webkit-scrollbar{display:none}
.quick button{flex:none;border:1px solid var(--line);background:var(--card);color:var(--text);border-radius:12px;padding:10px 14px;font-size:14px;touch-action:manipulation;cursor:pointer}
.quick button:active,form button:active{opacity:.6}
form{display:flex;gap:8px;padding:10px 16px calc(14px + env(safe-area-inset-bottom));flex:none}
input{flex:1;min-width:0;font-size:17px;padding:12px 16px;border-radius:999px;border:1px solid var(--line);
background:var(--card);color:var(--text)}
form button{border:0;border-radius:999px;background:#2e8b72;color:#fff;padding:0 20px;font-size:16px;touch-action:manipulation}
.gone{padding:40px 24px;text-align:center;color:var(--muted)}
</style></head><body>
<div class="top"><div class="clock" id="clock"></div><div class="icons" id="icons"></div></div>
<div class="main" id="main">
  <div class="light wait" id="light"><i></i><span id="lightText">Connecting…</span></div>
  <div class="face" id="face"><div class="head"><div class="eyes" id="eyes"><div class="eye"></div><div class="eye"></div></div>
    <div class="mouth"></div></div><div class="zz">z</div><div class="dots"><span>•</span><span>•</span><span>•</span></div></div>
  <div class="said"><div class="you" id="you"></div><div class="answer" id="answer"></div><div id="tagWrap"></div></div>
  <details id="earlier"><summary>Earlier ▾</summary><div id="oldList"></div></details>
</div>
<div class="quick"><button>What's my day?</button><button>My to-do list</button><button>My reminders</button>
<button>Read my notes</button><button>What's the weather?</button></div>
<form id="f"><input id="t" placeholder="Type to Jarvis…" autocomplete="off" enterkeyhint="send"><button>Send</button></form>
<script>
const $ = (id) => document.getElementById(id);
let since = 0, msgs = [], status = "waiting", waitingForMe = false;
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function tick() {
  const d = new Date();
  $("clock").innerHTML = "<b>" + d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"}) + "</b>"
    + d.toLocaleDateString([], {weekday: "short", day: "numeric", month: "short"});
}
tick(); setInterval(tick, 10000);
setInterval(() => {                       // a blink now and then, like on the device
  const f = $("face"); if (f.className !== "face") return;
  f.classList.add("blink"); setTimeout(() => f.classList.remove("blink"), 160);
}, 4200);
function draw() {
  let lastYou = -1, lastJ = -1;
  msgs.forEach((m, i) => { if (m.who === "you") lastYou = i; else lastJ = i; });
  const you = lastYou >= 0 ? msgs[lastYou] : null;
  const ans = lastJ > lastYou ? msgs[lastJ] : (lastYou < 0 && lastJ >= 0 ? msgs[lastJ] : null);
  $("you").textContent = you ? "You: " + you.text : "";
  $("answer").textContent = ans ? ans.text : (you ? "" : "Type below, or tap a button.");
  $("tagWrap").innerHTML = ans && ans.tag ? `<span class="tag ${esc(ans.kind)}">${esc(ans.tag)}</span>` : "";
  document.body.classList.toggle("long", !!ans && ans.text.length > 140);
  // earlier answers, newest first
  const old = [];
  const end = lastYou >= 0 ? lastYou : msgs.length;
  for (let i = end - 1; i >= 0; i--) {
    const m = msgs[i];
    if (m.who === "jarvis") {
      const q = i > 0 && msgs[i - 1].who === "you" ? msgs[i - 1].text : "";
      old.push(`<div class="old">${q ? "<b>You: " + esc(q) + "</b><br>" : ""}${esc(m.text)}`
        + (m.tag ? `<small>${esc(m.tag)}</small>` : "") + "</div>");
    }
  }
  $("earlier").style.display = old.length ? "block" : "none";
  $("oldList").innerHTML = old.slice(0, 15).join("");
}
function light(s) {
  const L = $("light"), T = $("lightText");
  let cls = "wait", txt = "Waiting for \\u201cHey Jarvis\\u201d", face = "";
  if (s.mic_switch) { cls = "off"; txt = "Muted by the switch. I can't hear anything"; face = "sleep"; }
  else if (s.mic_muted) { cls = "off"; txt = "Microphone muted"; face = "sleep"; }
  else if (s.mic === false) { cls = "off"; txt = "Microphone off"; face = "sleep"; }
  else if (s.status === "listening") { cls = "on"; txt = "Listening…"; face = "listen"; }
  else if (s.status === "thinking") { cls = "think"; txt = "Thinking…"; face = "think"; }
  else if (s.status === "speaking") { cls = "talk"; txt = s.silent ? "Answering here" : "Speaking…"; face = "speak"; }
  if (waitingForMe && cls !== "off" && s.status !== "speaking") { cls = "think"; txt = "Thinking…"; face = "think"; }
  L.className = "light " + cls; T.textContent = txt;
  $("face").className = "face" + (face ? " " + face : "");
  const icons = [];
  if (s.offline_mode) icons.push('<span class="warn">✈️ Offline mode</span>');
  else if (s.online === false) icons.push('<span class="warn">✈️ Offline</span>');
  else icons.push("<span>🌐 Online</span>");
  icons.push(s.quiet ? '<span class="warn">🤫 Quiet</span>' : "<span>🔈 Voice</span>");
  icons.push(`<span>🌐 ${s.net_calls} online today</span>`);
  if (s.kid_mode) icons.unshift('<span class="warn">🧸 Kid mode</span>');
  $("icons").innerHTML = icons.join("");
}
async function send(text) {
  if (!text.trim()) return;
  waitingForMe = true;
  msgs.push({who: "you", text, n: -1}); draw();     // show it at once, the server copy replaces it
  const r = await fetch("/send", {method: "POST", headers: {"Content-Type": "application/json"},
                                  body: JSON.stringify({text})});
  if (r.status === 403) location.reload();
}
$("f").onsubmit = (e) => { e.preventDefault(); send($("t").value); $("t").value = ""; };
document.querySelectorAll(".quick button").forEach((b) => b.onclick = () => send(b.textContent));
async function poll() {
  try {
    const r = await fetch("/poll?since=" + since);
    if (r.status === 403) { document.body.innerHTML =
      "<div class='gone'>Disconnected. Say \\u201cconnect my phone\\u201d to Jarvis to connect again.</div>"; return; }
    const d = await r.json();
    if (d.messages.length) {
      msgs = msgs.filter((m) => m.n !== -1);
      d.messages.forEach((m) => { msgs.push(m); since = m.n; if (m.who === "jarvis") waitingForMe = false; });
      msgs = msgs.slice(-40); draw(); $("main").scrollTop = 0;
    }
    light(d.state || {});
  } catch (e) {}
  setTimeout(poll, 800);
}
draw(); poll();
</script></body></html>"""


def _push(who, text, kind="", tag=""):
    with _lock:
        _n[0] += 1
        _chat.append({"n": _n[0], "who": who, "text": text, "t": time.time(), "kind": kind, "tag": tag})
        del _chat[:-40]


def connected():
    now = time.time()
    for k in [k for k, v in _sessions.items() if v < now]:
        _sessions.pop(k, None)
    return bool(_sessions)


def _log_size():
    try:
        return ONLINE_LOG.stat().st_size
    except OSError:
        return 0


def _new_calls():
    """Online calls written to the log since this turn began (what left the device for this answer)."""
    try:
        with ONLINE_LOG.open("rb") as f:
            f.seek(STATE["log_pos"])
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    calls = []
    for line in lines:
        try:
            calls.append(json.loads(line))
        except ValueError:
            pass
    return calls


TOOL_NAMES = {"weather": "weather", "environment": "air quality", "news": "news", "search": "look-up",
              "check": "connection check", "schedule": "weather", "grocery": "look-up"}


def _saved_use():
    """'weather saved 4 minutes ago' if this answer reused online data fetched earlier (fix 85)."""
    try:
        import online
        u = online.SAVED_USE
    except Exception:
        return ""
    if u.get("t", 0) == STATE["saved_seen"]:          # fix 86: not reused in THIS turn (no clock needed)
        return ""
    mins = int(u.get("age", 0) // 60)
    return f"{u.get('what', 'data')} saved " + ("just now" if mins < 1 else f"{mins} minute{'s' if mins > 1 else ''} ago")


def tag_for(route=""):
    """(kind, label) for an answer: did it stay on this device, or what exactly went online?"""
    calls = _new_calls()
    blocked = [c for c in calls if c.get("status") == "blocked"]
    sent = [c for c in calls if c.get("status") != "blocked"]
    if sent:
        what = list(dict.fromkeys(TOOL_NAMES.get(c.get("tool"), c.get("tool") or "look-up") for c in sent))
        words = [str(c.get("sent")) for c in sent if c.get("sent")]
        label = "🌐 Went online: " + ", ".join(what)
        label += (" · sent only “" + "”, “".join(dict.fromkeys(words)) + "”") if words \
            else " · nothing personal sent"
        if all(c.get("status") == "failed" for c in sent):
            label = label.replace("Went online", "Tried to go online (no answer)")
        return "online", label
    saved = _saved_use()
    if saved:
        return "local", f"🔒 Nothing sent now · used the {saved}"
    if blocked:
        return "blocked", "🛡️ Kept private: a look-up was stopped, nothing left this device"
    if (route or "").startswith(("🧠", "📚", "🤔", "🛡️")):
        return "ai", "🧠 On-device AI · nothing left this device"
    return "local", "🔒 Answered on this device · nothing left it"


def on_reply(text, route=""):
    """Every reply Jarvis says also appears on a connected phone, tagged on-device or online."""
    if connected() and text:
        kind, tag = tag_for(route)
        _push("jarvis", text, kind, tag)


def on_heard(text):
    """A new turn starts: remember where the online log ends, and show what was said out loud on the phone."""
    STATE["log_pos"] = _log_size()
    STATE["turn_t"] = time.time()
    try:
        import online
        STATE["saved_seen"] = online.SAVED_USE.get("t", 0)
    except Exception:
        pass
    if connected() and text and not STATE["from_phone"]:
        _push("you", "🎙️ " + text)


def next_message():
    """A message typed on the phone, or None. Marks this turn as coming from the phone."""
    with _lock:
        if _inbox:
            STATE["from_phone"] = True
            return _inbox.pop(0)
    STATE["from_phone"] = False
    return None


def silent():
    """Replies to the phone aren't spoken aloud."""
    return STATE["from_phone"]


def pair_link(ip):
    token = secrets.token_urlsafe(12)
    _pairs[token] = {"until": time.time() + PAIR_SECONDS, "tries": 0}
    code = "".join(secrets.choice(CODE_LETTERS) for _ in range(4))
    _codes[code] = token
    LAST.update(code=code, url=f"{ip}:{PORT}")
    return f"http://{ip}:{PORT}/p/{token}"


def disconnect():
    n = len(_sessions)
    _sessions.clear()
    _pairs.clear()
    _codes.clear()
    with _lock:
        _chat.clear()
        _inbox.clear()
    return n


_net = {"size": -1, "count": 0, "day": ""}


def _net_calls():
    """How many times Jarvis has gone online today (fix 85: today only, not since the first day)."""
    size, day = _log_size(), time.strftime("%Y-%m-%d")
    if size != _net["size"] or day != _net["day"]:
        count = 0
        try:
            for line in ONLINE_LOG.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("status") != "blocked" and str(e.get("time", "")).startswith(day):
                    count += 1
        except OSError:
            pass
        _net.update(size=size, day=day, count=count)
    return _net["count"]


def screen_state():
    """Only what the phone needs to look like Jarvis's screen. No memories, no lists, no log details."""
    try:
        import dashboard
        s = dashboard.STATE
    except Exception:
        s = {}
    return {"status": s.get("status", "waiting"), "online": s.get("online"), "offline_mode": bool(s.get("offline_mode")),
            "mic": s.get("mic"), "mic_muted": bool(s.get("mic_muted")), "mic_switch": bool(s.get("mic_switch")),
            "quiet": bool(s.get("quiet")), "kid_mode": bool(s.get("kid_mode")), "silent": STATE["from_phone"],
            "net_calls": _net_calls()}


class _Handler(BaseHTTPRequestHandler):
    def _ok_client(self):
        try:
            ip = ipaddress.ip_address(self.client_address[0])
            return ip.is_private or ip.is_loopback
        except ValueError:
            return False

    def _session(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "jarvis" and _sessions.get(v, 0) > time.time():
                return v
        return None

    def _send(self, code, body, ctype="text/html; charset=utf-8", extra=()):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._ok_client():
            return self._send(403, "Only devices on this Wi-Fi.")
        if self.path.startswith("/p/"):
            token = self.path[3:].split("?")[0]
            p = _pairs.get(token)
            if not p or p["until"] < time.time():
                return self._send(404, "<p style='font-family:sans-serif;padding:24px'>This code has expired. "
                                       "Say: connect my phone, and scan the new code.</p>")
            return self._send(200, PAIR_PAGE.replace("{token}", html.escape(token)).replace("{error}", ""))
        if self.path in ("/", "/app"):
            if self._session():
                return self._send(200, APP_PAGE)
            return self._send(200, CODE_PAGE.replace("{error}", ""))     # no camera: type the code instead
        if self.path.startswith("/poll"):
            if not self._session():
                return self._send(403, "{}", "application/json")
            try:
                since = int(self.path.split("since=")[1])
            except (IndexError, ValueError):
                since = 0
            with _lock:
                msgs = [m for m in _chat if m["n"] > since]
            return self._send(200, json.dumps({"messages": msgs, "state": screen_state()}), "application/json")
        return self._send(404, "Not found")

    def do_POST(self):
        if not self._ok_client():
            return self._send(403, "Only devices on this Wi-Fi.")
        length = min(int(self.headers.get("Content-Length") or 0), 4000)
        body = self.rfile.read(length).decode("utf-8", "replace")
        if self.path == "/code":
            from urllib.parse import parse_qs
            code = ((parse_qs(body).get("code") or [""])[0]).strip().upper()
            token = _codes.get(code)
            if token and _pairs.get(token, {}).get("until", 0) > time.time():
                _codes.pop(code, None)                      # the code works once; the PIN comes next
                return self._send(303, "", extra=[("Location", f"/p/{token}")])
            _code_fails[0] += 1
            if _code_fails[0] >= 10:                        # someone is guessing: cancel every open code
                _codes.clear()
                _pairs.clear()
                _code_fails[0] = 0
                return self._send(403, "<p style='font-family:sans-serif;padding:24px'>Too many wrong codes. "
                                       "Ask Jarvis for a new one.</p>")
            return self._send(200, CODE_PAGE.replace("{error}", "That code isn't right, or it has expired."))
        if self.path == "/pair":
            from urllib.parse import parse_qs
            q = parse_qs(body)
            token, pin = (q.get("t") or [""])[0], (q.get("pin") or [""])[0]
            p = _pairs.get(token)
            if not p or p["until"] < time.time():
                return self._send(404, "<p style='font-family:sans-serif;padding:24px'>This code has expired.</p>")
            if not STATE["check_pin"](pin):
                p["tries"] += 1
                if p["tries"] >= 5:
                    _pairs.pop(token, None)
                    return self._send(403, "<p style='font-family:sans-serif;padding:24px'>Too many wrong PINs. "
                                           "Ask Jarvis for a new code.</p>")
                return self._send(200, PAIR_PAGE.replace("{token}", html.escape(token))
                                  .replace("{error}", f"Wrong PIN ({5 - p['tries']} tries left)"))
            _pairs.pop(token, None)                         # a code works once
            for c in [c for c, t in _codes.items() if t == token]:
                _codes.pop(c, None)
            session = secrets.token_urlsafe(24)
            _sessions[session] = time.time() + SESSION_SECONDS
            _push("jarvis", "Connected. Type anything you'd say to me. Answers show here and aren't spoken aloud.",
                  "local", "🔒 Only on your Wi-Fi")
            if STATE.get("notify"):
                STATE["notify"]()
            return self._send(303, "", extra=[("Location", "/app"), ("Set-Cookie",
                              f"jarvis={session}; HttpOnly; SameSite=Strict; Max-Age={SESSION_SECONDS}; Path=/")])
        if self.path == "/send":
            if not self._session():
                return self._send(403, "{}", "application/json")
            try:
                text = str(json.loads(body).get("text", "")).strip()[:300]
            except (ValueError, AttributeError):
                text = ""
            if text:
                with _lock:
                    _inbox.append(text)
                _push("you", text)
                if STATE.get("wake"):
                    STATE["wake"]()                         # tell Jarvis there's something to read
            return self._send(200, "{}", "application/json")
        return self._send(404, "Not found")

    def log_message(self, *args):
        pass


def start(check_pin, wake, notify=None):
    """check_pin(pin) -> True/False; wake() nudges Jarvis's main loop."""
    global _server
    STATE.update(check_pin=check_pin, wake=wake, notify=notify)
    if _server is None:
        from dashboard import _QuietServer
        _server = _QuietServer(("0.0.0.0", PORT), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()


def qr_svg(url):
    import qrcode
    import qrcode.image.svg
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10).save(buf)
    return buf.getvalue().decode()
