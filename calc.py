"""Calculator and unit converter (fix 62). Plain Python: exact, instant, offline. No AI.

  "what is 245 times 18"            "18 percent of 2450"          "split 1850 between 4 people"
  "add 18 percent GST to 1000"      "20 percent off on 2000"      "450 is what percent of 2500"
  "square root of 144"              "convert 5 km to miles"       "38.5 degrees celsius in fahrenheit"
Edit right after: "make it 5 people", "what about 20 percent", "change 18 to 20", "and in pounds?"
"""
import ast
import math
import operator
import re
import time

# ---------------------------------------------------------------- numbers said as words
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                     "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "lakhs": 100000, "lac": 100000, "lacs": 100000,
           "crore": 10 ** 7, "crores": 10 ** 7, "million": 10 ** 6, "billion": 10 ** 9}
_NUMWORD = set(_UNITS) | set(_TENS) | set(_SCALES)


def _words_to_digits(s):
    """'two hundred and forty five times eighteen' -> '245 times 18'"""
    toks = re.findall(r"[a-z]+|\d[\d.]*|[^\sa-z\d]+", s)
    out, i = [], 0
    while i < len(toks):
        if toks[i] in _UNITS or toks[i] in _TENS or (toks[i] in ("a",) and i + 1 < len(toks)
                                                         and toks[i + 1] in ("hundred", "thousand", "lakh", "crore", "million")):
            total, cur, j = 0, 0, i
            while j < len(toks):
                w = toks[j]
                if w == "a" and j == i:
                    cur = 1
                elif w in _UNITS:
                    cur += _UNITS[w]
                elif w in _TENS:
                    cur += _TENS[w]
                elif w == "hundred":
                    cur = (cur or 1) * 100
                elif w in _SCALES:
                    total += (cur or 1) * _SCALES[w]
                    cur = 0
                elif w == "and" and j + 1 < len(toks) and toks[j + 1] in _NUMWORD and j > i:
                    pass
                else:
                    break
                j += 1
            val = total + cur
            if j < len(toks) - 1 and toks[j] == "point" and (toks[j + 1] in _UNITS or toks[j + 1].isdigit()):
                dec = ""
                j += 1
                while j < len(toks) and (toks[j] in _UNITS and _UNITS[toks[j]] < 10 or toks[j].isdigit()):
                    dec += str(_UNITS[toks[j]]) if toks[j] in _UNITS else toks[j]
                    j += 1
                out.append(f"{val}.{dec}")
            else:
                out.append(str(val))
            i = j
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def _clean(text):
    s = text.lower().strip()
    s = s.replace("×", " times ").replace("÷", " divided by ").replace("−", "-")
    s = re.sub(r"(?<=\d),(?=\d)", "", s)                                  # 1,850 / 1,00,000
    s = re.sub(r"[₹$]|\brs\.?(?=\s*\d)|\binr\b", " ", s)
    s = re.sub(r"\b(?:rupees?|bucks)\b", " ", s)
    s = re.sub(r"\bwhat'?s\b", "what is", s).replace("’", "'")
    s = s.replace("%", " percent ").replace("per cent", "percent")
    s = re.sub(r"[?!]|\.(?!\d)", " ", s)                                  # keep 2.5, drop the full stop
    s = _words_to_digits(s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s*k\b", lambda m: str(float(m.group(1)) * 1000), s)
    s = re.sub(r"(\d+(?:\.\d+)?)\s+(hundred|thousand|lakhs?|lacs?|crores?|million|billion)\b",
               lambda m: _num_str(float(m.group(1)) * _SCALES[m.group(2)]), s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    s = re.sub(r"^(?:hey |ok |okay )?(?:jarvis,? )?", "", s)
    s = re.sub(r",?\s*(?:please|jarvis)$", "", s).strip(" ,")
    return s


def _num_str(x):
    return str(int(x)) if float(x).is_integer() else str(x)


# ---------------------------------------------------------------- saying numbers
def fmt(x):
    if abs(x) >= 1e15:
        return f"{x:.3g}"
    if abs(x - round(x)) < 1e-9:
        return f"{int(round(x)):,}"
    if abs(x) < 0.01:
        return f"{x:.3g}"
    return f"{x:,.2f}".rstrip("0").rstrip(".")


def _indian(x):
    """Big amounts also in lakh / crore."""
    if abs(x) >= 10 ** 7:
        return f", that's {fmt(round(x / 10 ** 7, 2))} crore"
    if abs(x) >= 10 ** 5:
        return f", that's {fmt(round(x / 10 ** 5, 2))} lakh"
    return ""


# ---------------------------------------------------------------- safe maths (never eval)
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"sqrt": math.sqrt, "cbrt": lambda v: math.copysign(abs(v) ** (1 / 3), v)}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 100 or abs(a) > 1e6):
            raise OverflowError
        return _OPS[type(node.op)](a, b)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and len(node.args) == 1:
        v = _eval(node.args[0])
        if node.func.id == "sqrt" and v < 0:
            raise ValueError
        return _FUNCS[node.func.id](v)
    raise ValueError


