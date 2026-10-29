"""Environment, step 1 (fix 55): decisions, not just data.
AQI (India's CPCB scale, worked out on the device from PM2.5 / PM10), rain timing, UV and heat,
"is it a good time to go out?", the best hour for a walk, drying clothes, opening the windows.

Only the city's coordinates leave the device (Open-Meteo forecast + air-quality APIs), every call is logged,
answers are kept for 30 minutes, and offline mode blocks it all. The numbers come from code, never from the AI."""
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import online

CACHE_SECONDS = 30 * 60
_cache = {}            # city -> (time, data)

# ---------- India's National AQI (CPCB): sub-index from PM2.5 and PM10 (µg/m³, 24-hour average) ----------
_BANDS = [(0, 50), (51, 100), (101, 200), (201, 300), (301, 400), (401, 500)]
_PM25 = [(0, 30), (31, 60), (61, 90), (91, 120), (121, 250), (251, 380)]
_PM10 = [(0, 50), (51, 100), (101, 250), (251, 350), (351, 430), (431, 510)]
CATEGORIES = ["good", "satisfactory", "moderately polluted", "poor", "very poor", "severe"]
ADVICE = [
    "The air is clean. A great time to be outside.",
    "The air is fine for most people. Very sensitive people may feel minor breathing discomfort.",
    "People with asthma, heart or lung problems, children and older adults should avoid long, hard exercise outside.",
    "Most people may feel breathing discomfort outside for long. Keep outdoor time short, and wear a mask if you go out.",
    "Avoid being outside for long. Keep windows closed and wear a good mask (N95) if you must go out.",
    "The air is hazardous. Stay indoors with windows closed. Everyone should avoid going out.",
]


def _sub_index(c, table):
    if c is None:
        return None
    for (lo, hi), (ilo, ihi) in zip(table, _BANDS):
        if c <= hi:
            return round(ilo + (ihi - ilo) * (max(c, lo) - lo) / (hi - lo))
    return 500


def india_aqi(pm25, pm10):
    """(aqi, category index, main pollutant)  -- the higher sub-index wins, like CPCB."""
    a, b = _sub_index(pm25, _PM25), _sub_index(pm10, _PM10)
    if a is None and b is None:
        return None, None, None
    aqi, main = max((a or 0, "PM2.5"), (b or 0, "PM10"))
    cat = next(i for i, (lo, hi) in enumerate(_BANDS) if aqi <= hi)
    return aqi, cat, main


def uv_words(uv):
    return ("low" if uv < 3 else "moderate" if uv < 6 else "high" if uv < 8 else "very high" if uv < 11
            else "extreme")


# ---------- questions ----------
AQI_Q = re.compile(r"\b(?:aqi|a\.? ?q\.? ?i|air quality|pollution|polluted|smog|how(?:'s| is) the air|is the air"
                   r"|air (?:clean|bad|good|safe|today))\b", re.I)
RAIN_Q = re.compile(r"\b(?:when (?:will|is) it (?:going to )?rain|will it rain|is it going to rain|is rain (?:coming|expected)"
                    r"|rain (?:today|tomorrow|later|tonight)|(?:do|should) i (?:need|take|carry) (?:an |my )?umbrella"
                    r"|any rain"
                    r"|when (?:will|would|is|does|do) (?:the )?rains? (?:happen|come|start|fall|be)|when (?:will|is) it raining"
                    r"|next rain|rain\w* (?:again|next|this week|soon|in the next)|(?:chance|chances) of rain"
                    r"|(?:is|will) there (?:be )?(?:any )?rain|baarish)\b", re.I)   # fix 88
UV_Q = re.compile(r"\b(?:uv|u\.v\.|sunscreen|sun ?burn|sun (?:is )?(?:strong|harsh))\b", re.I)
OUT_Q = re.compile(r"\b(?:(?:good|right|safe|okay|ok) (?:time |day )?to go (?:out|outside)|should i go (?:out|outside)"
                   r"|can i go (?:out|outside)|is it (?:safe|okay|ok|good) (?:to go )?(?:out|outside)"
                   r"|go out(?:side)? (?:now|today))\b", re.I)
