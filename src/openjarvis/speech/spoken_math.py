"""Say a formula instead of spelling it.

The model answers engineering questions in LaTeX, and the voice read
"backslash sum F underscore x equals zero backslash quad" aloud. This turns
the common notation into the words a lecturer would say -- "the sum of F x
equals zero" -- and leaves anything it does not understand as plain letters
and digits, never as backslashes and braces.

It is deliberately small: the dozen constructs that appear in statics,
dynamics, surveying and the like. A formula it cannot read becomes its
operands spoken in order, which is still better than the punctuation.
"""

from __future__ import annotations

import re

# Delimiters the model uses: $$…$$, $…$, \[…\], \(…\), and the bare
# "[ \sum … ]" / "( 0^\circ )" left when markdown ate the backslash.
_DISPLAY = re.compile(r"\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]")
_INLINE = re.compile(r"(?<![\\$\w])\$([^$\n]+?)\$(?!\w)|\\\(([\s\S]+?)\\\)")
_TEX_COMMAND = re.compile(r"\\[a-zA-Z]+|\^\{|_\{")
_BARE_BRACKET = re.compile(r"\[\s([^\[\]\n]+?)\s\]")
_BARE_PAREN = re.compile(r"\(([^()\n]*?(?:\\[a-zA-Z]+|\^\\circ)[^()\n]*?)\)")

_GREEK = {
    "alpha": "alpha",
    "beta": "beta",
    "gamma": "gamma",
    "delta": "delta",
    "Delta": "delta",
    "epsilon": "epsilon",
    "theta": "theta",
    "Theta": "theta",
    "lambda": "lambda",
    "mu": "mu",
    "pi": "pi",
    "rho": "rho",
    "sigma": "sigma",
    "Sigma": "sigma",
    "tau": "tau",
    "phi": "phi",
    "Phi": "phi",
    "omega": "omega",
    "Omega": "omega",
    "nu": "nu",
    "eta": "eta",
    "kappa": "kappa",
    "zeta": "zeta",
}
_WORDS = {
    "sum": "the sum of",
    "int": "the integral of",
    "prod": "the product of",
    "sqrt": "the square root of",
    "infty": "infinity",
    "perp": "perpendicular",
    "parallel": "parallel to",
    "cdot": "times",
    "times": "times",
    "div": "divided by",
    "pm": "plus or minus",
    "mp": "minus or plus",
    "le": "less than or equal to",
    "leq": "less than or equal to",
    "ge": "greater than or equal to",
    "geq": "greater than or equal to",
    "ne": "not equal to",
    "neq": "not equal to",
    "approx": "approximately",
    "propto": "proportional to",
    "rightarrow": "gives",
    "to": "to",
    "circ": "degrees",
    "degree": "degrees",
    "partial": "partial",
    "nabla": "nabla",
    "angle": "angle",
    "quad": " ",
    "qquad": " ",
    ",": "",
    "left": "",
    "right": "",
    "displaystyle": "",
    "text": "",
    "mathrm": "",
    "mathbf": "",
    "vec": "vector",
    "hat": "hat",
    "bar": "bar",
    "dot": "dot",
    "ddot": "double dot",
    "ln": "the natural log of",
    "log": "log of",
    "sin": "sine of",
    "cos": "cosine of",
    "tan": "tangent of",
}


def _braces(text: str, start: int) -> tuple[str, int]:
    """The contents of a {…} group starting at *start*, and where it ends."""
    if start >= len(text) or text[start] != "{":
        # TeX takes a single token when there are no braces: \frac12 is one
        # half, x^2y is x squared times y, d_\perp is d perpendicular.
        m = re.match(r"\s*(\\[A-Za-z]+|[A-Za-z0-9])", text[start:])
        if not m:
            return "", start
        return m.group(1), start + m.end()
    depth, i = 0, start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1 : i], i + 1
        i += 1
    return text[start + 1 :], len(text)