_N = r"(\d+(?:\.\d+)?)"
_LEAD = (r"(?:(?:so |and |now |then )?(?:what(?:'s| is| will be| would be)|whats|how much (?:is|will be)|calculate|"
         r"compute|can you (?:calculate|tell me|work out)|tell me|work out|find|solve|what do you get for)\s+)?")


def _to_expr(s):
    """The sentence as a Python expression, or None if it isn't pure maths."""
    e = re.sub(r"^" + _LEAD, "", s)
    e = re.sub(r"\s*(?:equals?|is equal to|=)\s*$", "", e)
    e = re.sub(r"^(?:the )?(?:square root|root) of\s*" + _N, r"sqrt(\1)", e)
    e = re.sub(r"(?:the )?square root of\s*" + _N, r"sqrt(\1)", e)
    e = re.sub(r"(?:the )?cube root of\s*" + _N, r"cbrt(\1)", e)
    e = re.sub(r"^add " + _N + r" (?:and|to|plus) " + _N + "$", r"\1 + \2", e)
    e = re.sub(r"^(?:the )?sum of " + _N + r" and " + _N + "$", r"\1 + \2", e)
    e = re.sub(r"^subtract " + _N + r" from " + _N + "$", r"\2 - \1", e)
    e = re.sub(r"^multiply " + _N + r" (?:by|and|with) " + _N + "$", r"\1 * \2", e)
    e = re.sub(r"^divide " + _N + r" by " + _N + "$", r"\1 / \2", e)
    e = re.sub(r"^(?:the )?product of " + _N + r" and " + _N + "$", r"\1 * \2", e)
    e = re.sub(_N + r"\s*squared", r"(\1**2)", e)
    e = re.sub(_N + r"\s*cubed", r"(\1**3)", e)
    for pat, sym in ((r"to the power of|raised to(?: the power of)?|to the power|power", "**"),
                     (r"multiplied by|times|into|x|\*", "*"), (r"divided by|by|over|/", "/"),
                     (r"plus|\+|added to", "+"), (r"minus|less|-", "-")):
        e = re.sub(r"(?<=[\d)])\s*(?:" + pat + r")\s*(?=[\d(s]|cbrt)", f" {sym} ", e)
    e = re.sub(r"\s+", " ", e).strip()
    if not re.fullmatch(r"[\d.\s+\-*/()]*(?:(?:sqrt|cbrt)\([\d.]+\)[\d.\s+\-*/()]*)*", e):
        return None
    if not re.search(r"[+\-*/]|sqrt|cbrt", e) or not re.search(r"\d", e):
        return None
    return e


def _spoken(expr):
    s = expr.replace("**2)", ") squared").replace("**3)", ") cubed")
    s = re.sub(r"sqrt\(([\d.]+)\)", r"the square root of \1", s)
    s = re.sub(r"cbrt\(([\d.]+)\)", r"the cube root of \1", s)
    s = re.sub(r"[\d.]+", lambda m: fmt(float(m.group())), s)
    for sym, w in (("**", " to the power of "), ("*", " times "), ("/", " divided by "), ("+", " plus "),
                   (" - ", " minus ")):
        s = s.replace(sym, w)
    return re.sub(r"\s+", " ", s.replace("(", "").replace(")", "")).strip()


