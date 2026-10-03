"""Games: fun, simple activities you can play by voice.
While one is running, what you say goes to it first. Real commands (time, lists, weather...) still work,
and anything else gets a gentle reprompt instead of the LLM. All game state lives here in Python."""
import random
import re
import time

EXIT = re.compile(
    r"\b(?:stop|quit|exit|end|close)\b.*\b(?:game|games|quiz|playing|math)\b|\bi(?:'m| am) done playing\b")
LIST_GAMES = re.compile(r"\b(?:what|which) games\b|\bgames? (?:can|do) you\b")
PLAY = re.compile(r"\b(?:play|game|let'?s do|start|begin)\b")
PLAY_AGAIN = re.compile(r"\b(?:play|go) (?:it |that )?again\b|\bone more (?:time|game|round)\b")
STATUS = re.compile(r"\b(?:what happened to|where(?:'s| is)|back to|continue|resume) (?:the |our |my |that )?game\b"
                    r"|\bwhat (?:was|is) the question\b|\bwhere were we\b")
GAME_OVER_SECONDS = 20      # how long the "Game over" card stays on screen

# ---------- Spoken numbers ("forty two" -> 42) ----------
ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def parse_number(lower):
    """'42' / 'forty two' / 'one hundred twenty three' -> int, or None.
    With several numbers ('4... 24', 'I said 24'), the last one counts."""
    digits = re.findall(r"\b\d{1,4}\b", lower)
    if digits:
        return int(digits[-1])
    total, found = 0, False
    for w in re.findall(r"[a-z]+", lower):
        if w in TENS:
            total, found = total + TENS[w], True
        elif w in ONES:
            total, found = total + ONES[w], True
        elif w == "hundred":
            total, found = (total or 1) * 100, True
    return total if found else None


def _duration(seconds):
    m, s = divmod(int(seconds), 60)
    return (f"{m} minute{'s' if m != 1 else ''} " if m else "") + f"{s} second{'s' if s != 1 else ''}"


# ---------- Base ----------
class Activity:
    """Every game has: a name, KEYWORDS that pick it, start(), handle(), prompt() and screen()."""
    name = "activity"
    KEYWORDS = None

    def start(self):
        return ""

    def handle(self, lower):
        """Return (reply, finished), or None if this sentence isn't a move in the game."""
        return None

    def prompt(self):
        """What to say when we didn't catch a move: reminds the user where the game is."""
        return ""

    def screen(self):
        return {"title": self.name, "lines": []}


# ---------- Game: memory sequence ----------
class MemorySequence(Activity):
    name = "Memory sequence"
    KEYWORDS = re.compile(r"\b(?:memory|sequence|simon|colou?rs? game)\b")
    PALETTE = ["red", "blue", "green", "yellow"]
    START_LENGTH = 3
    HEARD_AS = {   # what speech-to-text writes for each colour
        "red": {"red", "read", "rad", "bread", "rid", "rat", "rit", "ret", "wed", "redd"},
        "blue": {"blue", "blew", "bloo", "bleu", "blu", "glue"},
        "green": {"green", "greens", "grin", "grain"},
        "yellow": {"yellow", "yello", "yellows", "jello", "hello", "halo", "hallo", "yalo"},
    }
    best = 0       # best score this session (shared across games)

    def __init__(self):
        self.seq = [random.choice(self.PALETTE) for _ in range(self.START_LENGTH)]
        self.heard = []
        self.to_show = None
        self.retried = False       # one more chance per round (speech-to-text can mishear a colour)

    def _colors(self, lower):
        found = []
        for word in re.findall(r"[a-z]+", lower):
            for colour, forms in self.HEARD_AS.items():
                if word in forms:
                    found.append(colour)
                    break
        return found

    def start(self):
        self.to_show = list(self.seq)
        return "Let's play memory sequence! Watch and listen, then say the colours back in order."

    def prompt(self):
        return "Say the colours back in order. Or say, show me again."

    def handle(self, lower):
        if re.search(r"\b(?:show|play|say) (?:it |me |them )?again\b|\brepeat\b", lower):
            self.heard = []
            self.to_show = list(self.seq)
            return "Okay, watch it again carefully.", False
        got = self._colors(lower)
        if not got:
            return None
        self.heard += got
        n = len(self.heard)
        if self.heard != self.seq[:n]:                      # a wrong colour (or too many)
            if not self.retried:
                self.retried = True
                self.heard = []
                self.to_show = list(self.seq)
                return "Not quite. Let's try that round once more, so watch carefully.", False
            done = len(self.seq) - 1 if len(self.seq) > self.START_LENGTH else 0
            MemorySequence.best = max(MemorySequence.best, done)
            answer = ", ".join(self.seq)
            score = f"You remembered {done} colours." if done else "Let's try again soon."
            return f"Game over! It was {answer}. {score} Your best is {MemorySequence.best}.", True
        if n < len(self.seq):                                # right so far, they paused
            return "Good so far. Keep going.", False
        self.seq.append(random.choice(self.PALETTE))         # whole pattern right: one more colour
        self.retried = False
        self.heard = []
        self.to_show = list(self.seq)
        return (random.choice(["Perfect!", "Yes!", "You got it!"])
                + f" Now {len(self.seq)} colours, so watch carefully."), False

    def screen(self):
        return {"title": "🧠 Memory sequence",
                "lines": [f"{len(self.seq)} colours this round", f"Best: {MemorySequence.best}"],
                "orbs": self.PALETTE}


