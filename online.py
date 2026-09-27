import json
import re
import time
import xml.etree.ElementTree as ET
import requests

DEFAULT_CITY = "Bengaluru"          # change to your city
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


def _extract_city(t):
    m = re.search(
        r"\b(?:in|at|for)\s+([a-z][a-z\s]*?)"
        r"(?:\s+(?:today|tomorrow|now|right now|this week))?[?.!]*$", t)
    return m.group(1).strip().title() if m else DEFAULT_CITY


def get_weather(city, when="today"):
    geo = _get("weather", city,
               "https://geocoding-api.open-meteo.com/v1/search",
               {"name": city, "count": 1}).json()
    if not geo.get("results"):
        return f"I couldn't find a place called {city}."
    place = geo["results"][0]
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
        answer = f"Tomorrow in {place['name']}: {desc}, with a high of {high} and a low of {low} degrees."
    else:
        cur = data["current"]
        desc = WEATHER_CODES.get(cur["weather_code"], "mixed conditions")
        answer = (f"In {place['name']} it's {round(cur['temperature_2m'])} degrees right now, {desc}. "
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


def answer_online_question(text):
    """The ONLY place the device touches the internet. Returns None if not an online question."""
    t = text.lower()
    try:
        if WEATHER_Q.search(t):
            when = "tomorrow" if "tomorrow" in t else "today"
            return get_weather(_extract_city(t), when)
        if NEWS_Q.search(t):
            return get_news()
    except requests.RequestException:
        return ("I can't reach the internet right now, so I can't check that. "
                "Everything else still works offline.")
    return None


if __name__ == "__main__":
    for q in ["What's the weather in Bengaluru?",
              "Will it rain in Mumbai tomorrow?",
              "What's the weather like?",
              "Tell me the news.",
              "When is my meeting with Rahul?"]:
        print(f"\n{q}\n-> {answer_online_question(q)}")