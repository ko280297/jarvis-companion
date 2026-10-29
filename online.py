import json
import re
import time
import xml.etree.ElementTree as ET
import requests

LOG_FILE = "online_log.jsonl"
TIMEOUT = 5
NEWS_FEED = "https://feeds.bbci.co.uk/news/world/rss.xml"                 # world news
# fix 101: India and your city. Google News' public feeds; for a city, only the city's name is sent (and logged).
NEWS_INDIA = "https://news.google.com/rss"
NEWS_SEARCH = "https://news.google.com/rss/search"
NEWS_LANG = {"en": {"hl": "en-IN", "gl": "IN", "ceid": "IN:en"}, "hi": {"hl": "hi", "gl": "IN", "ceid": "IN:hi"}}
CACHE_SECONDS = 10 * 60      # weather is reused for 10 minutes: faster, and less leaves the device
_geo_cache = {}              # city -> place (RAM only; cities don't move)
_weather_cache = {}          # (city, day) -> (time, answer)
LAST_CALL = {"ok": 0.0, "failed": 0.0}   # when an online call last worked / failed (for the screen icon)
SAVED_USE = {"t": 0.0, "age": 0.0, "what": ""}   # fix 85: last time saved online data was reused


WEATHER_Q = re.compile(r"\b(weather|temperature|rain|forecast|cloudy|clouds?|sunny|humid|humidity|windy|foggy)\b")
NEWS_Q = re.compile(r"\b(news|headlines)\b")

WEATHER_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    80: "rain showers", 81: "rain showers", 82: "heavy rain showers",
    95: "a thunderstorm", 96: "a thunderstorm with hail", 99: "a thunderstorm with hail",
}


def _log(tool, sent, url, status):
    """Every outbound attempt is recorded: what left the device, where, and the result."""
    host = url.split("/")[2]
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "tool": tool, "sent": sent, "host": host, "status": status}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    if status in LAST_CALL:
        LAST_CALL[status] = time.time()
    icon = "✅" if status == "ok" else "❌"
    print(f"🌐 ONLINE {icon} → {host} | sent: {sent!r} | {status}")


def _get(tool, sent, url, params=None):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        _log(tool, sent, url, "ok")
        return r
    except requests.RequestException:
        _log(tool, sent, url, "failed")
        raise


NOT_CITIES = {"rain", "today", "tomorrow", "now", "right now", "this week", "the week"}


def _extract_city(t, home_city):
    m = re.search(
        r"\b(?:in|at|for|of)\s+([a-z][a-z\s]*?)"
        r"(?:\s+(?:today|tomorrow|now|right now|this week))?[?.!]*$", t)
    if m and m.group(1).strip() not in NOT_CITIES:
        return near_home(m.group(1).strip().title(), home_city)
    return home_city


def near_home(city, home_city):
    """fix 104: 'Hardui' (a mishearing / a translation) is your own 'Hardoi', not another town far away."""
    import difflib
    if home_city and city.lower() != home_city.lower() and \
            difflib.SequenceMatcher(None, city.lower(), home_city.lower()).ratio() >= 0.75:
        print(f"   ('{city}' sounds like your city, so using {home_city})")
        return home_city
    return city


def _geocode(city):
    """Pick the most populated match, and retry without spaces ('Shah Jahanpur' -> 'Shahjahanpur')."""
    if city.lower() in _geo_cache:
        return _geo_cache[city.lower()]
    for name in dict.fromkeys([city, city.replace(" ", "")]):
        geo = _get("weather", name,
                   "https://geocoding-api.open-meteo.com/v1/search",
                   {"name": name, "count": 10}).json()
        results = geo.get("results")
        if results:
            place = max(results, key=lambda r: r.get("population") or 0)
            _geo_cache[city.lower()] = place
            return place
    return None


def get_weather(city, when="today"):
    key = (city.lower(), when)
    hit = _weather_cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        print("   (weather from the last 10 minutes: nothing sent)")
        SAVED_USE.update(t=time.time(), age=time.time() - hit[0], what="weather")   # fix 85
        return hit[1]

    place = _geocode(city)
    if place is None:
        return f"I couldn't find a place called {city}."
    label = f"{place['name']}, {place['country']}" if place.get("country") else place["name"]
    label = _plain(label)                                         # fix 102: "Hardoī" -> "Hardoi" (said right)
    lat, lon = place["latitude"], place["longitude"]

    data = _get("weather", f"{lat:.2f},{lon:.2f}",
                "https://api.open-meteo.com/v1/forecast", {
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "hourly": "temperature_2m,precipitation_probability,cloud_cover,weather_code",   # fix 101: tonight
                    "timezone": "auto", "forecast_days": 2,
                }).json()
    if when == "tonight":                                           # fix 101: "will it be cloudy tonight?"
        answer = _tonight(data, label)
        _weather_cache[key] = (time.time(), answer)
        return answer

    day = data["daily"]
    i = 1 if when == "tomorrow" else 0
    high, low = round(day["temperature_2m_max"][i]), round(day["temperature_2m_min"][i])

    if when == "tomorrow":
        desc = WEATHER_CODES.get(day["weather_code"][i], "mixed conditions")
        answer = f"Tomorrow in {label}: {desc}, with a high of {high} and a low of {low} degrees."
    else:
        cur = data["current"]
        desc = WEATHER_CODES.get(cur["weather_code"], "mixed conditions")
        answer = (f"In {label} it's {round(cur['temperature_2m'])} degrees right now, {desc}. "
                  f"Today's high is {high} and the low is {low}.")

    rain = day["precipitation_probability_max"][i]
    if rain is not None:
        answer += f" Chance of rain is {rain} percent."
    _weather_cache[key] = (time.time(), answer)
    return answer