# ---------- Game: mental math, 10 questions ----------
class MentalMath(Activity):
    name = "Mental math"
    KEYWORDS = re.compile(r"\b(?:math|maths|mental|sums?|arithmetic)\b")
    TOTAL = 10

    def __init__(self):
        self.n, self.score, self.started = 0, 0, time.time()
        self.question, self.answer, self.last = "", 0, ""
        self.tried = False

    def _next(self):
        self.n += 1
        self.tried = False
        op = random.choice(["plus", "minus", "times"])
        if op == "plus":
            a, b = random.randint(2, 50), random.randint(2, 50)
            self.answer = a + b
        elif op == "minus":
            a, b = sorted([random.randint(2, 60), random.randint(2, 60)], reverse=True)   # never negative
            self.answer = a - b
        else:
            a, b = random.randint(2, 12), random.randint(2, 12)
            self.answer = a * b
        self.question = f"What's {a} {op} {b}?"
        return f"Question {self.n}. {self.question}"

    def start(self):
        return f"Let's do {self.TOTAL} quick questions. Say repeat to hear one again, or skip to move on. " + self._next()

    def prompt(self):
        return f"I didn't catch a number. Question {self.n}: {self.question}"

    def _move_on(self, prefix):
        if self.n >= self.TOTAL:
            took = _duration(time.time() - self.started)
            praise = "Brilliant!" if self.score >= 8 else "Nice work!" if self.score >= 5 else "Good practice!"
            return f"{prefix} Game over! You got {self.score} out of {self.TOTAL}, in {took}. {praise}", True
        return f"{prefix} {self._next()}", False

    def handle(self, lower):
        if re.search(r"\b(?:repeat|again|say that again)\b", lower):
            return self.question, False
        if re.search(r"\b(?:skip|pass|don'?t know|no idea)\b", lower):
            self.last = f"Skipped: {self.answer}"
            return self._move_on(f"It's {self.answer}.")
        n = parse_number(lower)
        if n is None:
            return None
        if n == self.answer:
            self.score += 1
            self.last = f"✓ {self.answer}"
            return self._move_on(random.choice(["Right!", "Correct!", "That's it!", "Yes!"]))
        if not self.tried:                  # maybe speech-to-text misheard: one more chance
            self.tried = True
            return f"I heard {n}. Try once more?", False
        self.last = f"✗ You said {n}, it was {self.answer}"
        return self._move_on(f"Not quite, it's {self.answer}.")

    def screen(self):
        return {"title": f"🧮 Mental math: question {self.n} of {self.TOTAL}",
                "lines": [self.question, f"Score: {self.score}", self.last]}