BEST_Q = re.compile(r"\b(?:best time (?:for (?:a |my )?|to (?:go for (?:a )?)?)"
                    r"(walk|run|jog|exercise|workout|cycle|cycling|play|walking|running|jogging|go out|outdoor)"
                    r"|(?:good|right) time (?:for (?:a |my )?|to go for (?:a )?)(walk|run|jog|exercise|workout|cycle))", re.I)
DRY_Q = re.compile(r"\b(?:dry (?:my |the )?(?:clothes|laundry|washing)|drying (?:clothes|laundry)"
                   r"|(?:good|right) day (?:for|to do) (?:laundry|washing)|(?:wash|washing) (?:clothes|laundry) today)\b", re.I)
WINDOW_Q = re.compile(r"\b(?:open|close|keep)\b.*\bwindows?\b|\bwindows? (?:open|closed)\b", re.I)


def _city(text, home_city):
    m = re.search(r"\b(?:in|at|for|of)\s+([A-Za-z][A-Za-z ]{1,30}?)\s*(?:today|tomorrow|now|right now)?[?.!]*$", text)
    if m and m.group(1).lower().strip() not in {"rain", "today", "tomorrow", "now", "a walk", "a run", "my city",
                                                 "the day", "the morning", "the evening", "laundry", "walk", "run"}:
        word = m.group(1).strip()
        if not re.search(r"\b(?:walk|run|jog|exercise|workout|clothes|laundry|window|outside|out|sunscreen)\b", word, re.I):
            return word.title()
    return home_city


# ---------- data (one forecast call + one air-quality call, kept 30 minutes) ----------
def get_data(city):
    hit = _cache.get(city.lower())
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        print("   (air and forecast from the last 30 minutes: nothing sent)")
        try:                                                     # fix 85: for the phone's tag
            online.SAVED_USE.update(t=time.time(), age=time.time() - hit[0], what="weather and air data")
        except AttributeError:
            pass
        return hit[1]
    place = online._geocode(city)
    if place is None:
        return None
    lat, lon = place["latitude"], place["longitude"]
    sent = f"{lat:.2f},{lon:.2f}"
    wx = online._get("environment", sent, "https://api.open-meteo.com/v1/forecast", {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,  # fix 88
        "current": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,"
                   "wind_speed_10m,uv_index",
        "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation_probability,precipitation,"
                  "weather_code,wind_speed_10m,wind_gusts_10m,uv_index",
    }).json()
    try:
        air = online._get("environment", sent, "https://air-quality-api.open-meteo.com/v1/air-quality", {
            "latitude": lat, "longitude": lon, "timezone": "auto", "past_days": 1, "forecast_days": 2,
            "hourly": "pm2_5,pm10",
        }).json()
    except requests.RequestException:
        air = None                                         # the weather part still works
    label = place["name"]
    data = {"label": label, "wx": wx, "air": air, "offset": wx.get("utc_offset_seconds", 0)}
    _cache[city.lower()] = (time.time(), data)
    return data


def _local_now(data):
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=data["offset"])


def _hours(block, now, ahead=36, back=0):
    """[(datetime, {var: value})] from an Open-Meteo 'hourly' block, from now-back to now+ahead hours."""
    if not block or "time" not in block:
        return []
    out = []
    keys = [k for k in block if k != "time"]
    for i, t in enumerate(block["time"]):
        dt = datetime.fromisoformat(t)
        if now - timedelta(hours=back) <= dt + timedelta(minutes=59) and dt <= now + timedelta(hours=ahead):
            out.append((dt, {k: (block[k][i] if i < len(block[k]) else None) for k in keys}))
    return out


