"""Daily summary: "What's my day?"
Built by Python from what's already on the device, with no LLM, so it's fast and the dates are exact.
The only online part is the weather, and only if a home city is set."""
import re
from datetime import datetime, timedelta

from reminders import when_words

SUMMARY_Q = re.compile(
    r"\b(my day|my plans? (?:for )?today|plan for (?:the )?day|what do i have (?:on |planned )?today"
    r"|anything (?:on|planned for|for) today|daily summary|morning brief(?:ing)?|day look like)\b")


def _date_tag(d):
    """The same format resolve_dates() uses: [date: Friday, 2 October 2026]."""
    return f"[date: {d:%A}, {d.day} {d:%B %Y}]"


def _clean(item):
    """'the meeting with Rahul today [date: ...] (saved on ...)' -> 'meeting with Rahul'."""
    item = re.sub(r"\s*\[date:.*?\]", "", item.split(" (saved on")[0])
    item = re.sub(r"\b(today|tomorrow|tonight|on (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b",
                  "", item, flags=re.I)                    # the heading already says when
    item = re.sub(r"^the\s+", "", " ".join(item.split()), flags=re.I)
    return item.strip(" ,.")


def _plural(n, word):
    return f"{n} {word}{'s' if n != 1 else ''}"


def _unique(items):
    """Drop repeats, ignoring upper/lower case."""
    seen, out = set(), []
    for i in items:
        if i.lower() not in seen:
            seen.add(i.lower())
            out.append(i)
    return out


def _events_on(d, memory):
    """Schedule items and memories that carry this day's date."""
    tag = _date_tag(d)
    items = [i for i in memory.list_get("schedule") if tag in i]
    items += [m for m in memory.all() if tag in m]
    return [_clean(i) for i in items]


def build_summary(memory, reminders, weather=None):
    """weather: a function that returns one sentence about today's weather (or None)."""
    now = datetime.now()
    today, tomorrow = now.date(), now.date() + timedelta(days=1)

    name = memory.get_setting("user_name")
    hello = "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"
    lines = [f"{hello}{', ' + name if name else ''}! It's {today:%A}, {today.day} {today:%B}."]

    # 1. Reminders still to come today
    todays = [(d, x, k) for d, x, k in reminders.upcoming() if d.date() == today]
    parts = _unique([("a timer" if k == "timer" else x) + f", {when_words(d)}" for d, x, k in todays])
    if parts:
        lines.append(f"You have {_plural(len(parts), 'reminder')} left today: " + "; ".join(parts) + ".")
    else:
        lines.append("You have no more reminders today.")

    # 2. Today's schedule
    events = _unique(_events_on(today, memory))
    if events:
        lines.append("On your schedule today: " + "; ".join(events) + ".")

    # 3. A peek at tomorrow
    upcoming = _unique(_events_on(tomorrow, memory)
                       + [x for d, x, k in reminders.upcoming() if d.date() == tomorrow and k == "reminder"])
    if upcoming:
        lines.append("Tomorrow: " + "; ".join(upcoming) + ".")

    # 4. Shopping
    grocery = memory.list_get("grocery")
    if grocery:
        lines.append(f"Your grocery list has {_plural(len(grocery), 'item')}.")

    # 5. Weather (the only part that uses the internet)
    if weather:
        sentence = weather()
        if sentence:
            lines.append(sentence)

    return " ".join(lines)