def _tonight(data, label):
    """Tonight = from now (or 6 PM) until 6 AM, from the hourly forecast."""
    h = data.get("hourly") or {}
    times = h.get("time") or []
    now = (data.get("current") or {}).get("time") or time.strftime("%Y-%m-%dT%H:%M")
    start = max(now[:13], now[:10] + "T18")                       # "2026-10-29T18"
    end = _next_day(now[:10]) + "T06"
    idx = [i for i, t in enumerate(times) if start <= t[:13] <= end]
    if not idx:
        return f"I couldn't get tonight's forecast for {label}."
    temps = [h["temperature_2m"][i] for i in idx if h["temperature_2m"][i] is not None]
    clouds = [h["cloud_cover"][i] for i in idx if h["cloud_cover"][i] is not None]
    rain = max((h["precipitation_probability"][i] or 0) for i in idx)
    codes = [h["weather_code"][i] for i in idx]
    cloud = sum(clouds) / len(clouds) if clouds else None
    sky = ("clear" if cloud is not None and cloud < 25 else "partly cloudy" if cloud is not None and cloud < 60
           else "mostly cloudy" if cloud is not None and cloud < 90 else "cloudy all night")
    if any(c in (61, 63, 65, 80, 81, 82, 95, 96, 99) for c in codes):
        sky += ", with some rain"
    answer = f"Tonight in {label}: {sky}, between {round(min(temps))} and {round(max(temps))} degrees."
    answer += (f" Chance of rain is {rain} percent." if rain >= 20 else " No rain expected.")
    return answer


def _next_day(day):
    import datetime as _dt
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=1)).isoformat()


def _plain(name):
    """Drop accent marks the voice can't say: 'Hardoī' -> 'Hardoi'."""
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))


def check_internet():
    """Asked directly 'are you online?': one tiny request with nothing personal in it, logged like every call."""
    try:
        _get("check", "(nothing personal: just checking the connection)",
             "https://api.open-meteo.com/v1/forecast", {"latitude": 0, "longitude": 0, "current": "temperature_2m"})
        return True
    except requests.RequestException:
        return False

NOT_A_PLACE = {"the", "latest", "today", "todays", "today's", "some", "any", "me", "top", "good", "more", "fresh",
               "breaking", "big", "main", "important", "this", "tell", "give", "read", "what", "whats", "what's",
               "us", "all", "recent", "new", "current", "local", "world", "india", "indian", "national",
               "international", "global", "my", "city", "town", "hindi", "english", "s"}


def news_place(t, home_city=None):
    """'news from Lucknow' -> 'Lucknow';  'India news' -> 'india';  'world news' -> 'world';  'news' -> 'india'."""
    t = t.lower()
    if re.search(r"\b(?:world|international|global)\b", t):
        return "world"
    if re.search(r"\b(?:local|my city|my town|near me|around me|here)\b", t):
        return home_city or "india"
    for rx in (r"\b(?:news|headlines)\s+(?:from|in|of|about|for|at)\s+(?:the\s+)?([a-z][a-z .'-]*?)(?:\s+(?:today|right now|now|please))?[?.!]*$",
               r"\b(?:from|in|of|for)\s+([a-z][a-z .'-]*?)\s+(?:latest |today'?s |top |local )?(?:news|headlines)",
               r"\b([a-z]+(?: [a-z]+)?)(?:'s)?\s+(?:latest |local |top |fresh )?(?:news|headlines)\b"):
        m = re.search(rx, t)
        if m:
            if re.search(r"\b(?:my|mine|our)\b", m.group(1)):
                return "__private__"                          # "news about my diary": never sent online
            words = [w for w in re.findall(r"[a-z]+", m.group(1)) if w not in NOT_A_PLACE]
            if words and len(words) <= 3:
                return " ".join(words).title()
    return "india"


def _titles(r, n):
    root = ET.fromstring(r.content)
    out = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        for _ in range(2):                                        # fix 104: "… - Lucknow News - Dainik Bhaskar"
            title = re.sub(r"\s+-\s+[^-]{2,60}$", "", title).strip()                       # " - Times of India"
            title = re.sub(r"\s*\|\s*[^|]{2,40}$", "", title).strip()                      # fix 102: " | India News"
        if title and title not in out:
            out.append(title)
        if len(out) == n:
            break
    return out