def current_aqi(data):
    """AQI from the average of the last 24 hours (how CPCB does it for PM)."""
    if not data.get("air"):
        return None
    now = _local_now(data)
    past = [(dt, v) for dt, v in _hours(data["air"].get("hourly"), now, ahead=0, back=24) if dt <= now]
    pm25 = [v["pm2_5"] for _, v in past if v.get("pm2_5") is not None]
    pm10 = [v["pm10"] for _, v in past if v.get("pm10") is not None]
    if not pm25 and not pm10:
        return None
    return india_aqi(sum(pm25) / len(pm25) if pm25 else None, sum(pm10) / len(pm10) if pm10 else None)


def _hour_aqi(air_hours, dt):
    """A rough hourly AQI (single-hour PM, not the 24-hour average): only used to compare hours."""
    v = air_hours.get(dt.replace(minute=0))
    if not v:
        return None
    return india_aqi(v.get("pm2_5"), v.get("pm10"))[0]


def _clock(dt):
    return dt.strftime("%I %p").lstrip("0")


def _day_word(dt, now):
    return "today" if dt.date() == now.date() else "tomorrow" if dt.date() == now.date() + timedelta(days=1) \
        else f"on {dt:%A}"


# ---------- answers ----------
def aqi_answer(data):
    a = current_aqi(data)
    if not a or a[0] is None:
        return "I couldn't get the air quality right now."
    aqi, cat, main = a
    return (f"Air quality in {data['label']}: AQI {aqi}, {CATEGORIES[cat]}"
            + (f", mostly from {main}" if aqi > 100 else "") + f". {ADVICE[cat]}")


def rain_answer(data, text):
    now = _local_now(data)
    hours = _hours(data["wx"].get("hourly"), now, ahead=36)
    if "tomorrow" in text.lower():
        hours = [h for h in hours if h[0].date() == now.date() + timedelta(days=1)]
        span = "tomorrow"
    elif re.search(r"\btoday|tonight\b", text.lower()):
        hours = [h for h in hours if h[0].date() == now.date()]
        span = "today"
    else:
        span = "in the next day and a half"
    wet = [(dt, v) for dt, v in hours if (v.get("precipitation_probability") or 0) >= 50]
    if not hours:
        return "I couldn't get the rain forecast right now."
    if not wet:
        top = max((v.get("precipitation_probability") or 0) for _, v in hours)
        if span == "in the next day and a half":                       # fix 88: look further ahead
            week = _hours(data["wx"].get("hourly"), now, ahead=168)
            later = [(dt, v) for dt, v in week if (v.get("precipitation_probability") or 0) >= 50]
            if later:
                dt, v = later[0]
                return (f"No rain in the next day and a half in {data['label']}. The next likely rain is "
                        f"{_day_word(dt, now)} around {_clock(dt)}, {v['precipitation_probability']} percent chance.")
            if week:
                dt, v = max(week, key=lambda h: h[1].get("precipitation_probability") or 0)
                p = v.get("precipitation_probability") or 0
                return (f"No rain is expected in the next 7 days in {data['label']}."
                        + (f" The best chance is {_day_word(dt, now)}, only {p} percent." if p >= 10 else ""))
        return f"No rain expected {span} in {data['label']}. The highest chance is {top} percent."
    first, v = wet[0]
    peak = max(wet, key=lambda h: h[1].get("precipitation_probability") or 0)
    mm = sum((v2.get("precipitation") or 0) for _, v2 in hours)
    heavy = " It could be heavy, so plan for it." if mm >= 20 else ""
    when = "within the hour" if first <= now + timedelta(minutes=59) else f"around {_clock(first)} {_day_word(first, now)}"
    return (f"Rain is likely {when} in {data['label']}, with the highest chance, "
            f"{peak[1]['precipitation_probability']} percent, around {_clock(peak[0])}. Carry an umbrella.{heavy}")


