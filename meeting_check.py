"""Try fixing meeting notes without speaking. Uses a throw-away database: your real data is never touched.
   python meeting_check.py"""
import os, sys, tempfile
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
import memory as M
import meeting, todo

mem = M.MemoryStore(os.path.join(tempfile.mkdtemp(), "test.db"))


def run(t, chunk=False):
    print(f"\nYou: {t}")
    for p in (meeting.meeting_chunk(t, mem) if chunk else meeting.handle_meeting(t, mem)) or []:
        if p and p[0] == "say":
            print("   Jarvis:", p[1])


run("start meeting mode")
for s in ["Arjun will update the budget sheet by Friday.", "Let's review the slides on Friday at 3 PM.",
          "Can you send the report to Priya."]:
    print(f"\n(in the meeting) {s}")
    meeting.meeting_chunk(s, mem)
run("meeting mode off", chunk=True)
for t in ["read them all", "change the second one to review the demo slides", "change it to update the budget file",
          "yes", "change it to Saturday at 4 pm", "yes", "remove the last one"]:
    run(t)
print("\nSaved to the schedule:", mem.list_get("schedule"))
print("Saved to the to-do list:", [r["text"] for r in todo._rows(mem)])