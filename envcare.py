"""Environment, step 2 (fix 57): care, not just numbers.
 - Severe weather warnings from the forecast (possible storm-level winds, very heavy rain, a heat wave, very bad air),
   using IMD-style levels. Always "possible", always "check IMD's official alerts". On request any time;
   checked automatically every 3 hours only if the user turns it on.
 - Asthma / breathing care: "I have asthma" (or a saved memory that says so) -> earlier, gentler air advice.
   Edit: "I don't have asthma" / "turn off asthma care".
 - Smart reminders that ask first: an outdoor reminder at a bad-air / very hot / rainy hour gets
   "Want me to move it to 6 PM?"; watering the plants when rain is coming gets "Want me to skip it that day?"
 - Mosquito weather: warm + humid + recent rain = mosquito-friendly, with dengue / malaria tips. An estimate from
   the weather, not a mosquito count.
Only the city's coordinates go online (logged); offline mode blocks it; the AI never makes up any of this."""
import re
import time
from datetime import datetime, timedelta

import requests

import environment as env
import online

MEMORY = None          # set by main.py
_pending = None        # {"kind": "move" | "skip", "rid": ..., "due": datetime, "at": time}

# IMD-style levels
RAIN_LEVELS = [(204.5, "extremely heavy rain"), (115.6, "very heavy rain"), (64.5, "heavy rain")]   # mm in a day
GUST_STORM, GUST_SEVERE = 62, 89       # km/h, like IMD's cyclonic storm / severe cyclonic storm wind levels
HEAT, SEVERE_HEAT = 40, 45             # °C, plains

SENSITIVE_WORDS = re.compile(r"\b(?:asthma|asthmatic|copd|bronchitis|breathing (?:problem|issue|trouble)s?"
                             r"|lung (?:disease|problem)s?|wheez\w*)\b", re.I)
SET_SENSITIVE = re.compile(r"^(?:i have|i've got|i got|i suffer from|i am|i'm)\s+(?:an?\s+)?(?:asthma|asthmatic|copd"
                           r"|bronchitis|breathing (?:problems?|issues?|trouble)|lung (?:disease|problems?)|a weak chest)\b", re.I)
UNSET_SENSITIVE = re.compile(r"\b(?:i (?:don'?t|do not) have (?:asthma|copd|bronchitis|breathing problems?)(?: any ?more)?"
                             r"|(?:turn|switch) off (?:the )?(?:asthma|breathing|air) care|i'?m not asthmatic"
                             r"|my asthma is (?:gone|cured|better now))\b", re.I)
WARN_ON = re.compile(r"\b(?:turn|switch) on (?:the )?(?:severe )?weather (?:warnings?|alerts?)\b|\bwarn me about (?:the )?weather\b", re.I)
WARN_OFF = re.compile(r"\b(?:turn|switch) off (?:the )?(?:severe )?weather (?:warnings?|alerts?)\b"
                      r"|\b(?:stop|no more) (?:the )?weather (?:warnings?|alerts?)\b", re.I)
WARN_Q = re.compile(r"\b(?:any|are there(?: any)?) (?:severe |bad )?weather (?:warnings?|alerts?)\b"
                    r"|\b(?:storm|cyclone|thunderstorm|heat ?wave|flood|very heavy rain|heavy rain)\b.*\b(?:coming|expected|today|tomorrow|soon)\b"
                    r"|\b(?:is|will) (?:a |there be a )?(?:storm|cyclone|heat ?wave|flood)\b|\bis it safe (?:outside )?tomorrow\b", re.I)
MOSQ_Q = re.compile(r"\b(?:mosquito\w*|mosquitto\w*|machhar|dengue|malaria|chikungunya)\b", re.I)
YES = re.compile(r"^(?:yes|yeah|yep|sure|ok(?:ay)?|please(?: do)?|do it|go ahead|move it|skip it|haan)\b", re.I)
NO = re.compile(r"^(?:no|nope|not now|leave it|keep it|don'?t|never ?mind)\b", re.I)
OUTDOOR = re.compile(r"\b(?:walk|walking|run|running|jog|jogging|cycl\w+|ride|exercise|workout|yoga|play\w*|cricket"
                     r"|football|badminton|park|outdoor\w*|outside|morning walk|evening walk)\b", re.I)