def uv_answer(data):
    now = _local_now(data)
    today = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=24) if dt.date() == now.date()]
    if not today or (now.hour >= 16 and max((v.get("uv_index") or 0) for _, v in today) < 1):
        today = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=36)
                 if dt.date() == now.date() + timedelta(days=1)]
    if not today:
        today = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=36)][:24]
    if not today:
        return "I couldn't get the UV forecast right now."
    peak = max(today, key=lambda h: h[1].get("uv_index") or 0)
    uv = round(peak[1].get("uv_index") or 0)
    if uv < 3:
        return f"UV is low {_day_word(peak[0], now)}, at most {uv}. No sunscreen needed for a short time outside."
    strong = [dt for dt, v in today if (v.get("uv_index") or 0) >= 6]
    span = f" from {_clock(strong[0])} to {_clock(strong[-1] + timedelta(hours=1))}" if strong else ""
    return (f"UV goes up to {uv}, {uv_words(uv)}, around {_clock(peak[0])} {_day_word(peak[0], now)}. "
            + (f"It's strongest{span}: use sunscreen, a cap and stay in the shade." if strong
               else "Sunscreen is a good idea if you're out for long."))


def _score(dt, v, air_hours):
    """Lower is better: air, feels-like temperature (comfortable is 15-26 degrees), rain and strong sun.
    Fix 56: heat counts more (an afternoon at 30 degrees is hot and sweaty), and so does strong sun."""
    s = 0.0
    aqi = _hour_aqi(air_hours, dt)
    if aqi is not None:
        s += max(0, aqi - 50) / 10
    t = v.get("apparent_temperature") if v.get("apparent_temperature") is not None else v.get("temperature_2m")
    if t is not None:
        s += max(0, t - 26) * 2 + max(0, 15 - t) * 1.5
    s += (v.get("precipitation_probability") or 0) / 10
    s += max(0, (v.get("uv_index") or 0) - 3) * 1.2
    return s, aqi, t


def _air_hours(data):
    if not data.get("air"):
        return {}
    blk = data["air"].get("hourly") or {}
    return {datetime.fromisoformat(t): {"pm2_5": (blk.get("pm2_5") or [None] * 9999)[i],
                                        "pm10": (blk.get("pm10") or [None] * 9999)[i]}
            for i, t in enumerate(blk.get("time", []))}


def _why(aqi, t, v):
    bits = []
    if t is not None:
        bits.append(f"{round(t)} degrees")
    if aqi is not None:
        bits.append("good air" if aqi <= 100 else f"AQI around {aqi}")
    if (v.get("precipitation_probability") or 0) < 30:
        bits.append("no rain")
    return ", ".join(bits)


def best_time_answer(data, what="a walk"):
    """Fix 56: the best hour, plus one different option (e.g. a cool morning vs a cleaner evening)."""
    now = _local_now(data)
    air = _air_hours(data)
    slots = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=30)
             if dt >= now.replace(minute=0) and 5 <= dt.hour <= 20]
    if not slots:
        return "I couldn't get the forecast right now."
    scored = sorted(((_score(dt, v, air), dt, v) for dt, v in slots), key=lambda x: (x[0][0], x[1]))
    (s, aqi, t), dt, v = scored[0]
    if aqi is not None and aqi > 200:
        return (f"The air is poor all day, even at its best, around {_clock(dt)}. An indoor workout is a better idea today.")
    reply = (f"The best time for {what} is {_clock(dt)} to {_clock(dt + timedelta(hours=1))} {_day_word(dt, now)}: "
             + _why(aqi, t, v) + ".")
    other = next(((sc, d2, v2) for sc, d2, v2 in scored[1:]
                  if abs(d2.hour - dt.hour) >= 4 and sc[0] <= s + 5
                  and (sc[1] is None or sc[1] <= 200)), None)
    if other:
        (s2, aqi2, t2), d2, v2 = other
        reply += (f" Another good time: {_clock(d2)} to {_clock(d2 + timedelta(hours=1))} {_day_word(d2, now)}, "
                  + _why(aqi2, t2, v2) + ".")
    return reply


