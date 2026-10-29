"""Kid-safe play mode (fix 76). Stories, riddles, an easy quiz, jokes, games, spelling, the dictionary and maths.
While it's on: the internet is off, the owner's private things are hidden, and a grown-up's PIN turns it off.
It is NOT a babysitter: Jarvis says so when it starts.

The stories are short retellings (in our own words) of old folk tales and fables: Panchatantra, Aesop,
Akbar-Birbal, Tenali Rama. To add your own, add a line to STORIES below: ("title", "the story").
"""
import random
import re
import time

STORIES = [
    ("The Thirsty Crow",
     "Once upon a time, on a very hot summer day, a little crow was flying over the fields. The sun was burning, "
     "and the crow was very, very thirsty. He looked for water in the river, but the river had dried up. "
     "He looked in the pond, but the pond was empty too. The poor crow was tired, and his wings felt heavy. "
     "Just then, he saw a pot in a garden. He flew down, full of hope, and looked inside. There was water! "
     "But the water was right at the bottom, and his short beak could not reach it. He tried and tried, but no. "
     "The crow sat down and thought. Then he had an idea. He saw some small pebbles lying on the ground. "
     "He picked up one pebble, and dropped it into the pot. Plop! Then another pebble. Plop! And another. "
     "Slowly, slowly, the water started to rise. Higher, and higher, and higher. "
     "At last, the water came all the way up, and the crow could drink. He drank and drank, until he wasn't "
     "thirsty any more. Then he flapped his wings, and flew away, happy and proud. "
     "The lesson of this story is: don't give up. If you think hard, you can find a way."),
    ("The Lion and the Mouse",
     "Once upon a time, in a big green forest, a mighty lion was fast asleep under a tree. "
     "A tiny little mouse came running along, and by mistake, she ran right over the lion's nose! "
     "The lion woke up with a roar, and caught the mouse in his big paw. "
     "Please, please let me go, squeaked the little mouse. If you let me go, one day I will help you. "
     "The lion laughed a big lion laugh. How can a tiny mouse ever help a lion? But he was kind, so he let her go. "
     "A few days later, the lion was walking in the forest, when suddenly, he fell into a hunter's net. "
     "He pulled, and he pushed, and he roared, but the ropes held him tight. He could not get out. "
     "The little mouse heard the lion roaring. She came running as fast as her little legs could go. "
     "Don't worry, she said, I will help you. And she began to bite the ropes with her sharp little teeth. "
     "Bite, bite, bite. One rope broke. Then another. Then another. And soon, the lion was free! "
     "Thank you, little friend, said the lion. I'm sorry I laughed at you. From that day, they were best friends. "
     "The lesson of this story is: nobody is too small to help. Be kind to everyone."),
    ("The Hare and the Tortoise",
     "Once upon a time, there was a hare who could run very, very fast. He was proud of it, and he liked to "
     "laugh at the other animals. One day, he laughed at a tortoise. You are so slow, he said. "
     "The tortoise smiled calmly and said, let's have a race, and we'll see who wins. "
     "All the animals of the forest came to watch. Ready, steady, go! The hare ran off like the wind, "
     "and the tortoise started walking, slowly and steadily. Soon, the hare was far, far ahead. He looked back, "
     "and he couldn't even see the tortoise. I have so much time, thought the hare. I'll take a little nap "
     "under this tree. And he fell fast asleep. The tortoise kept walking. Step by step. He never stopped, "
     "and he never gave up. He walked past the sleeping hare, and kept going. "
     "When the hare woke up, the sun was going down. He jumped up and ran as fast as he could. But when he "
     "reached the finish line, the tortoise was already there, and all the animals were cheering for him! "
     "The lesson of this story is: slow and steady wins the race. Keep going, and don't show off."),
    ("The Monkey and the Crocodile",
     "Once upon a time, a clever monkey lived in a big jamun tree, next to a river. The jamuns were sweet and "
     "juicy. One day, a crocodile came out of the river to rest under the tree. The friendly monkey threw down "
     "some jamuns for him. They're delicious, said the crocodile. Soon, the monkey and the crocodile became "
     "good friends. Every day, the monkey shared his jamuns, and the crocodile took some home to his wife. "
     "But the crocodile's wife was greedy. If the jamuns are so sweet, she said, the monkey's heart must be even "
     "sweeter. Bring me his heart! The crocodile was sad, but he went to the monkey and said, come to my home "
     "for dinner. Sit on my back, and I'll carry you across the river. The monkey happily jumped on. "
     "In the middle of the river, the crocodile said, I'm sorry, my friend. My wife wants to eat your heart. "
     "The monkey was scared, but he stayed calm, and thought fast. Oh no, he said, I left my heart at home, in "
     "the jamun tree! Take me back, and I'll get it for you. The silly crocodile swam back to the tree. "
     "The monkey jumped up onto the highest branch, safe and sound. Hearts don't live in trees, he called down. "
     "And you are no friend of mine! The lesson of this story is: stay calm and think, even when you're scared."),
    ("The Clever Rabbit and the Lion",
     "Once upon a time, a big, greedy lion lived in a forest. Every day, he hunted many animals, and all the "
     "animals were afraid. So they went to the lion and said, please, king, stay in your cave. Every day, one of "
     "us will come to you. The lion agreed. One day, it was the turn of a small, clever rabbit. "
     "The rabbit wasn't in a hurry. He walked very slowly, and he thought, and thought. "
     "When he finally reached the cave, it was late, and the lion was very angry. Why are you so late, he roared. "
     "The rabbit bowed and said, I'm sorry, my king. On the way, another lion stopped me. He said he is the real "
     "king of this forest! What, roared the lion. Show me this other lion! "
     "The rabbit took him to a deep well. He lives down there, said the rabbit. The lion looked into the well, "
     "and saw a lion looking back at him. It was his own reflection, but he didn't know that! He roared, and the "
     "lion in the well roared back. Angry, the lion jumped into the well to fight. Splash! There was no other lion, "
     "only cold water. The lion climbed out, all wet and embarrassed, and ran far away, and he never troubled the "
     "forest again. All the animals were free, and they cheered for the clever little rabbit. "
     "The lesson of this story is: a clever mind is stronger than big muscles."),
    ("The Boy Who Cried Wolf",
     "Once upon a time, a young shepherd boy looked after the sheep of his village, on a quiet green hill. "
     "Every day was the same, and the boy was bored. One day, he had an idea for some fun. "
     "He shouted as loudly as he could, wolf! Wolf! A wolf is eating the sheep! "
     "The villagers dropped their work, and ran up the hill to help. But there was no wolf. "
     "The boy laughed and laughed. The villagers were not happy, and they went back down. "
     "A few days later, he did it again. Wolf! Wolf! Again the villagers came running, and again, there was no "
     "wolf. Don't do this again, they told him, and they walked away, angry. "
     "Then one evening, as the sun was setting, a real wolf came out of the forest, and crept towards the sheep. "
     "The boy was scared. Wolf! Wolf! Please help! he shouted. But this time, nobody came. They thought he was "
     "playing a trick again. The boy ran down to the village, crying. The villagers came back with him, and "
     "chased the wolf away, but some sheep were lost. The boy felt very sorry. "
     "The lesson of this story is: always tell the truth, so people believe you when it really matters."),
    ("The Ant and the Grasshopper",
     "Once upon a time, in a sunny field, there lived a grasshopper and a little ant. "
     "All summer long, the grasshopper sang songs, played music, and jumped around in the sunshine. "
     "But the little ant worked hard every day. She carried grain after grain of food to her home. "
     "Come and play with me, said the grasshopper. Why work so hard on such a lovely day? "
     "Winter is coming, said the ant. I'm saving food for when it's cold. "
     "Winter is far away, laughed the grasshopper, and he went back to singing. "
     "Then the days became shorter, and the wind became cold. Soon, winter came, and snow covered the whole "
     "field. There was no food anywhere. The grasshopper was cold and hungry. "
     "He knocked on the ant's door. Please, little ant, he said, I'm so hungry. Can you help me? "
     "The ant was warm and cosy inside, with lots of food. She felt sorry for him, and she shared her food. "
     "Thank you, said the grasshopper. Next summer, I'll work and save too. "
     "The lesson of this story is: work a little today, and you'll be ready for tomorrow."),
    ("Birbal's Khichdi",
     "Long ago, there was a great king called Akbar, and his wisest minister was Birbal. "
     "One cold winter day, Akbar said, if anyone can stand in the cold lake all night, I will give him a reward. "
     "A poor man said, I will do it. He stood in the icy water the whole night, shivering. "
     "In the morning, Akbar asked, how did you do it? The man said, I saw a lamp far away, in the palace, and "
     "I looked at it all night. Then you got warmth from the lamp, said Akbar. No reward for you. "
     "The poor man went away, very sad. The next day, Birbal didn't come to the king's court. "
     "Akbar went to Birbal's house to see what was wrong. There was Birbal, sitting next to a tiny fire. "
     "And high up, far above the fire, hung a pot of khichdi. What are you doing, asked Akbar. "
     "I'm cooking khichdi, said Birbal. Akbar laughed. How will it ever cook? The fire is so far away! "
     "Birbal smiled. The same way that poor man got warmth from a lamp far away in the palace. "
     "Akbar understood. He called the poor man back, and gave him his reward. "
     "The lesson of this story is: always be fair to everyone."),
    ("Tenali Rama and the Thieves",
     "Long ago, there lived a very clever man called Tenali Rama. One summer night, he heard a sound outside. "
     "He peeped out, and saw some thieves hiding in the bushes near his house. "
     "Tenali Rama didn't shout. He just smiled. Then he said, very loudly, so the thieves could hear: "
     "my dear wife, there are many thieves around these days. Let's hide our gold in the well, where nobody "
     "will find it! He filled a big box with heavy stones, and dropped it into the well. Splash! "
     "Then he went to sleep. The thieves rubbed their hands. The gold is in the well, they whispered. "
     "They started pulling up water from the well, bucket after bucket, to reach the box. "
     "Tenali Rama's garden was very dry, and he cleverly opened the water channel, so all the water went to his plants. "
     "The thieves worked all night long, pulling up water. In the morning, they finally pulled out the box, "
     "opened it, and found only stones! Just then, Tenali Rama came out and said, thank you, my friends. "
     "You have watered my whole garden! The thieves ran away, tired and embarrassed. "
     "The lesson of this story is: a clever idea can turn trouble into help."),
    # fix 79: goose story removed
]

