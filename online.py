import json
import re
import time
import xml.etree.ElementTree as ET
import requests

LOG_FILE = "online_log.jsonl"
TIMEOUT = 5
NEWS_FEED = "https://feeds.bbci.co.uk/news/world/rss.xml"

WEATHER_Q = re.compile(r"\b(weather|temperature|rain|forecast)\b")
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
        return m.group(1).strip().title()
    return home_city

def _geocode(city):
    """Pick the most populated match, and retry without spaces ('Shah Jahanpur' -> 'Shahjahanpur')."""
    for name in dict.fromkeys([city, city.replace(" ", "")]):
        geo = _get("weather", name,
                   "https://geocoding-api.open-meteo.com/v1/search",
                   {"name": name, "count": 10}).json()
        results = geo.get("results")
        if results:
            return max(results, key=lambda r: r.get("population") or 0)
    return None

def get_weather(city, when="today"):
    place = _geocode(city)
    if place is None:
        return f"I couldn't find a place called {city}."
    label = f"{place['name']}, {place['country']}" if place.get("country") else place["name"]
    lat, lon = place["latitude"], place["longitude"]

    data = _get("weather", f"{lat:.2f},{lon:.2f}",
                "https://api.open-meteo.com/v1/forecast", {
                    "latitude": lat, "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "timezone": "auto", "forecast_days": 2,
                }).json()

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
    return answer

def get_news(n=3):
    r = _get("news", "(nothing - public headlines only)", NEWS_FEED)
    root = ET.fromstring(r.content)
    titles = [item.findtext("title") for item in root.iter("item")][:n]
    return "Top headlines. " + ". ".join(titles) + "."


def answer_online_question(text, home_city=None):
    """The ONLY place the device touches the internet. Returns None if not an online question."""
    t = text.lower()
    try:
        if WEATHER_Q.search(t):
            city = _extract_city(t, home_city)
            if not city:
                return "Which city should I check? You can say: my city is Pune."
            when = "tomorrow" if "tomorrow" in t else "today"
            return get_weather(city, when)
        if NEWS_Q.search(t):
            return get_news()
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
            return get_weather(city.title(), day)

        if name == "get_news":
            return get_news()

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
        ("What's the weather in Mumbai?", None),    # city spoken -> uses Mumbai
        ("Tell me the news.", None),
        ("When is my meeting with Rahul?", None),   # not online -> None
    ]
    for q, home in tests:
        print(f"\n{q}  [home city: {home}]\n-> {answer_online_question(q, home)}")