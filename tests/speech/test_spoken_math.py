"""Formulas are said, not spelt."""

from openjarvis.speech.spoken_math import speak_formula, speak_math
from openjarvis.speech.spoken_text import to_spoken_text


def test_the_statics_formulas_from_the_chat_are_read_as_words():
    # 22 September: the chat showed these verbatim and the voice read the
    # backslashes.
    assert (
        speak_math(r"[ \sum F_x=0,\quad \sum F_y=0,\quad \sum M=0 ]").strip()
        == "the sum of F x equals 0, the sum of F y equals 0, the sum of M equals 0"
    )
    assert (
        speak_math(r"from (0^\circ) to (360^\circ).").strip()
        == "from 0 degrees to 360 degrees."
    )


def test_fractions_powers_and_subscripts():
    assert speak_formula(r"T=\frac12 m v_G^2+\frac12 I_G\omega^2") == (
        "T equals 1 over 2 m v G squared plus 1 over 2 I G omega squared"
    )
    assert speak_formula(r"\frac{a+b}{c}") == "a plus b over c"
    assert speak_formula(r"x^{n+1}") == "x to the power of n plus 1"
    assert (
        speak_formula(r"RL_{\text{last}}-RL_{\text{first}}") == "RL last minus RL first"
    )
    assert speak_formula(r"\sqrt{2gh}") == "the square root of 2gh"


def test_every_delimiter_style_the_model_uses():
    assert speak_math(r"$E=mc^2$").strip() == "E equals mc squared"
    assert speak_math(r"$$F=\mu_s N$$").strip() == "F equals mu s N"
    assert speak_math(r"\(v=\omega r\)").strip() == "v equals omega r"
    assert speak_math(r"\[ M = F d_\perp \]").strip() == "M equals F d perpendicular"


def test_money_is_not_math_and_prose_is_untouched():
    assert (
        to_spoken_text("Set $200 aside; a - b is fine.")
        == "Set two hundred dollars aside; a - b is fine."
    )
    assert (
        speak_math("see [the docs] and (maybe) more")
        == "see [the docs] and (maybe) more"
    )


def test_it_runs_before_the_identifier_rules():
    # Without the ordering, "\sum F_x" was hidden as "an identifier".
    assert "identifier" not in to_spoken_text(r"Balance: \(\sum F_x = 0\)")


def test_money_is_said_in_words():
    # 15 October: the local voice could not read "$83,402".
    assert (
        to_spoken_text("The price is $83,402.")
        == "The price is eighty-three thousand four hundred two dollars."
    )
    assert to_spoken_text("Up to $83,402.50 today") == (
        "Up to eighty-three thousand four hundred two dollars and fifty cents today"
    )
    assert to_spoken_text("It costs $1.") == "It costs one dollar."
    assert to_spoken_text("Only $0.99") == "Only ninety-nine cents"
    assert to_spoken_text("Raised $3.5 million") == (
        "Raised three point five million dollars"
    )
    assert to_spoken_text("Revenue hit $12M") == "Revenue hit twelve million dollars"


def test_pesos_euros_and_pounds():
    assert to_spoken_text("₱1,250.75") == (
        "one thousand two hundred fifty pesos and seventy-five centavos"
    )
    assert to_spoken_text("€20 or £1.01") == "twenty euros or one pound and one penny"


def test_money_in_a_streamed_reply_and_in_bold():
    from openjarvis.speech.spoken_text import SpokenTextStream

    stream = SpokenTextStream()
    said = stream.push("It is **$83,402** now.") + stream.finish()
    assert " ".join(said) == "It is eighty-three thousand four hundred two dollars now."