# Pictures for the story card: the first word found in a sentence picks its picture (fix 77)
SCENES = [
    ("lesson", "⭐"), ("crow", "🐦"), ("pebble", "🪨"), ("pot", "🏺"), ("water", "💧"), ("thirsty", "🥵"),
    ("mouse", "🐭"), ("net", "🕸️"), ("lion", "🦁"), ("tortoise", "🐢"), ("hare", "🐇"), ("nap", "😴"),
    ("finish", "🏁"), ("race", "🏁"), ("crocodile", "🐊"), ("monkey", "🐒"), ("jamun", "🫐"), ("river", "🌊"),
    ("tree", "🌳"), ("well", "🪣"), ("reflection", "🪞"), ("rabbit", "🐇"), ("wolf", "🐺"), ("sheep", "🐑"),
    ("villagers", "🏃"), ("grasshopper", "🦗"), ("ant", "🐜"), ("snow", "❄️"), ("winter", "❄️"), ("summer", "☀️"),
    ("food", "🍞"), ("khichdi", "🍲"), ("fire", "🔥"), ("lamp", "🪔"), ("lake", "🏞️"), ("akbar", "👑"),
    ("birbal", "🧑‍🦱"), ("thieves", "🥷"), ("box", "📦"), ("gold", "💰"), ("garden", "🌻"), ("goose", "🪿"),
    ("egg", "🥚"), ("farmer", "👨‍🌾"), ("sun", "☀️"), ("night", "🌙"),
]


