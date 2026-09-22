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


__all__ = ["speak_formula", "speak_math"]
