"""Fix 114 check: the recall coach (flashcards from your notes), including where it could go wrong.
   jarvis recall_check.py     A throw-away database: your real memory is never opened. No mic, no AI, no internet."""
import os
import random
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import memory as M  # noqa: E402
import notes  # noqa: E402
import recall as R  # noqa: E402

random.seed(1)
ok = bad = 0


def check(name, cond, got=""):
    global ok, bad
    ok += bool(cond)
    bad += not cond
    print(("✅ " if cond else "❌ ") + name + ("" if cond else f"\n      got: {got}"))


mem = M.MemoryStore(os.path.join(tempfile.mkdtemp(), "test.db"))
H = lambda t: R.handle(t, mem)

print("— Cards are made by code, only from what your notes say —")
cards = R.cards_from("Photosynthesis is how plants make food. GST: goods and services tax. "
                     "CPU stands for central processing unit. It is raining today. My plan is to sleep early. "
                     "Is the exam on Monday? The mitochondria are the powerhouse of the cell.")
qs = [q for q, a in cards]
check('"Photosynthesis is how plants make food" -> What is Photosynthesis?', ("What is Photosynthesis?",
      "how plants make food") in cards, cards)
check('"GST: goods and services tax" -> a card', ("What is GST?", "goods and services tax") in cards, cards)
check('"CPU stands for ..." -> What does CPU stand for?', ("What does CPU stand for?", "central processing unit")
      in cards, cards)
check('"The mitochondria are ..." -> What are the mitochondria?', any(q == "What are the mitochondria?" for q in qs), qs)
check('"It is raining today" is NOT a card', not any("raining" in a for q, a in cards), cards)
check('"My plan is to sleep early" is NOT a card', not any("sleep" in a for q, a in cards), cards)
check("a question in the note is NOT a card", not any("exam" in q.lower() for q in qs), qs)

print("\n— Checking answers (by code) —")
for said, ans, want in [("it's how plants make their food", "how plants make food", True),
                        ("goods and services tax", "goods and services tax", True),
                        ("goods and service", "goods and services tax", True),
                        ("tax", "goods and services tax", False),
                        ("Tokyo", "Tokyo", True), ("Kyoto", "Tokyo", False),
                        ("1947", "1947", True), ("1948", "1947", False),
                        ("seven", "7 days", True), ("8 days", "7 days", False),
                        ("I don't remember", "Tokyo", False),
                        ("central processing unit", "central processing unit", True),
                        ("central unit", "central processing unit", True)]:
    check(f'"{said}" for "{ans}" -> {"right" if want else "wrong"}', R.is_right(said, ans) == want,
          R.is_right(said, ans))

print("\n— A quiz —")
r = H("quiz me")
check("no notes yet: says how to make cards (no made-up questions)", r and "don't have any flashcards" in r, r)
notes._save(mem, "Biology", "Photosynthesis is how plants make food. The mitochondria are the powerhouse of the cell.")
notes._save(mem, "Tax", "GST: goods and services tax.")
r = H("how many flashcards do I have")
check('"how many flashcards do I have" counts them (3, all due today)', r and "3 flashcards" in r and "3 are due" in r, r)
r = H("quiz me")
check('"quiz me" starts with question 1 of 3', r and "Question 1 of 3" in r, r)
answers = {"What is Photosynthesis?": "how plants make food", "What are the mitochondria?": "I don't know",
           "What is GST?": "goods and services"}
results = []
for _ in range(3):
    q = R._quiz["cards"][R._quiz["i"]]["q"]
    results.append(H(answers[q]))
check("a right answer: Right!", any(x.startswith("Right") for x in results), results)
check('"I don\'t know" shows the answer', any("The answer is: the powerhouse of the cell" in x for x in results), results)
check("the end: 2 of 3 right, the missed one comes back tomorrow",
      "2 of 3 right" in results[-1] and "tomorrow" in results[-1], results[-1])
check("the quiz is over", not R.active())
c = {x["q"]: x for x in R._cards(mem)}
check("missed card: due tomorrow", c["What are the mitochondria?"]["due"] == (date.today() + timedelta(days=1)).isoformat())
check("right card: comes back later (in 3 days)",
      c["What is GST?"]["due"] == (date.today() + timedelta(days=3)).isoformat(), c["What is GST?"])
r = H("quiz me")
check("nothing due now: says when the next ones are (no repeat today)", r and "Nothing is due" in r, r)

print("\n— Where it could go wrong —")
r = H("quiz me on my tax note")
check('"quiz me on my tax note" asks only the tax card', r and "Question 1 of 1" in r and "GST" in r, r)
r = H("what's the weather")
check('"what\'s the weather" in the middle is NOT taken as a (wrong) answer: the quiz ends quietly',
      r is None and not R.active(), r)
r = H("quiz me on cooking")
check('"quiz me on cooking" (no such cards) says so', r and "couldn't find" in r, r)
r = H("quiz me on my biology note")
r = H("stop the quiz")
check('"stop the quiz" ends it', r and not R.active(), r)
r = H("quiz me on my biology note")
r = H("stop")
check('"stop" also ends it', not R.active(), r)
for said in ["test my microphone", "I have a test tomorrow", "quiz night is on Friday", "what is GST",
             "remind me to revise my notes at 6", "take a note: revise chapter 2"]:
    check(f'"{said}" does not start a quiz', H(said) is None and not R.active())

print("\n— Edit options —")
H("quiz me on my tax note")
r = H("goods and taxes")
check("an answer marked wrong ...", r and "Not quite" in r, r)
r = H("that was right")
check('... "that was right" counts it as right', r and "counted it as right" in r, r)
check("... and the card moves on (not due tomorrow)", R._cards(mem, "q = 'What is GST?'")[0]["due"] !=
      (date.today() + timedelta(days=1)).isoformat())
r = H("change the answer to goods and services tax, India")
check('"change the answer to ..." updates the card', r and "the answer is now" in r, r)
r = H("make a flashcard: what is the capital of Japan? Tokyo")
check('"make a flashcard: ...? Tokyo" makes one', r and "Answer: Tokyo" in r, r)
r = H("make a flashcard question what is 7 times 8 answer 56")
check('"make a flashcard question ... answer ..." works too', r and "Answer: 56" in r, r)
H("quiz me on capital")
r = H("delete this card")
check('"delete this card" in a quiz deletes THAT card (the one being asked)', r and "deleted" in r and
      not R._cards(mem, "a = 'Tokyo'") and R._cards(mem, "q = 'What is GST?'"), r)
notes.delete(mem, notes._all(mem)[0]["id"])
R.sync(mem)
check("a deleted note's cards go away; your own cards stay",
      not any(x["source"] == "Biology" for x in R._cards(mem)) and any(x["a"] == "56" for x in R._cards(mem)))
print(f"\n{ok} passed, {bad} failed")