def story_parts(text):
    """The story in small pieces (a sentence or two), each read with its own picture."""
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+", text) if p.strip()]


def scene_for(sentence, previous="📖"):
    low = sentence.lower()
    if re.search(r"\bthe lesson\b", low):
        return "⭐"
    hits = [(m.start(), pic) for word, pic in SCENES for m in [re.search(rf"\b{word}", low)] if m]
    return min(hits)[1] if hits else previous             # the thing named first in the sentence


RIDDLES = [   # (question, answer, what counts as right, a hint)
    ("What has hands but cannot clap?", "a clock", r"clock|watch", "It's on the wall, and it tells you the time."),
    ("What has to be broken before you can use it?", "an egg", r"egg", "You might have it for breakfast."),
    ("What goes up but never comes down?", "your age", r"\bage\b", "It gets bigger on every birthday."),
    ("What has many teeth but cannot bite?", "a comb", r"comb", "You use it on your hair."),
    ("What is full of holes but can still hold water?", "a sponge", r"sponge", "It's soft, and you wash dishes with it."),
    ("I am tall when I'm young and short when I'm old. What am I?", "a candle", r"candle",
     "I give light, and I melt slowly."),
    ("What has a neck but no head?", "a bottle", r"bottle", "You can drink water from it."),
    ("What can you catch but not throw?", "a cold", r"cold", "It makes you sneeze, achoo!"),
    ("What gets wetter the more it dries?", "a towel", r"towel", "You use it after a bath."),
    ("What has legs but cannot walk?", "a table, or a chair", r"table|chair|bed|stool", "You sit at it, or on it."),
]

