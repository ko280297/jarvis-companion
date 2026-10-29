"""Short commands for Jarvis, used by jarvis.bat (so you never type .venv\\Scripts\\python.exe again).

    jarvis              start Jarvis
    jarvis type         talk to Jarvis by typing
    jarvis test         the big test: shows only the score and anything that failed
                        (the full output is saved in test_result.txt)
    jarvis checks       the 4 small tests (Hindi, hearing, reminders, phone)
    jarvis all          the big test and the 4 small tests
    jarvis fix 109      run apply_fixes109.py
    jarvis setup        run setup_jarvis.py (downloads; add --check to only check)
    jarvis NAME.py ...  run any Jarvis file with Jarvis's own Python
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
ENV = dict(os.environ, PYTHONIOENCODING="utf-8")
CHECKS = ["hindi_check.py", "hear_check.py", "remind_check.py", "phone_check.py"]


def keep_private(name):
    """Test output stays on this computer: make sure git never uploads it."""
    gi = HERE / ".gitignore"
    lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    if name not in lines:
        with open(gi, "a", encoding="utf-8") as f:
            f.write(("\n" if lines and lines[-1] else "") + name + "\n")


def run(*args, **kw):
    return subprocess.run([PY, *args], cwd=HERE, env=ENV, **kw).returncode


def big_test():
    keep_private("test_result.txt")
    print("Running the big test (a few minutes, keep Ollama open)...", flush=True)
    out = HERE / "test_result.txt"
    with open(HERE / "scenarios.txt", encoding="utf-8") as fin, open(out, "w", encoding="utf-8") as fout:
        run("text_mode.py", "--fast", stdin=fin, stdout=fout, stderr=subprocess.STDOUT)
    text = out.read_text(encoding="utf-8", errors="replace")
    failed = []
    for block in text.split("You> ")[1:]:
        first = block.splitlines()[0].strip() if block.strip() else ""
        if "❌ FAIL" in block:
            want = [l.strip() for l in block.splitlines() if "❌ FAIL" in l][0]
            failed.append(f'  ❌ "{first}"   {want.replace("❌ FAIL", "").strip()}')
    score = [l for l in text.splitlines() if "SCORE" in l]
    print(score[-1].strip() if score else "⚠️ The test stopped early. Send Claude the end of test_result.txt.")
    for f in failed:
        print(f)
    if failed:
        print("  (full output: test_result.txt)")
    return not failed and bool(score)


def small_checks():
    ok = True
    for name in CHECKS:
        if not (HERE / name).exists():
            continue
        p = subprocess.run([PY, name], cwd=HERE, env=ENV, capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
        lines = [l for l in (p.stdout + p.stderr).splitlines() if l.strip()]
        last = lines[-1].strip() if lines else "(no output)"
        good = p.returncode == 0 and " 0 failed" in last
        ok &= good
        print(f"  {'✅' if good else '❌'} {name:<17} {last}")
        if not good:
            print("\n".join("      " + l for l in lines[-15:]))
    return ok


def main(argv):
    cmd = argv[0].lower() if argv else ""
    if cmd in ("", "start", "run"):
        return run("main.py")
    if cmd in ("type", "text"):
        return run("text_mode.py", *argv[1:])
    if cmd == "test":
        return 0 if big_test() else 1
    if cmd in ("checks", "check"):
        return 0 if small_checks() else 1
    if cmd == "all":
        a = big_test()
        print("\nThe 4 small tests:")
        b = small_checks()
        print("\n🎉 Everything passed." if a and b else "\n⚠️ Something failed: send Claude the lines with ❌.")
        return 0 if a and b else 1
    if cmd == "fix" and len(argv) > 1:
        name = f"apply_fixes{argv[1]}.py"
        if not (HERE / name).exists():
            print(f"I can't find {name} in this folder. Put it next to jarvis.bat first.")
            return 1
        return run(name)
    if cmd == "setup":
        return run("setup_jarvis.py", *argv[1:])
    if cmd.endswith(".py"):
        return run(*argv)
    print(__doc__)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)