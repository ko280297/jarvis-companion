"""Checks meeting mode without a microphone: feeds it sentences, says yes to everything, prints what would be saved.
Run from the companion folder:  python test_meeting.py   (nothing real is saved: it uses a pretend memory)"""
import meeting
class Mem:
    def __init__(self): self.lists = {}
    def list_get(self, n): return self.lists.get(n, [])
    def list_add(self, n, i): self.lists.setdefault(n, []).append(i)
m = Mem()
meeting.handle_meeting("meeting mode on", m)
for line in ["Okay team, let's review the slides on Friday at 3 PM.",
             "Krati will send the final report by tomorrow.",
             "Can you book the conference room for next Monday?",
             "Arjun, please update the budget sheet before the 15th of October.",
             "Last Friday we shipped the new version.",
             "I don't think we need to meet on Friday.",
             "The weather was really hot yesterday.",
             "Shubhangi will follow up with the client on Wednesday.",
             "Let's catch up at half past four tomorrow.",
             "Remind me to email Rahul after lunch."]:
    meeting.meeting_chunk(line, m)
p = meeting.meeting_chunk("meeting mode off", m)
for s in p: print(s)
while meeting._review:
    for s in meeting.handle_meeting("yes", m): print(s)
print("\nSAVED:")
for name, items in m.lists.items():
    print(f"  {name}:")
    for i in items:
        print(f"    - {i}")