def go_out_answer(data):
    now = _local_now(data)
    cur = data["wx"].get("current") or {}
    soon = _hours(data["wx"].get("hourly"), now, ahead=2)
    rain = max([(v.get("precipitation_probability") or 0) for _, v in soon] or [0])
    a = current_aqi(data)
    aqi, cat = (a[0], a[1]) if a and a[0] is not None else (None, None)
    t = cur.get("apparent_temperature", cur.get("temperature_2m"))
    uv = cur.get("uv_index") or 0
    problems = []
    if cat is not None and cat >= 3:
        problems.append(f"the air is {CATEGORIES[cat]} (AQI {aqi})")
    if t is not None and t >= 38:
        problems.append(f"it feels like {round(t)} degrees")
    if rain >= 60:
        problems.append(f"rain is likely soon ({rain} percent)")
    if uv >= 8:
        problems.append(f"the sun is very strong (UV {round(uv)})")
    if not problems:
        good = [x for x in ((f"AQI {aqi}" if aqi is not None else ""), (f"{round(t)} degrees" if t is not None else ""),
                            "no rain soon" if rain < 30 else f"{rain} percent chance of rain") if x]
        note = (" The air is only moderately clean, so keep hard exercise short if you have asthma."
                if cat == 2 else "")
        return "Yes, it's a good time to go out: " + ", ".join(good) + "." + note
    return "Better not right now: " + " and ".join(problems) + ". " + best_time_answer(data, "going out")


def dry_answer(data):
    now = _local_now(data)
    day = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=12) if 8 <= dt.hour <= 18]
    if not day:
        return "It's late in the day for drying clothes outside. Tomorrow morning is a better time."
    rain = [(dt, v) for dt, v in day if (v.get("precipitation_probability") or 0) >= 50]
    hum = sum((v.get("relative_humidity_2m") or 60) for _, v in day) / len(day)
    wind = sum((v.get("wind_speed_10m") or 0) for _, v in day) / len(day)
    if rain:
        return (f"Not a good time to dry clothes outside: rain is likely around {_clock(rain[0][0])} "
                f"{_day_word(rain[0][0], now)}. "
                "Dry them indoors, or wait.")
    sunny = [dt for dt, v in day if (v.get("weather_code") or 0) <= 2]
    when = (f" Best from {_clock(sunny[0])} to {_clock(sunny[-1] + timedelta(hours=1))} {_day_word(sunny[0], now)}."
            if sunny else "")
    if hum > 80:
        return f"No rain, but it's very humid ({round(hum)} percent), so clothes will dry slowly.{when}"
    return (f"Yes, a good time to dry clothes: no rain, {round(hum)} percent humidity"
            + (", and a breeze" if wind >= 8 else "") + f".{when}")


def window_answer(data):
    a = current_aqi(data)
    cur = data["wx"].get("current") or {}
    t = cur.get("temperature_2m")
    raining = (cur.get("precipitation") or 0) > 0.2
    if a and a[0] is not None and a[1] >= 3:
        return f"Better keep them closed: the air outside is {CATEGORIES[a[1]]}, AQI {a[0]}."
    if raining:
        return "It's raining right now, so maybe keep them closed for a while."
    if a and a[0] is not None and a[1] == 2:
        best = best_time_answer(data, "fresh air")
        m = re.search(r"is (\d+ [AP]M) to \d+ [AP]M (today|tomorrow|on \w+)", best)
        return (f"The air is moderately polluted, AQI {a[0]}. Open them only for a short time"
                + (f", ideally around {m.group(1)} {m.group(2)}, when it's cleaner." if m else "."))
    if t is not None and (t >= 34 or t <= 12):
        return (f"The air is fine{f' (AQI {a[0]})' if a and a[0] is not None else ''}, but it's {round(t)} degrees "
                "outside, so open them only for a short time.")
    return (f"Yes, a good time to open the windows: the air is {CATEGORIES[a[1]] if a and a[1] is not None else 'okay'}"
            + (f" (AQI {a[0]})" if a and a[0] is not None else "") + (f" and it's {round(t)} degrees." if t is not None else "."))