WATERING = re.compile(r"\bwater(?:ing)? (?:the |my )?(?:plants?|garden|lawn|flowers|tulsi)\b", re.I)


def sensitive(memory):
    """On if the user said so, or a saved memory mentions asthma / breathing problems (unless they turned it off)."""
    s = memory.get_setting("env_sensitive")
    if s in ("on", "off"):
        return s == "on"
    return any(SENSITIVE_WORDS.search(m.split(" (saved on")[0]) for m in memory.all()
               if re.search(r"\b(?:i|my)\b", m, re.I))


# ---------- severe weather ----------
def warnings(data, memory):
    """[(key, sentence)] for the next two days."""
    now = env._local_now(data)
    hours = env._hours(data["wx"].get("hourly"), now, ahead=48)
    out = []
    for day in sorted({dt.date() for dt, _ in hours}):
        h = [v for dt, v in hours if dt.date() == day]
        word = "today" if day == now.date() else "tomorrow" if day == now.date() + timedelta(days=1) else f"on {day:%A}"
        rain = sum((v.get("precipitation") or 0) for v in h)
        gust = max([(v.get("wind_gusts_10m") or 0) for v in h] or [0])
        hot = max([(v.get("temperature_2m") or 0) for v in h] or [0])
        level = next((name for mm, name in RAIN_LEVELS if rain >= mm), None)
        if gust >= GUST_STORM:
            kind = "severe storm" if gust >= GUST_SEVERE else "storm"
            rain_part = f" and {level}" if level else ""
            out.append((f"wind-{day}", f"very strong winds, gusts up to {round(gust)} km/h{rain_part}, are forecast "
                                       f"{word}. This could be a {kind}"))
        elif level:
            out.append((f"rain-{day}", f"{level}, about {round(rain)} millimetres, is forecast {word}"))
        if hot >= HEAT:
            out.append((f"heat-{day}", f"{'severe heat wave' if hot >= SEVERE_HEAT else 'heat-wave level'} temperatures, "
                                       f"up to {round(hot)} degrees, are forecast {word}. Drink water often and avoid "
                                       "the afternoon sun"))
    a = env.current_aqi(data)
    limit = 3 if sensitive(memory) else 4                       # poor for sensitive people, very poor for everyone
    if a and a[0] is not None and a[1] >= limit:
        out.append((f"air-{now.date()}", f"the air is {env.CATEGORIES[a[1]]} right now, AQI {a[0]}. "
                                         "Keep windows closed and limit time outside"))
    return out


def _warning_text(ws):
    first = ws[0][1]
    text = "⚠️ Weather warning: " + first[:1].upper() + first[1:] + "."
    for _, w in ws[1:3]:
        text += " Also, " + w + "."
    if any(k.startswith(("wind", "rain")) for k, _ in ws):
        text += " This is from the forecast, so please also check IMD's official alerts, and stay indoors if advised."
    return text


def warning_due(memory):
    """Called from the main loop: every 3 hours, only if the user turned warnings on. Each warning once a day."""
    if memory.get_setting("env_warnings") != "on" or not memory.get_setting("home_city"):
        return None
    last = float(memory.get_setting("env_warn_checked") or 0)
    if time.time() - last < 3 * 3600:
        return None
    memory.set_setting("env_warn_checked", str(time.time()))
    try:
        data = env.get_data(memory.get_setting("home_city"))
    except requests.RequestException:
        return None
    if not data:
        return None
    said = set((memory.get_setting("env_warned") or "").split("|"))
    new = [(k, w) for k, w in warnings(data, memory) if k not in said]
    if not new:
        return None
    memory.set_setting("env_warned", "|".join(list(said | {k for k, _ in new})[-20:]))
    return {"say": _warning_text(new), "card": env.card(data)}