def _frac(text: str) -> str:
    out = []
    i = 0
    while True:
        j = text.find("\\frac", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        k = j + len("\\frac")
        num, k = _braces(text, k)
        den, k = _braces(text, k)
        out.append(f" {speak_formula(num)} over {speak_formula(den)} ")
        i = k
    return "".join(out)


def _sqrt(text: str) -> str:
    out, i = [], 0
    while True:
        j = text.find("\\sqrt", i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        body, k = _braces(text, j + len("\\sqrt"))
        out.append(f" the square root of {speak_formula(body)} ")
        i = k
    return "".join(out)


def _powers(text: str) -> str:
    """Superscripts and subscripts, braces nested or not."""
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch in "^_":
            arg, k = _braces(text, i + 1)
            if k == i + 1:
                out.append(ch)
                i += 1
                continue
            if ch == "^":
                if arg == "2":
                    out.append(" squared")
                elif arg == "3":
                    out.append(" cubed")
                elif arg in ("\\circ", "circ"):
                    out.append(" degrees")
                else:
                    out.append(f" to the power of {speak_formula(arg)}")
            else:
                out.append(f" {speak_formula(arg)}")
            i = k
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def speak_formula(tex: str) -> str:
    """Words for one formula body (no delimiters)."""
    text = tex.strip()
    if not text:
        return ""
    text = _frac(text)
    text = _sqrt(text)
    text = _powers(text)

    def command(m: re.Match) -> str:
        name = m.group(1)
        if name in _GREEK:
            return f" {_GREEK[name]} "
        if name in _WORDS:
            return f" {_WORDS[name]} "
        return f" {name} "

    text = re.sub(r"\\([A-Za-z]+)", command, text)
    text = text.replace("\\,", " ").replace("\\;", " ").replace("\\!", "")
    text = re.sub(r"[{}]", " ", text)
    text = text.replace("=", " equals ").replace("+", " plus ")
    text = re.sub(r"(?<=\S)\s*-\s*(?=\S)", " minus ", text)
    text = text.replace("*", " times ").replace("/", " over ")
    text = text.replace("<", " less than ").replace(">", " greater than ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+,", ",", text)
    return text


def _replace(m: re.Match) -> str:
    body = next(g for g in m.groups() if g is not None)
    return f" {speak_formula(body)} "


def speak_math(text: str) -> str:
    """Replace every formula in *text* with its spoken form."""
    if "\\" not in text and "$" not in text and "^" not in text:
        return text
    text = _DISPLAY.sub(_replace, text)
    text = _INLINE.sub(_replace, text)
    text = _BARE_BRACKET.sub(
        lambda m: (
            f" {speak_formula(m.group(1))} "
            if _TEX_COMMAND.search(m.group(1))
            else m.group(0)
        ),
        text,
    )
    text = _BARE_PAREN.sub(
        lambda m: (
            f" {speak_formula(m.group(1))} "
            if _TEX_COMMAND.search(m.group(1))
            else m.group(0)
        ),
        text,
    )
    # A stray command outside any delimiter (the model forgot them). Only
    # commands this module knows: "C:\Program Files" is a path, not TeX,
    # and the path rules downstream must still see its backslashes.
    text = re.sub(
        r"\\([A-Za-z]+)\b",
        lambda m: (
            f" {_WORDS.get(m.group(1), _GREEK.get(m.group(1), ''))} "
            if m.group(1) in _WORDS or m.group(1) in _GREEK
            else m.group(0)
        ),
        text,
    )
    text = re.sub(r"[ \t]{2,}", " ", text)
    return re.sub(r" +([,.;:!?])", r"\1", text)


# Arithmetic typed as plain text rather than LaTeX ("= 101.425 − 2.115 =
# 99.310 m"). The voice was handed it raw: every such line ran away and
# came out as a cut-off garble or a shout (14 October, a levelling problem).

_NUMBER = r"\d[\d,]*(?:\.\d+)?"
#: Two numbers joined by an operator: the mark of a line of working. A
#: hyphen counts only spaced ("3 - 1"), so "2023-2024" stays a range.
_CALC = re.compile(
    rf"{_NUMBER}\)?\s*(?:[+×÷*−]|\s-\s|\\times|\\div|\\cdot)\s*\(?\s*-?{_NUMBER}"
)
_EQUALS = re.compile(r"=|≈|\\approx")
_LIST_MARK = re.compile(r"^\s*(?:[-*+•]|\d+[.)])\s+")


def is_working(line: str) -> bool:
    """Whether *line* is a step of arithmetic ("= 100.000 + 1.425 = 101.425").

    A result alone ("x = 5") or a rule in words ("elevation = HI −
    foresight") is not: those carry the answer, the working only shows it.
    """
    body = _LIST_MARK.sub("", line).replace("*", " ").replace("$", " ").strip()
    if not _EQUALS.search(body):
        return False
    return bool(_CALC.search(body)) or body.startswith(("=", "≈"))


_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve "
    "thirteen fourteen fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "- - twenty thirty forty fifty sixty seventy eighty ninety".split()


def _int_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        return f"{_ONES[hundreds]} hundred" + (f" {_int_words(rest)}" if rest else "")
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand")):
        if n >= size:
            high, rest = divmod(n, size)
            tail = f" {_int_words(rest)}" if rest else ""
            return f"{_int_words(high)} {name}{tail}"
    return str(n)


def decimal_words(number: str) -> str:
    """"101.425" -> "one hundred one point four two five" (the user's pick,
    14 October: the integer said whole, the decimals digit by digit, every
    written digit kept -- in surveying "100.060" is not "100.06")."""
    whole, _, fraction = number.replace(",", "").partition(".")
    if not whole.isdigit() or len(whole) > 12 or not fraction.isdigit():
        return number
    digits = " ".join(_ONES[int(d)] for d in fraction)
    return f"{_int_words(int(whole))} point {digits}"


#: Units said after a number. "m" needs its space: "5m" may be minutes or
#: millions.
_UNIT_WORDS = {
    "mm": "millimeters",
    "cm": "centimeters",
    "km": "kilometers",
    "m²": "square meters",
    "m³": "cubic meters",
    "m/s": "meters per second",
    "kg": "kilograms",
    "kN": "kilonewtons",
    "kPa": "kilopascals",
    "MPa": "megapascals",
    "ha": "hectares",
}
_UNIT = re.compile(
    rf"(?<![\w.])(?P<n>{_NUMBER})"
    r"(?:\s?(?P<u>m/s|m²|m³|mm|cm|km|kg|kN|kPa|MPa|ha)|\s(?P<m>m))"
    r"(?![\w²³/'’])"
)
_DECIMAL = re.compile(r"(?<![\w.])\d[\d,]*\.\d+(?![\w.]|\.\d)")


def _unit(match: re.Match) -> str:
    number = match.group("n")
    words = _UNIT_WORDS.get(match.group("u") or "", "meters")
    if number == "1":
        words = re.sub(r"s\b", "", words, count=1)
    spoken = decimal_words(number) if "." in number else number
    return f"{spoken} {words}"


#: Currency sign -> (one, many, one cent, many cents).
_CURRENCIES = {
    "$": ("dollar", "dollars", "cent", "cents"),
    "₱": ("peso", "pesos", "centavo", "centavos"),
    "€": ("euro", "euros", "cent", "cents"),
    "£": ("pound", "pounds", "penny", "pence"),
}
_SCALES = {"thousand": "thousand", "k": "thousand", "K": "thousand",
           "million": "million", "M": "million", "m": "million",
           "billion": "billion", "B": "billion", "bn": "billion",
           "trillion": "trillion"}
_MONEY = re.compile(
    r"(?<![\w$₱€£])(?P<sign>[$₱€£])\s?(?P<n>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<f>\d+))?"
    r"(?:\s?(?P<scale>thousand|million|billion|trillion|bn|[kKmMB])(?![\w]))?"
)


def _money(match: re.Match) -> str:
    """"$83,402" -> "eighty-three thousand four hundred two dollars". The
    local voice read the sign and the comma-grouped digits as noise (15
    October); words leave it nothing to guess."""
    one, many, cent, cents = _CURRENCIES[match.group("sign")]
    whole = match.group("n").replace(",", "")
    fraction = match.group("f") or ""
    scale = _SCALES.get(match.group("scale") or "")
    if len(whole) > 12:
        return match.group(0)
    if scale:
        # "$3.5 million": the number as said, then the scale and the unit.
        number = (
            decimal_words(f"{whole}.{fraction}") if fraction else _int_words(int(whole))
        )
        return f"{number} {scale} {many}"
    amount = int(whole)
    if fraction and len(fraction) != 2:
        return f"{decimal_words(f'{whole}.{fraction}')} {many}"
    small = int(fraction) if fraction else 0
    change = f"{_int_words(small)} {cent if small == 1 else cents}" if small else ""
    if amount == 0 and change:
        return change
    words = f"{_int_words(amount)} {one if amount == 1 else many}"
    return f"{words} and {change}" if change else words


def _speak_line(line: str) -> str:
    line = _MONEY.sub(_money, line)
    calc = bool(_CALC.search(line))
    equation = "=" in line
    if calc or re.search(r"^\s*=|(?<=\S) = (?=\S)", line):
        # "= 101.425" carries on from the line above: "That's 101.425".
        line = re.sub(r"^(\s*)=\s*", r"\1That's ", line)
        line = re.sub(r"\s*=\s*", " equals ", line)
        if equation:  # "won 3 - 1" is a score, not a sum
            line = re.sub(r"(?<=[\w)])\s+-\s+(?=[\w(])", " minus ", line)
        line = re.sub(r"(?<=[\w)])\s*\+\s*(?=[\w(])", " plus ", line)
        line = re.sub(r"(?<=[\d)])\s*\*\s*(?=[\d(])", " times ", line)
    line = line.replace("−", " minus ")
    line = _UNIT.sub(_unit, line)
    if calc and equation:
        # Brackets are a pause: "100 plus, 1.425 plus 1.735, minus, ..."
        line = re.sub(r"\s*[()]\s*", ", ", line)
        line = _DECIMAL.sub(lambda m: decimal_words(m.group(0)), line)
        line = re.sub(r"(?:,\s*){2,}", ", ", line)
        line = re.sub(r"^(\s*), ", r"\1", line)
        line = re.sub(r",\s*([.!?:;]|$)", r"\1", line)
    return re.sub(r"(?<=\S)[ \t]{2,}", " ", line)


def speak_plain_math(text: str) -> str:
    """Say typed arithmetic, units and minus signs as words."""
    return "\n".join(_speak_line(line) for line in text.split("\n"))


__all__ = [
    "decimal_words",
    "is_working",
    "speak_formula",
    "speak_math",
    "speak_plain_math",
]
