"""A small safety check for anything the user asks Jarvis to save and repeat later
(reminders, lists, memories). Speech-to-text can mishear, so the reply stays gentle."""
import re

UNSAFE = re.compile(r"\b(kill|murder|stab|shoot|bomb|hurt|attack|rape|poison|kidnap)\w*\b")
REFUSAL = "Hmm, that didn't sound right, so I won't save it. If I misheard you, please say it again."


def is_unsafe(text):
    return bool(UNSAFE.search(text.lower()))