# ---------- mosquitoes ----------
def mosquito_answer(city):
    place = online._geocode(city)
    if place is None:
        return f"I couldn't find a place called {city}."
    lat, lon = place["latitude"], place["longitude"]
    wx = online._get("environment", f"{lat:.2f},{lon:.2f}", "https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "auto", "past_days": 10, "forecast_days": 3,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation",
    }).json()
    data = {"wx": wx, "offset": wx.get("utc_offset_seconds", 0)}
    now = env._local_now(data)
    past = env._hours(wx.get("hourly"), now, ahead=0, back=240)
    soon = env._hours(wx.get("hourly"), now, ahead=72)
    if not past or not soon:
        return "I couldn't get the weather needed for that right now."
    temp = sum((v.get("temperature_2m") or 0) for _, v in soon) / len(soon)
    hum = sum((v.get("relative_humidity_2m") or 0) for _, v in soon) / len(soon)
    rain = sum((v.get("precipitation") or 0) for _, v in past)
    rain_soon = sum((v.get("precipitation") or 0) for _, v in soon)
    score = (2 if 25 <= temp <= 32 else 1 if 20 <= temp <= 35 else 0) \
        + (2 if hum >= 75 else 1 if hum >= 60 else 0) + (2 if rain >= 10 else 1 if rain >= 2 else 0) \
        + (1 if rain_soon >= 5 else 0)
    if temp < 16:
        score = 0
    level = "high" if score >= 5 else "moderate" if score >= 3 else "low"
    why = [f"about {round(temp)} degrees"]
    why.append("humid" if hum >= 60 else "fairly dry")
    if rain >= 2:
        why.append("it rained in the last 10 days, so water may be standing")
    if rain_soon >= 5:
        why.append("more rain is coming")
    reply = f"Mosquito weather in {place['name']} looks {level} right now: " + ", ".join(why) + "."
    if level == "low":
        return reply + " Still, empty any standing water at home now and then. This is an estimate from the weather."
    return (reply + " Dengue mosquitoes bite mostly in the daytime, especially early morning and late afternoon, "
            "and malaria mosquitoes at night. Use repellent, wear long sleeves, sleep under a net, and empty standing "
            "water in coolers, pots and tyres every week. This is an estimate from the weather, not a mosquito count.")


# ---------- smart reminders (ask first) ----------
def reminder_suggestion(memory, reminders, rec):
    """After a reminder is set: an outdoor plan at a bad hour, or watering when rain is coming -> a question."""
    global _pending
    if not rec or not memory.get_setting("home_city"):
        return None
    rid, text, due_ts, rep = rec
    due = datetime.fromtimestamp(due_ts)
    outdoor, water = bool(OUTDOOR.search(text)), bool(WATERING.search(text))
    if not (outdoor or water) or due - datetime.now() > timedelta(hours=36):
        return None
    try:
        data = env.get_data(memory.get_setting("home_city"))
    except requests.RequestException:
        return None
    if not data:
        return None
    air = env._air_hours(data)
    hours = env._hours(data["wx"].get("hourly"), env._local_now(data), ahead=40)
    at = next((v for dt, v in hours if dt == due.replace(minute=0, second=0, microsecond=0)), None)
    if at is None:
        return None
    if water:
        wet = [dt for dt, v in hours if dt.date() == due.date() and (v.get("precipitation_probability") or 0) >= 60]
        if not wet:
            return None
        _pending = {"kind": "skip", "rid": rid, "at": time.time()}
        return (f"By the way, rain is likely around {env._clock(wet[0])} {env._day_word(wet[0], env._local_now(data))}. "
                "Want me to skip watering that day?")
    aqi = env._hour_aqi(air, due.replace(minute=0, second=0, microsecond=0))
    t = at.get("apparent_temperature", at.get("temperature_2m"))
    rain = at.get("precipitation_probability") or 0
    bad = []
    if aqi is not None and aqi > (100 if sensitive(memory) else 200):
        bad.append(f"the air looks poor (AQI around {aqi})")
    if t is not None and t >= 36:
        bad.append(f"it will feel like {round(t)} degrees")
    if rain >= 60:
        bad.append(f"rain is likely ({rain} percent)")
    if not bad:
        return None
    same_day = [(env._score(dt, v, air)[0], dt) for dt, v in hours
                if dt.date() == due.date() and 5 <= dt.hour <= 20 and dt > datetime.now()]
    if not same_day:
        return None
    best = min(same_day)[1]
    if abs((best - due).total_seconds()) < 3600:
        return None
    new_due = best.replace(minute=due.minute)
    _pending = {"kind": "move", "rid": rid, "due": new_due, "at": time.time()}
    return (f"By the way, at {env._clock(due)} " + " and ".join(bad) + f". {env._clock(best)} looks better. "
            "Want me to move it?")


