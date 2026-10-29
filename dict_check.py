"""Try the offline dictionary without speaking.   python dict_check.py"""
import sys, time
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import dictionary as d
t = time.time()
d._wn()
print(f"(dictionary loaded in {time.time() - t:.1f} s)")
for q in ["What does serendipity mean?", "define happy", "another meaning", "spell it", "another word for happy",
          "opposite of brave", "use frugal in a sentence", "how do you spell necessary", "what does ephemeral mean",
          "synonym for big", "what does Sarita mean"]:
    t = time.time()
    r = d.handle(q)
    print(f"\nYou: {q}\n  Jarvis: {r if r else '(not a dictionary word: the rest of Jarvis answers)'}   [{time.time() - t:.2f} s]")