# ---------- Game: guess the number ----------
class NumberGuess(Activity):
    name = "Guess the number"
    KEYWORDS = re.compile(r"\b(?:number|guess(?:ing)?)\b")

    def __init__(self, low=1, high=100):
        self.low, self.high = low, high
        self.secret = random.randint(low, high)
        self.tries = 0
        self.last = "Say your first guess!"

    def start(self):
        return f"Let's play! I'm thinking of a number between {self.low} and {self.high}. What's your guess?"

    def prompt(self):
        return f"I'm thinking of a number between {self.low} and {self.high}. Say a number to guess."

    def handle(self, lower):
        if re.search(r"\b(?:give up|tell me the (?:number|answer)|reveal)\b", lower):
            return f"No problem! It was {self.secret}. Good try!", True
        n = parse_number(lower)
        if n is None:
            return None
        if not self.low <= n <= self.high:
            return f"Pick a number between {self.low} and {self.high}.", False
        self.tries += 1
        if n == self.secret:
            word = "try" if self.tries == 1 else "tries"
            return f"Yes! It was {n}! You got it in {self.tries} {word}. Well played!", True
        direction = "higher" if n < self.secret else "lower"
        self.last = f"{n}: go {direction}"
        if abs(n - self.secret) <= 5:
            return f"So close! Go a little {direction}.", False
        return f"Go {direction}.", False

    def screen(self):
        return {"title": f"🔢 Guess the number ({self.low}–{self.high})", "lines": [f"Tries: {self.tries}", self.last]}


# ---------- Manager ----------
GAMES = [MemorySequence, MentalMath, NumberGuess]     # checked in this order; new games go here
_current = None
_last_game = None      # so "play again" restarts the same game


def _names():
    return ", ".join(g.name for g in GAMES[:-1]) + ", and " + GAMES[-1].name


def _show(game):
    """A pattern the game wants played on screen (memory sequence), taken once."""
    seq = getattr(game, "to_show", None)
    game.to_show = None
    return seq


def _start(game):
    """Start a new game and remember it for 'play again'."""
    global _current, _last_game
    _current, _last_game = game(), game
    reply = _current.start()
    return {"say": reply, "screen": _current.screen(), "show": _show(_current)}


def is_active():
    return _current is not None


def end_activity():
    global _current
    _current = None


def game_reprompt():
    """Something wasn't a move and wasn't a command: remind the user where the game is (instead of the LLM)."""
    if not _current:
        return None
    return {"say": _current.prompt(), "screen": _current.screen()}


def handle_activity(text):
    """Returns {say, screen, show} if a game handled this, otherwise None."""
    global _current
    lower = text.lower().strip(" .!?,")

    if _current:
        if EXIT.search(lower):
            name = _current.name
            _current = None
            return {"say": f"Okay, we stopped {name}. That was fun!", "screen": None}
        if STATUS.search(lower):
            return {"say": f"We're still playing {_current.name}! " + _current.prompt(), "screen": _current.screen()}
        result = _current.handle(lower)
        if result is None:
            return None                              # maybe a real command: main.py decides, then reprompts
        reply, finished = result
        if finished:                                 # keep a "Game over" card on screen for a little while
            screen = {"title": f"🏁 Game over · {_current.name}", "lines": [reply],
                      "expires": time.time() + GAME_OVER_SECONDS}
        else:
            screen = _current.screen()
        show = _show(_current)
        if finished:
            _current = None
        return {"say": reply, "screen": screen, "show": show}

    if STATUS.search(lower):
        return {"say": f"We're not playing a game right now. I can play {_names()}.", "screen": None}

    if LIST_GAMES.search(lower):
        return {"say": f"I can play {_names()}. Just say, let's play, and the name!", "screen": None}

    if _last_game and PLAY_AGAIN.search(lower):
        return _start(_last_game)

    if PLAY.search(lower):                           # "let's play ..." -> pick the game by its keywords
        for game in GAMES:
            if game.KEYWORDS.search(lower):
                return _start(game)
        return {"say": f"I can play {_names()}. Which one would you like?", "screen": None}
    return None