# ---------------------------------------------------------------- units
UNITS = {   # name: (kind, factor to the base unit, spoken singular, spoken plural)
    "km": ("len", 1000, "kilometre", "kilometres"), "m": ("len", 1, "metre", "metres"),
    "cm": ("len", 0.01, "centimetre", "centimetres"), "mm": ("len", 0.001, "millimetre", "millimetres"),
    "mile": ("len", 1609.344, "mile", "miles"), "ft": ("len", 0.3048, "foot", "feet"),
    "inch": ("len", 0.0254, "inch", "inches"), "yard": ("len", 0.9144, "yard", "yards"),
    "kg": ("mass", 1, "kilogram", "kilograms"), "g": ("mass", 0.001, "gram", "grams"),
    "mg": ("mass", 1e-6, "milligram", "milligrams"), "lb": ("mass", 0.45359237, "pound", "pounds"),
    "oz": ("mass", 0.028349523125, "ounce", "ounces"), "tonne": ("mass", 1000, "tonne", "tonnes"),
    "quintal": ("mass", 100, "quintal", "quintals"),
    "l": ("vol", 1, "litre", "litres"), "ml": ("vol", 0.001, "millilitre", "millilitres"),
    "gallon": ("vol", 3.785411784, "gallon", "gallons"),
    "sec": ("time", 1, "second", "seconds"), "min": ("time", 60, "minute", "minutes"),
    "hr": ("time", 3600, "hour", "hours"), "day": ("time", 86400, "day", "days"),
    "week": ("time", 604800, "week", "weeks"), "year": ("time", 31536000, "year", "years"),
    "c": ("temp", None, "degree Celsius", "degrees Celsius"), "f": ("temp", None, "degree Fahrenheit",
                                                                   "degrees Fahrenheit"),
    "k": ("temp", None, "kelvin", "kelvin"),
}
ALIASES = {
    "km": "kilometres? kilometers? kms? kilo_?meters? kilo_?metres?", "m": "metres? meters? m",
    "cm": "centimetres? centimeters? cms?", "mm": "millimetres? millimeters? mm", "mile": "miles? mi",
    "ft": "feet foot ft", "inch": "inch(?:es)? inch", "yard": "yards? yd",
    "kg": "kilograms? kilogrammes? kilos? kgs? kg", "g": "grams? grammes? gms? g", "mg": "milligrams? mg",
    "lb": "pounds? lbs? lb", "oz": "ounces? oz", "tonne": "tonnes? tons?", "quintal": "quintals?",
    "l": "litres? liters? ltrs? l", "ml": "millilitres? milliliters? ml", "gallon": "gallons?",
    "sec": "seconds? secs?", "min": "minutes? mins?", "hr": "hours? hrs?", "day": "days?", "week": "weeks?",
    "year": "years?",
    "c": "(?:degrees?_)?(?:celsius|centigrade|c)", "f": "(?:degrees?_)?(?:fahrenheit|farenheit|f)",
    "k": "(?:degrees?_)?kelvin",
}
_ALIAS_RE = [(u, re.compile(r"(?:" + "|".join(a.replace("_", " ") for a in ALIASES[u].split()) + r")$")) for u in ALIASES]
_UNIT_WORD = r"((?:degrees? )?[a-z]+(?: ?(?:meters?|metres?))?)"


def _unit(word):
    word = (word or "").strip()
    word = re.sub(r"^degrees? (?=[a-z])", lambda m: m.group(), word)
    for u, rx in _ALIAS_RE:
        if rx.fullmatch(word):
            return u
    return None


def _convert(v, a, b):
    ka, kb = UNITS[a][0], UNITS[b][0]
    if ka != kb:
        return None
    if ka == "temp":
        c = v if a == "c" else (v - 32) * 5 / 9 if a == "f" else v - 273.15
        return c if b == "c" else c * 9 / 5 + 32 if b == "f" else c + 273.15
    return v * UNITS[a][1] / UNITS[b][1]


def _uname(u, v):
    return UNITS[u][2] if abs(v - 1) < 1e-9 else UNITS[u][3]


# ---------------------------------------------------------------- the answers
LAST = None          # the last calculation, so it can be edited: {"kind", ..., "t"}


