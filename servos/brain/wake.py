from __future__ import annotations

import re

from servos.config import DEFAULT_NAME

# Speech-to-text often writes the default name as Houston or a near miss.
_MISHEARD = re.compile(
    r"\b(?:houston|huston|pardiston|pardelston|partleston|bartleston)\b",
    re.IGNORECASE,
)
_FILLER = re.compile(r"^(?:hey|ok|okay|um|uh)\b[\s,]*", re.IGNORECASE)


def gate(
    text: str,
    *,
    always: bool,
    wake_word: bool,
    followup: bool,
    name: str = DEFAULT_NAME,
) -> tuple[str, str]:
    """Decide what to do with a transcript.

    Returns ``(action, utterance)`` where action is ``ask``, ``arm``, or ``skip``.
    Push-to-talk and follow-up windows skip the wake word. Always-listening
    with the wake word on answers only after the assistant's name.
    A leading mishearing such as "Houston" counts when the name is still Pardilston.
    """

    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "skip", ""
    if not always or not wake_word or followup:
        return "ask", cleaned

    match = _find_name(cleaned, name)
    if match is None:
        return "skip", ""

    remainder = f"{cleaned[: match.start()]} {cleaned[match.end() :]}"
    remainder = re.sub(
        r"^(hey|ok|okay|um|uh)\b[\s,]*",
        "",
        remainder.strip(),
        flags=re.IGNORECASE,
    )
    remainder = remainder.strip(" \t,.;:-")
    remainder = re.sub(r"\s+", " ", remainder).strip()
    if not remainder:
        return "arm", ""
    return "ask", remainder


def _find_name(cleaned: str, name: str) -> re.Match[str] | None:
    exact = re.compile(rf"\b{re.escape(name)}\b", re.IGNORECASE).search(cleaned)
    if exact is not None:
        return exact
    if name.casefold() != DEFAULT_NAME.casefold():
        return None
    filler = _FILLER.match(cleaned)
    start = filler.end() if filler else 0
    return _MISHEARD.match(cleaned, start)