QUIZ = [   # (question, answer, what counts as right, a hint)
    ("Which animal is called the king of the jungle?", "the lion", r"lion", "It has a big mane and a loud roar."),
    ("How many legs does a spider have?", "eight", r"\b(?:8|eight)\b", "It's more than six, and less than ten."),
    ("What colour do you get when you mix blue and yellow?", "green", r"green", "It's the colour of leaves."),
    ("Which is the biggest planet in our solar system?", "Jupiter", r"jupiter", "Its name starts with J."),
    ("How many days are there in a week?", "seven", r"\b(?:7|seven)\b", "Monday, Tuesday... count them!"),
    ("Which bird is India's national bird?", "the peacock", r"peacock|peahen|peafowl",
     "It has beautiful blue and green feathers, and it dances in the rain."),
    ("What do bees make?", "honey", r"honey", "It's sweet and sticky."),
    ("Which is the closest star to the Earth?", "the Sun", r"\bsun\b", "You see it every day, in the sky."),
    ("How many colours are there in a rainbow?", "seven", r"\b(?:7|seven)\b", "The same as the days of the week."),
    ("What do caterpillars turn into?", "butterflies", r"butterfl|moth", "They have colourful wings."),
    ("Which is the fastest land animal?", "the cheetah", r"cheetah", "It looks like a leopard, with black spots."),
    ("What is frozen water called?", "ice", r"\bice\b", "You put it in a cold drink."),
]

KID_PROMPT = (
    "You are Jarvis, a kind, cheerful friend talking with a young child. Reply in one or two short, simple sentences "
    "a child can understand. Be warm and encouraging, but don't use pet names like sweetie or little one. "
    "If you didn't understand, just ask them to say it again. Never talk about violence, scary things, adult topics, "
    "medicine, money or anything unsafe; for those, say: let's ask a grown-up about that. Never ask for or repeat "
    "personal details like names of places, addresses or phone numbers. Never make up facts: if you are not sure, "
    "say you don't know.")

