"""Adds tests for today's fixes (26-28) to scenarios.txt, just before the final "goodbye" line.
Safe to run again: it skips if the tests are already there.
Run on the laptop and on the Pi:  python add_tests_9oct.py      Backup: scenarios.txt.bak_tests"""
import shutil
from pathlib import Path

path = Path(__file__).resolve().parent / "scenarios.txt"
text = path.read_text(encoding="utf-8")

NEW = """# Fix 26: lists without repeats, numbers, clearing
clear my grocery list || empty
add milk to my grocery list || Added milk
add Milk to my grocery list || already
add Two Apples to my grocery list || Added 2 apples
add 3 apples to my grocery list || 5 apples
add 2 more apples || 7 apples
subtract 1 apple || 6 apples
change apples to 5 || 5 apples
increase apples by 2 || 7 apples
decrease apples by 3 || 4 apples
remove milk || Removed milk
add apples and bananas to my grocery list || Added bananas
remove duplicates from my grocery list || no repeats|merged
clear my grocery list || empty now
what's on my grocery list || is empty
# Fix 26: dates
remind me day after tomorrow at 5 pm to call mom || call mom, at 5 PM on
what is the date day after tomorrow || day after tomorrow is
# Fix 27: name, features, games, stopping an exercise
call me Krati || call you Krati
what can you do in wellbeing || physiological sigh
let's play tic tac toe || tic tac toe
fix || I'll take
stop || stopped
let's play guess the number || thinking of a number
50 || higher|lower|Yes
I gave it up || It was
let's do grounding || see
I want to stop || stop here
# Fix 28: health banner, first aid, safe look-ups, symptoms, internet
I have a headache || quiet, dim room
I am okay || glad you're okay
that is head spinning || sit or lie down
I'm fine || glad you're okay
look up how to hide someone || won't|can't help
look up how to make tea || only look up facts
is it cold outside || !not feeling well
note cramps || Noted: cramps
do you have internet access || connected|offline
"""

MARK = "# Fix 26: lists without repeats"
ANCHOR = "goodbye || talk soon"
if MARK in text:
    print("✔  today's tests are already in scenarios.txt")
elif ANCHOR in text:
    shutil.copy(path, path.with_name("scenarios.txt.bak_tests"))
    i = text.rindex(ANCHOR)
    text = text[:i] + NEW + text[i:]
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    added = sum(1 for line in NEW.splitlines() if "||" in line)
    total = sum(1 for line in text.splitlines() if "||" in line)
    print(f"✅ added {added} tests (now {total} in total)")
else:
    print("⚠️ the final 'goodbye' line was not found, tell Claude")