def card(data):
    """A small 'Air & weather' card for the screen."""
    lines = []
    a = current_aqi(data)
    if a and a[0] is not None:
        lines.append(f"AQI {a[0]} · {CATEGORIES[a[1]].capitalize()} (model estimate)")
    now = _local_now(data)
    hours = _hours(data["wx"].get("hourly"), now, ahead=24)
    wet = [dt for dt, v in hours if (v.get("precipitation_probability") or 0) >= 50]
    lines.append(f"🌧️ Rain likely around {_clock(wet[0])} {_day_word(wet[0], now)}" if wet else "🌤️ No rain expected")
    today = [v.get("uv_index") or 0 for dt, v in hours if dt.date() == now.date()]
    if today and max(today) >= 3:
        lines.append(f"☀️ UV up to {round(max(today))} ({uv_words(max(today))})")
    best = best_time_answer(data)
    m = re.search(r"is (\d+ [AP]M to \d+ [AP]M \w+)", best)
    if m:
        lines.append(f"🚶 Best time outside: {m.group(1)}")
    return {"title": f"🌿 Air & weather · {data['label']}", "lines": lines}


def day_line(memory):
    """Fix 58: one short line for "what's my day": when rain is likely today, and the air."""
    city = memory.get_setting("home_city")
    if not city:
        return None
    try:
        data = get_data(city)
        if not data:
            return None
        now = _local_now(data)
        today = [(dt, v) for dt, v in _hours(data["wx"].get("hourly"), now, ahead=24) if dt.date() == now.date()]
        wet = [dt for dt, v in today if (v.get("precipitation_probability") or 0) >= 50]
        parts = [f"Rain is likely around {_clock(wet[0])}, so carry an umbrella." if wet else "No rain expected today."]
        a = current_aqi(data)
        if a and a[0] is not None:
            parts.append(f"The air is {CATEGORIES[a[1]]}, AQI {a[0]}.")
        return " ".join(parts)
    except (requests.RequestException, KeyError, TypeError, ValueError, IndexError, AttributeError):
        return None


def handle_env(text, memory, offline=False):
    """Returns {"say": ..., "card": ...} for an environment question, otherwise None."""
    lower = text.lower()
    m_best = BEST_Q.search(lower)
    kind = ("best" if m_best else "out" if OUT_Q.search(lower) else "dry" if DRY_Q.search(lower)
            else "window" if WINDOW_Q.search(lower) else "aqi" if AQI_Q.search(lower)
            else "rain" if RAIN_Q.search(lower) else "uv" if UV_Q.search(lower) else None)
    if kind == "best" and re.match(r"(?:is|are) (?:it|now|this)\b", lower.strip()):
        kind = "out"                                       # "is it a good time for a run?" = right now?
    if not kind:
        return None
    if offline:
        return {"say": "Offline mode is on, so I can't check the air or the forecast. Say offline mode off, "
                       "and I'll check it.", "card": None}
    city = _city(text, memory.get_setting("home_city"))
    if not city:
        return {"say": "Which city should I check? You can say: my city is Pune.", "card": None}
    try:
        data = get_data(city)
    except requests.RequestException:
        return {"say": "I can't reach the internet right now, so I can't check that. Everything else still works.",
                "card": None}
    if not data:
        return {"say": f"I couldn't find a place called {city}.", "card": None}
    try:
        if kind == "best":
            what = m_best.group(1) or m_best.group(2)
            what = {"walking": "a walk", "running": "a run", "jogging": "a jog", "cycling": "cycling", "go out": "going out",
                    "outdoor": "being outside"}.get(what, "a " + what if what in ("walk", "run", "jog", "workout") else what)
            reply = best_time_answer(data, what)
        else:
            reply = {"out": go_out_answer, "dry": dry_answer, "window": window_answer, "aqi": aqi_answer,
                     "uv": uv_answer}.get(kind, lambda d: rain_answer(d, text))(data)
        return {"say": reply, "card": card(data)}
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as e:
        print(f"   (environment data looked different than expected: {e!r})")
        return {"say": "I got the forecast, but couldn't read it properly. Please try again later.", "card": None}