ON = re.compile(r"\b(?:kids?|child|children)(?:'s)? mode (?:on|start)\b|\b(?:turn on|start|switch on|begin|enable)"
                r" (?:the )?(?:kids?|child|children)(?:'s)? mode\b|\bkid[- ]safe mode (?:on)?\b")
OFF = re.compile(r"\b(?:kids?|child|children)(?:'s)? mode (?:off|of|stop|end)\b|\b(?:turn off|stop|end|exit|switch off"
                 r"|close|leave|disable) (?:the )?(?:kids?|child|children)(?:'s)? mode\b")
STORY = re.compile(r"\b(?:tell|read|say)(?: me| us)? (?:a |another |one more |the )?stor(?:y|ies)\b|\bstory time\b"
                   r"|\b(?:another|one more|next) story\b|\bstory (?:about|of)\b|\bstories\b"
                   r"|\btell me (?:your|ur|one|some) stor|\b(?:and other|an other|a nother) stor")   # fix 80
RIDDLE = re.compile(r"\briddles?\b|\bpuzzle\b")
QUIZ_RX = re.compile(r"\bquiz\b|\bask me (?:a )?questions?\b|\btest me\b")
HELP = re.compile(r"^(?:what can (?:you|we|i) do|what can we play|help|what should we do|i'?m bored|i am bored)$")
STOP_GAME = re.compile(r"^(?:stop|stop the (?:quiz|riddles?)|no more|enough|i'?m done|that'?s enough)$")
THINKING = re.compile(r"^(?:h+m+|u+m+|uh+|err*|wait|let me think|i'?m thinking|thinking|one (?:minute|second|sec)"
                      r"|hold on|ok(?:ay)?|hmm+ let me think|(?:give me )?(?:a )?(?:minute|second))$")   # fix 78
HINT = re.compile(r"\bhints?\b|\bclue\b|\bhelp me\b")
DONT_KNOW = re.compile(r"\b(?:i )?(?:don'?t know|no idea|tell me|give up|i give up|what is it|skip)\b")
GROWN_UP = re.compile(
    r"\b(?:remind\w*|notes?|alarm|timer|list|schedule|to-?do|tasks?|plans?|meeting|remember|forget|memor\w*|period|cycle"
    r"|journal|mood|focus|weather|news|look (?:it )?up|search|online|internet|offline|settings?|voice|city|pin"
    r"|password|delete|erase|remove|clear|share|qr|e-?mail|call (?:my|mom|dad|someone)|message|buy|order|money|bank"
    r"|address|phone|summary|my day|change my|learn\w*|fix|correct\w*)\b")

_story_order = []
_pending = None          # {"kind": "riddle" | "quiz", "i": index, "score", "asked", "t"}
_state = {"on": None}


def is_on():                                               # fix 80: no database needed
    return bool(_state["on"])


STATUS_Q = re.compile(r"^(?:is|are)\b.*\b(?:kids?|child|children)(?:'s)? mode\b"
                      r"|\b(?:kids?|child)(?:'s)? mode (?:on |off )?or (?:not|off|on)\b")   # fix 80


def on(memory):
    if _state["on"] is None:
        _state["on"] = (memory.get_setting("kid_mode") == "on")
    return _state["on"]


def turn_on(memory):
    memory.set_setting("kid_mode", "on")
    _state["on"] = True
    return ("Kid mode is on! I can tell stories, riddles and jokes, do a fun quiz, play games, and help with spelling "
            "and maths. The internet is off, and grown-up things are hidden. Remember, I'm not a babysitter, "
            "so a grown-up should stay nearby. What shall we do first?")


def turn_off(memory):
    global _pending
    memory.set_setting("kid_mode", "off")
    _state["on"] = False
    _pending = None
    return "Kid mode is off. Welcome back!"