def _answer(c):
    k = c["kind"]
    if k == "expr":
        try:
            r = _eval(ast.parse(c["expr"], mode="eval"))
        except ZeroDivisionError:
            return "You can't divide by zero."
        except (OverflowError, ValueError, SyntaxError):
            return "That number is too big or not possible, sorry."
        return f"{_spoken(c['expr'])[:1].upper() + _spoken(c['expr'])[1:]} is {fmt(r)}{_indian(r)}."
    if k == "pct_of":
        r = c["p"] / 100 * c["m"]
        return f"{fmt(c['p'])} percent of {fmt(c['m'])} is {fmt(r)}{_indian(r)}."
    if k == "add_pct":
        extra = c["p"] / 100 * c["m"]
        lab = c.get("label") or ""
        if lab:
            return (f"{lab.upper() if lab == 'gst' else lab.capitalize()} at {fmt(c['p'])} percent on {fmt(c['m'])} "
                    f"is {fmt(extra)}, so the total is {fmt(c['m'] + extra)}{_indian(c['m'] + extra)}.")
        return f"{fmt(c['m'])} plus {fmt(c['p'])} percent is {fmt(c['m'] + extra)}{_indian(c['m'] + extra)}."
    if k == "off_pct":
        save = c["p"] / 100 * c["m"]
        return f"{fmt(c['p'])} percent off {fmt(c['m'])} is {fmt(c['m'] - save)}. You save {fmt(save)}."
    if k == "what_pct":
        if c["b"] == 0:
            return "You can't take a percentage of zero."
        return f"{fmt(c['a'])} is {fmt(round(c['a'] / c['b'] * 100, 2))} percent of {fmt(c['b'])}."
    if k == "split":
        if c["n"] <= 0:
            return "I need at least one person to split it between."
        each = c["m"] / c["n"]
        each_s = fmt(each) if each.is_integer() else f"{each:,.2f}".rstrip("0").rstrip(".")
        return f"{fmt(c['m'])} split between {fmt(c['n'])} people is {each_s} each."
    if k == "conv":
        r = _convert(c["v"], c["a"], c["b"])
        if r is None:
            return f"I can't turn {UNITS[c['a']][3]} into {UNITS[c['b']][3]}, they measure different things."
        exact = abs(r - round(r, 2)) < 1e-9
        return (f"{fmt(c['v'])} {_uname(c['a'], c['v'])} is {'' if exact else 'about '}{fmt(r)} "
                f"{_uname(c['b'], r)}.")
    return None


_PCT = _N + r" percent"
_LABEL = r"(?: (gst|tax|tip|vat|service charge|interest))?"
_PATTERNS = [
    # 450 is what percent of 2500 / what percent of 2500 is 450 / what percentage is 450 of 2500
    (re.compile(r"(?:what(?:'s| is)? )?" + _N + r" is what percent(?:age)? of " + _N), lambda m: dict(kind="what_pct", a=float(m[1]), b=float(m[2]))),
    (re.compile(r"what percent(?:age)? (?:of " + _N + r" is " + _N + r")"), lambda m: dict(kind="what_pct", a=float(m[2]), b=float(m[1]))),
    (re.compile(r"what percent(?:age)? is " + _N + r" (?:of|out of) " + _N), lambda m: dict(kind="what_pct", a=float(m[1]), b=float(m[2]))),
    # add 18 percent gst to 1000 / 1000 plus 18 percent gst / 1000 with 18 percent tip
    (re.compile(r"add " + _PCT + _LABEL + r" (?:to|on|in) " + _N), lambda m: dict(kind="add_pct", p=float(m[1]), m=float(m[3]), label=m[2])),
    (re.compile(_N + r" (?:plus|\+|with|including|inclusive of|and) " + _PCT + _LABEL), lambda m: dict(kind="add_pct", p=float(m[2]), m=float(m[1]), label=m[3])),
    (re.compile(r"(?:a |an )?" + _PCT + r" (gst|tax|tip|vat|service charge) (?:on|of|for) " + _N), lambda m: dict(kind="add_pct", p=float(m[1]), m=float(m[3]), label=m[2])),
    # 20 percent off 2000 / 20 percent discount on 2000 / 2000 minus 20 percent / 2000 with 20 percent discount
    (re.compile(_PCT + r" (?:off|discount)(?: on| of| from)? " + _N), lambda m: dict(kind="off_pct", p=float(m[1]), m=float(m[2]))),
    (re.compile(_N + r" (?:minus|-|less|after|with(?: a)?) " + _PCT + r"(?: off| discount)?"), lambda m: dict(kind="off_pct", p=float(m[2]), m=float(m[1]))),
    (re.compile(r"(?:subtract|take|remove|deduct) " + _PCT + r" (?:from|off|of) " + _N), lambda m: dict(kind="off_pct", p=float(m[1]), m=float(m[2]))),
    # 18 percent of 2450
    (re.compile(_PCT + r" of " + _N), lambda m: dict(kind="pct_of", p=float(m[1]), m=float(m[2]))),
    # split 1850 between 4 people / divide 1850 among 4 friends / 1850 split 4 ways
    (re.compile(r"(?:split|divide|share) (?:the bill of |a bill of |the )?" + _N + r" (?:between|among|amongst|by|in|into|for|with) " + _N + r"(?: (?:people|persons|friends|ways|of us|parts|members))?"),
     lambda m: dict(kind="split", m=float(m[1]), n=float(m[2]))),
    (re.compile(_N + r" (?:split|divided|shared) (?:between|among|amongst|by|in|into|for|with)? ?" + _N + r" (?:people|persons|friends|ways|of us|members)"),
     lambda m: dict(kind="split", m=float(m[1]), n=float(m[2]))),
    # convert 5 km to miles / 5 km in miles / how many grams in 2 kg
    (re.compile(r"(?:convert )?" + _N + r" ?" + _UNIT_WORD + r" (?:in|to|into|in to|as) " + _UNIT_WORD),
     lambda m: _conv(m[1], m[2], m[3])),
    (re.compile(r"how many " + _UNIT_WORD + r" (?:are there |is there )?(?:in|is|are|make|equals?|=) (?:a |an |one )?" + _N + r"? ?" + _UNIT_WORD),
     lambda m: _conv(m[2] or "1", m[3], m[1])),
]
_PREFIX = re.compile(r"^" + _LEAD + r"(?:the )?")
_SUFFIX = re.compile(r"\s*(?:each|per person|in total|total|exactly|equals?|is)?$")