def get_news(n=3, where="world", lang="en"):
    """fix 101: world (BBC), India (Google News India), or a city ('Lucknow': only that name is sent)."""
    hindi = lang == "hi"
    if where == "world":
        r = _get("news", "(nothing - public headlines only)", NEWS_FEED)
        head = "Top world headlines."                                  # (translated in Hindi mode)
    elif where == "india":
        r = _get("news", "(nothing - public headlines for India only)", NEWS_INDIA, NEWS_LANG[lang])
        head = "आज की मुख्य ख़बरें।" if hindi else "Top headlines in India."
    else:
        if where == "__private__" or not CITY_OK.match(where):
            _log_blocked("news", where)
            return "That sounds personal, so I won't send it online. I can get news for a place, like: news from Lucknow."
        r = _get("news", where, NEWS_SEARCH, dict(NEWS_LANG[lang], q=where))
        head = f"{_hindi_name(where)} की ताज़ा ख़बरें।" if hindi else f"Latest news about {where}."
    titles = _titles(r, n)
    if not titles:
        return f"I couldn't find news for {where} right now."
    if hindi and where != "world":                               # Hindi headlines, written by people: no translation
        return head + " " + " ".join(t if t[-1:] in "।?!" else t.rstrip(".") + "।" for t in titles)   # fix 104
    return head + " " + " ".join(t if t[-1:] in ".?!" else t + "." for t in titles)   # fix 102: no "?."


def _hindi_name(name):
    """fix 104: 'Lucknow' -> 'लखनऊ' for the Hindi heading (the on-device translator; no internet)."""
    import sys
    h = sys.modules.get("hindi")
    try:
        out = h._parts["to_hi"]([name])[0].strip(" ।.")
        return out if out and h.DEV.search(out) else name
    except Exception:
        return name


def _lang():
    """Hindi mode on? Then Indian news comes in Hindi straight away (fix 101)."""
    import sys
    h = sys.modules.get("hindi")
    try:
        return "hi" if h and h.on() else "en"
    except Exception:
        return "en"


def answer_online_question(text, home_city=None):
    """The ONLY place the device touches the internet. Returns None if not an online question."""
    t = text.lower()
    try:
        if WEATHER_Q.search(t):
            city = _extract_city(t, home_city)
            if not city:
                return "Which city should I check? You can say: my city is Pune."
            when = ("tomorrow" if "tomorrow" in t else
                    "tonight" if re.search(r"\b(?:tonight|night|this evening)\b", t) else "today")   # fix 101
            return get_weather(city, when)
        if NEWS_Q.search(t):
            return get_news(where=news_place(t, home_city), lang=_lang())
    except requests.RequestException:
        return ("I can't reach the internet right now, so I can't check that. "
                "Everything else still works offline.")
    return None


CITY_OK = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,39}$")


def _log_blocked(tool, attempted):
    """Record requests the LLM tried but the code refused. Nothing leaves the device."""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "tool": tool, "sent": None, "attempted": attempted, "status": "blocked"}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"🛡️ BLOCKED → {tool} | attempted: {attempted!r} | nothing sent")


def run_tool_call(name, args, home_city=None):
    """LLM decides, code enforces: only whitelisted tools, only validated arguments."""
    try:
        if name == "get_weather":
            city = (args.get("city") or home_city or "").strip()
            day = args.get("day") if args.get("day") in ("today", "tomorrow") else "today"
            if not city:
                return "Which city should I check? You can say: my city is Pune."
            if not CITY_OK.match(city) or len(city.split()) > 3:
                _log_blocked(name, city)
                return "I blocked that request because it didn't look like a city name."
            return get_weather(near_home(city.title(), home_city), day)                 # fix 104

        if name == "get_news":
            return get_news(where="india", lang=_lang())                # fix 101: India by default

    except requests.RequestException:
        return ("I can't reach the internet right now, so I can't check that. "
                "Everything else still works offline.")

    _log_blocked(name, str(args))    # unknown tool: refuse
    return None


def check_place(city):
    """Is this a real place? Returns 'Name, Country' if found, None if not found, '' if offline.
    Only the city name leaves the device, and it's logged like every other call."""
    try:
        place = _geocode(city)
    except requests.RequestException:
        return ""
    if place is None:
        return None
    return f"{place['name']}, {place['country']}" if place.get("country") else place["name"]


if __name__ == "__main__":
    tests = [
        ("What's the weather?", None),              # no home city set -> should ask
        ("What's the weather?", "Pune"),            # home city set -> uses it
        ("What's the weather?", "Pune"),            # again -> from the 10-minute cache, nothing sent
        ("What's the weather in Mumbai?", None),    # city spoken -> uses Mumbai
        ("Tell me the news.", None),
        ("When is my meeting with Rahul?", None),   # not online -> None
    ]
    for q, home in tests:
        print(f"\n{q}  [home city: {home}]\n-> {answer_online_question(q, home)}")