def _story(lower):
    global _story_order
    words = set(re.findall(r"[a-z]+", lower)) - {"tell", "me", "a", "the", "story", "about", "of", "another", "one",
                                                  "more", "please", "us", "read", "and"}
    if words:
        for title, text in STORIES:
            if words & set(re.findall(r"[a-z]+", title.lower())):
                return title, text
    if not _story_order:
        _story_order = random.sample(range(len(STORIES)), len(STORIES))
    title, text = STORIES[_story_order.pop()]
    return title, text


def _card(title, lines):
    return {"title": "🧸 " + title, "lines": lines}


def handle(text, memory):
    """While kid mode is on. 'off' = a grown-up wants to turn it off (needs the PIN);
    {"say", "card"} = a kid-mode answer; None = allowed, the rest of Jarvis answers (games, jokes, maths...)."""
    global _pending
    lower = re.sub(r"^(?:hey |ok |okay )?jarvis,?\s*", "", text.lower().strip(" .!?")).replace("’", "'")
    if OFF.search(lower):
        return "off"

    if _pending and time.time() - _pending["t"] < 600:
        p = _pending
        if STOP_GAME.match(lower):
            _pending = None
            extra = (f" You got {p['score']} out of {p['asked']}. " + ("Well done!" if p["score"] else "Good try!")
                     if p["kind"] == "quiz" and p["asked"] else "")
            return {"say": "Okay, we'll stop." + extra + " Want a story or a joke?", "card": None}
        bank = RIDDLES if p["kind"] == "riddle" else QUIZ
        q, answer, rx, hint = bank[p["i"]]
        p["t"] = time.time()
        if (STORY.search(lower) or re.search(r"\bjokes?\b|\bgames?\b|\bplay\b", lower)
                or (QUIZ_RX.search(lower) and p["kind"] == "riddle") or (RIDDLE.search(lower) and p["kind"] == "quiz")) \
                and not re.search(rx, lower):
            _pending = None                                    # they want something else now
            p = None
    if _pending and time.time() - _pending["t"] < 600:
        p = _pending
        if THINKING.match(lower):                              # fix 78: thinking is fine, no answer yet
            return {"say": "Take your time! Say: hint, if you want a clue.", "card": None}
        if HINT.search(lower) and not re.search(rx, lower):
            p["hinted"] = True
            return {"say": f"Here's a clue: {hint}", "card": _card("Clue", [q, "💡 " + hint])}
        if re.search(rx, lower):
            p["score"] += 1
            said = random.choice(["Yes! That's right!", "Correct! Well done!", "You got it! Super!"])
        elif DONT_KNOW.search(lower) or p.get("tries", 0) >= 1:
            said = (f"That's okay! The answer is {answer}." if DONT_KNOW.search(lower)
                    else f"Good try! The answer is {answer}.")
        else:
            p["tries"] = p.get("tries", 0) + 1                  # one more try, with a clue
            return {"say": f"Not quite! Here's a clue: {hint} Try again!", "card": _card("Clue", [q, "💡 " + hint])}
        p["tries"] = 0
        p["asked"] += 1
        if p["kind"] == "quiz" and p["asked"] >= 5:
            _pending = None
            return {"say": f"{said} That's the end of the quiz. You got {p['score']} out of 5. "
                           + ("Amazing!" if p["score"] >= 4 else "Great effort!"), "card": None}
        nxt = random.choice([i for i in range(len(bank)) if i not in p["seen"]] or [p["i"]])
        p["seen"].add(nxt)
        p.update(i=nxt, t=time.time())
        p["tries"] = 0
        lead = "Next riddle" if p["kind"] == "riddle" else f"Question {p['asked'] + 1}"
        more = " Or say stop." if p["kind"] == "riddle" else ""
        return {"say": f"{said} {lead}: {bank[nxt][0]}{more}", "card": _card(lead, [bank[nxt][0]])}

    if re.search(r"\b(?:don'?t|do not|dont|no|not|never|no more)\b.{0,25}\b(?:story|stories|riddles?|quiz|listen)\b", lower):
        if re.search(r"\bjokes?\b|\bgames?\b|\bplay\b", lower):
            return None                                     # "I don't want a story, tell me a joke": the joke
        return {"say": "Okay! No problem. Want a joke, a riddle, or a game instead?", "card": None}   # fix 77
    if HELP.match(lower):
        return {"say": "We can do lots of things! Say: tell me a story, tell me a riddle, start a quiz, tell me a joke, "
                       "let's play a game, or how do you spell elephant.", "card": None}
    if STORY.search(lower):
        title, story = _story(lower)
        return {"say": f"Here's a story: {title}.", "story": (title, story_parts(story)), "card": None}   # fix 77
    if RIDDLE.search(lower):
        i = random.randrange(len(RIDDLES))
        _pending = {"kind": "riddle", "i": i, "score": 0, "asked": 0, "seen": {i}, "t": time.time()}
        return {"say": f"Here's a riddle: {RIDDLES[i][0]}", "card": _card("Riddle", [RIDDLES[i][0]])}
    if QUIZ_RX.search(lower):
        i = random.randrange(len(QUIZ))
        _pending = {"kind": "quiz", "i": i, "score": 0, "asked": 0, "seen": {i}, "t": time.time()}
        return {"say": f"Quiz time! 5 easy questions. Question 1: {QUIZ[i][0]}", "card": _card("Quiz", [QUIZ[i][0]])}
    m = re.match(r"^(?:my name is|call me|my name's)\s+([a-z]+)$", lower)
    if m:                                                   # nice to meet you, but nothing is saved
        return {"say": f"Nice to meet you, {m.group(1).title()}! Want a story, a riddle, or a quiz?", "card": None}
    if GROWN_UP.search(lower):
        return {"say": "That's a grown-up thing, so let's leave it for later! Want a story, a riddle, a quiz, "
                       "a joke, or a game?", "card": None}
    if re.search(r"\bmy\b", lower) and re.match(r"(?:what|where|when|who|do you know|tell me)\b", lower):
        return {"say": "I keep grown-up things private in kid mode. But I'd love to hear about you! "
                       "Or shall we play a game?", "card": None}
    return None