def _conv(v, a, b):
    ua, ub = _unit(a), _unit(b)
    if not ua or not ub or ua == ub:
        return None
    return dict(kind="conv", v=float(v), a=ua, b=ub)


def _parse(s):
    body = _SUFFIX.sub("", _PREFIX.sub("", s)).strip()
    for rx, make in _PATTERNS:
        m = rx.fullmatch(body)
        if m:
            c = make(m)
            if c:
                return c
    e = _to_expr(s)
    if e:
        return dict(kind="expr", expr=e)
    return None


# ---------------------------------------------------------------- edits: "make it 5 people", "change 18 to 20"
def _edit(s):
    c = dict(LAST)
    k = c["kind"]
    s = re.sub(r"^(?:no,? |oh,? |sorry,? |actually,? |wait,? )+", "", s)
    lead = r"(?:(?:and |so )?(?:make it|what about|how about|what if it'?s|what if|and|for|with|instead|change it to|now)\s+)"
    m = re.fullmatch(r"change " + _N + r" to " + _N + r"(?: percent| people)?", s) \
        or re.fullmatch(r"(?:not|instead of) " + _N + r",? (?:but |it'?s |make it )?" + _N, s)
    if m:
        old, new = float(m[1]), float(m[2])
        if k == "expr":
            parts = re.split(r"([\d.]+)", c["expr"])
            hit = [i for i, p in enumerate(parts) if re.fullmatch(r"[\d.]+", p) and float(p) == old]
            if not hit:
                return None
            parts[hit[-1]] = _num_str(new)
            c["expr"] = "".join(parts)
        else:
            keys = [key for key in ("p", "m", "n", "a", "b", "v") if key in c and c[key] == old]
            if not keys:
                return None
            c[keys[0]] = new
        return c
    m = re.fullmatch(lead + "?" + _N + r" (?:people|persons|friends|ways|of us|members)", s)
    if m and k == "split":
        c["n"] = float(m[1])
        return c
    m = re.fullmatch(lead + "?" + _PCT + r"(?: instead)?", s)
    if m and k in ("pct_of", "add_pct", "off_pct"):
        c["p"] = float(m[1])
        return c
    m = re.fullmatch(r"(?:and |what about |how about |now |make it |and what about )?(?:in|to|into) " + _UNIT_WORD
                     + r"(?: instead)?", s)
    if m and k == "conv":
        u = _unit(m[1])
        if u and UNITS[u][0] == UNITS[c["a"]][0]:
            c["b"] = u
            return c
    m = re.fullmatch(lead + _N + r"(?: instead)?", s)
    if m:
        v = float(m[1])
        if k == "split":
            c["n"] = v
        elif k in ("pct_of", "add_pct", "off_pct"):
            c["p"] = v
        elif k == "conv":
            c["v"] = v
        else:
            return None
        return c
    m = re.fullmatch(lead + _N + r" ?" + _UNIT_WORD + r"(?: instead)?", s)
    if m and k == "conv" and _unit(m[2]) == c["a"]:
        c["v"] = float(m[1])
        return c
    return None


def handle(text, edits_ok=True):
    """A spoken answer for a maths / percent / split / unit question, or None if it isn't one."""
    global LAST
    s = _clean(text)
    if not s:
        return None
    if edits_ok and LAST and time.time() - LAST["t"] < 180:
        c = _edit(s)
        if c:
            c["t"] = time.time()
            LAST = c
            return _answer(c)
    if not re.search(r"\d", s):
        if not re.match(r"how many ", s):
            return None
        s = re.sub(r"\b(?:in|is|make) (?:a|an|one) ", lambda m: m.group().rsplit(" ", 2)[0] + " 1 ", s)
    c = _parse(s)
    if not c:
        return None
    c["t"] = time.time()
    LAST = c
    return _answer(c)


def forget():
    global LAST
    LAST = None
