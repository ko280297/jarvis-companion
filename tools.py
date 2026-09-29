import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]
MONTH_RE = "|".join(MONTHS)

# Only these question shapes go to the date tool; everything else goes to the LLM.
DATE_QUESTION = re.compile(
    r"\b(what|which)('s|\s+is|\s+was)?\s+(the\s+)?(date|day)\b"
    r"|\btoday'?s\s+date\b"
    r"|\b(date|day)\s+(on|of|is)\b"
    r"|\bwhen\s+is\s+(the\s+)?next\b"
)
TIME_QUESTION = re.compile(r"\bwhat\s+time\b|\bwhat('s|\s+is)\s+the\s+time\b")
# "What's the date?" / "What day is it today?" (nothing else in the question)
PLAIN_DATE_Q = re.compile(
    r"^(what|which)('s|\s+is)?\s+(the\s+)?(date|day)(\s+is\s+it)?(\s+today)?[?.!\s]*$")
# "2nd October 2026", "12th of October"
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MONTH_RE})(?:,?\s+(\d{{4}}))?")
# "October 2nd, 2026", "October 12"
MONTH_DAY = re.compile(rf"\b({MONTH_RE})\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?")


def _fmt(d):
    return f"{d:%A}, {d.day} {d:%B %Y}"


def _find_explicit_date(t):
    """Find a date like '2nd October 2026' in lowercase text."""
    m = DAY_MONTH.search(t)
    if m:
        day, month, year = int(m.group(1)), m.group(2), m.group(3)
    else:
        m = MONTH_DAY.search(t)
        if not m:
            return None
        month, day, year = m.group(1), int(m.group(2)), m.group(3)

    today = date.today()
    try:
        d = date(int(year) if year else today.year, MONTHS.index(month) + 1, day)
    except ValueError:          # e.g. 31st February
        return None
    if not year and d < today:  # "5th January" means the coming one
        d = d.replace(year=d.year + 1)
    return d


def answer_time_question(text):
    """Answer simple date/time questions with Python. Returns None if not one."""
    t = text.lower()

    if TIME_QUESTION.search(t):
        return "It is " + datetime.now().strftime("%I:%M %p").lstrip("0") + "."

    if not DATE_QUESTION.search(t):
        return None

    today = date.today()

    explicit = _find_explicit_date(t)
    if explicit:
        return f"It is {_fmt(explicit)}."

    m = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)
    if m:
        target = WEEKDAYS.index(m.group(1))
        ahead = (target - today.weekday()) % 7 or 7
        return f"Next {m.group(1).title()} is {_fmt(today + timedelta(days=ahead))}."

    if "tomorrow" in t:
        return f"Tomorrow is {_fmt(today + timedelta(days=1))}."
    if "yesterday" in t:
        return f"Yesterday was {_fmt(today - timedelta(days=1))}."
    if "today" in t or PLAIN_DATE_Q.match(t.strip()):
        return f"Today is {_fmt(today)}."

    return None

def resolve_dates(text):
    """Turn relative dates into real ones at save time: 'on Friday' -> '... [date: Friday, 2 October 2026]'."""
    t = text.lower()
    today = date.today()
    d = _find_explicit_date(t)
    if d is None:
        if "day after tomorrow" in t:
            d = today + timedelta(days=2)
        elif "tomorrow" in t:
            d = today + timedelta(days=1)
        elif "today" in t:
            d = today
        else:
            m = re.search(r"\b(" + "|".join(WEEKDAYS) + r")\b", t)
            if m:
                target = WEEKDAYS.index(m.group(1))
                ahead = (target - today.weekday()) % 7 or 7
                d = today + timedelta(days=ahead)
    return f"{text} [date: {_fmt(d)}]" if d else text

if __name__ == "__main__":
    for q in ["What time is it?",
              "When is the next Sunday?",
              "What is the date on next Thursday?",
              "What is the day on 2nd October 2026?",
              "What day is October 12th?",
              "What's the date today?",
              "What day is it tomorrow?",
              "When is my meeting with Rahul?",
              "What is the date?",
              "What is the date of my meeting with Rahul?",]:
        print(q, "->", answer_time_question(q))