def waiting():
    """A riddle or quiz question is waiting for an answer (so silence means: still thinking)."""
    return bool(_pending) and time.time() - _pending["t"] < 600


def nudge(n):
    """Said when the child is quiet while thinking: patient, then a clue, then the answer."""
    global _pending
    p = _pending
    bank = RIDDLES if p["kind"] == "riddle" else QUIZ
    q, answer, rx, hint = bank[p["i"]]
    p["t"] = time.time()
    if n == 1:
        return "Take your time! I'm listening. Say your answer, or say: hint."
    if n == 2:
        p["hinted"] = True
        return f"Here's a clue: {hint}"
    if n == 3:
        return f"Still thinking? The question was: {q}"
    _pending = None
    return f"The answer was {answer}. Let's play again later! Just say: tell me a riddle."


BAD = re.compile(r"\b(?:kill\w*|blood\w*|dead|death|die[ds]?|dying|gun\w*|knife|knives|weapon\w*|bomb\w*|drug\w*"
                 r"|alcohol|beer|wine|drunk|cigarette\w*|smok\w*|sex\w*|naked|hate|stupid|idiot|scary|horror"
                 r"|monster\w*|ghost\w*|war|fight\w*|hurt\w*|steal\w*|money|medicine\w*|pill\w*|dose)\b", re.I)


def safe_reply(reply):
    """The AI's answer to a child: anything that isn't child-friendly is replaced."""
    if BAD.search(reply) or len(reply.split()) > 60:
        return "Hmm, let's ask a grown-up about that one. Want to hear a story instead?"
    return reply