def handle_pending(text, reminders):
    global _pending
    if not _pending or time.time() - _pending["at"] > 120:
        _pending = None
        return None
    lower = text.lower().strip(" .!?,")
    if not (YES.match(lower) or NO.match(lower)):
        return None
    p, _pending = _pending, None
    if NO.match(lower):
        return "Okay, I'll keep it as it is."
    row = next((r for r in reminders.active() if r[0] == p["rid"]), None)
    if not row:
        return "That reminder isn't there any more."
    if p["kind"] == "move":
        reminders.set_due(p["rid"], p["due"])
        return f"Done. I moved it to {env._clock(p['due'])}."
    if row[3]:                                            # a repeating reminder: skip just that day
        import reminders as _r
        due = datetime.fromtimestamp(row[2])
        reminders.set_due(p["rid"], _r.next_due(due, row[3], now=due))
        return "Done. I'll skip it that day; it stays on for the next days."
    reminders.cancel_id(p["rid"])
    return "Done. I cancelled it, since the rain will water the plants."


# ---------- the questions ----------
def handle(text, memory, offline=False, guest=False):
    """Step-2 questions first; otherwise step 1 (environment.handle_env), with asthma-aware notes."""
    lower = text.lower().strip(" .!?,")
    if not guest and UNSET_SENSITIVE.search(lower):
        memory.set_setting("env_sensitive", "off")
        return {"say": "Okay, I've turned off asthma care. I'll give the normal air advice.", "card": None}
    if not guest and SET_SENSITIVE.match(lower):
        memory.set_setting("env_sensitive", "on")
        return {"say": "Thanks for telling me. I'll warn you earlier when the air isn't good, and suggest cleaner times "
                       "to go out. This stays on this device. You can say: turn off asthma care.", "card": None}
    if not guest and WARN_ON.search(lower):
        memory.set_setting("env_warnings", "on")
        return {"say": "Weather warnings are on. Every few hours I'll check the forecast for your city and warn you "
                       "about possible storms, very heavy rain, heat waves or very bad air. Only the city's location "
                       "goes online. Say turn off weather warnings to stop.", "card": None}
    if not guest and WARN_OFF.search(lower):
        memory.set_setting("env_warnings", "off")
        return {"say": "Okay, weather warnings are off. You can still ask me any time: any weather warnings?",
                "card": None}
    kind = "mosq" if MOSQ_Q.search(lower) else "warn" if WARN_Q.search(lower) else None
    if kind:
        if offline:
            return {"say": "Offline mode is on, so I can't check the forecast. Say offline mode off, and I'll check it.",
                    "card": None}
        city = env._city(text, memory.get_setting("home_city"))
        if not city:
            return {"say": "Which city should I check? You can say: my city is Pune.", "card": None}
        try:
            if kind == "mosq":
                return {"say": mosquito_answer(city), "card": None}
            data = env.get_data(city)
            if not data:
                return {"say": f"I couldn't find a place called {city}.", "card": None}
            ws = warnings(data, memory)
            if not ws:
                extra = "" if memory.get_setting("env_warnings") == "on" else \
                    " If you like, say: turn on weather warnings, and I'll keep an eye on it."
                return {"say": f"No severe weather is expected in {data['label']} in the next two days.{extra}",
                        "card": env.card(data)}
            return {"say": _warning_text(ws), "card": env.card(data)}
        except requests.RequestException:
            return {"say": "I can't reach the internet right now, so I can't check that.", "card": None}
        except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
            print(f"   (environment data looked different than expected: {e!r})")
            return {"say": "I got the forecast, but couldn't read it properly. Please try again later.", "card": None}
    res = env.handle_env(text, memory, offline=offline)
    if not res or guest or offline or not sensitive(memory):
        return res
    kind1 = ("aqi" if env.AQI_Q.search(lower) else "out" if env.OUT_Q.search(lower) else
             "best" if env.BEST_Q.search(lower) else None)
    if kind1:
        try:
            data = env.get_data(env._city(text, memory.get_setting("home_city")))
            a = env.current_aqi(data) if data else None
        except requests.RequestException:
            a = None
        if a and a[0] is not None and a[1] >= 2:
            res["say"] = res["say"].replace(" The air is only moderately clean, so keep hard exercise short if you have "
                                            "asthma.", "")
            res["say"] += (" Since you told me about your asthma, keep hard exercise outside short today, "
                           "and keep your medicine with you.")
    return res
