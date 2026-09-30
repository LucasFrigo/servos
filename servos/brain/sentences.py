from __future__ import annotations

import re


_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "prof"}


class SentenceBuffer:
    """Cut streamed tokens into speakable pieces as soon as a pause appears."""

    def __init__(self) -> None:
        self.buf = ""

    def push(self, token: str) -> list[str]:
        if not token:
            return []
        self.buf += token
        if "<tool_call" in self.buf:
            return []
        pieces: list[str] = []
        while True:
            cut = self._cut()
            if cut is None:
                break
            piece = self.buf[:cut].strip()
            self.buf = self.buf[cut:].lstrip()
            if _speakable(piece):
                pieces.append(piece)
        return pieces

    def flush(self) -> str | None:
        if "<tool_call" in self.buf:
            self.buf = ""
            return None
        text = self.buf.strip()
        self.buf = ""
        if _speakable(text):
            return text
        return None

    def _cut(self) -> int | None:
        for index, char in enumerate(self.buf):
            if char in ".!?":
                if self._skip_period(index, char):
                    continue
                return self._close_punct(index)
            if char == "," and self._comma_break(index):
                return index + 1
        if len(self.buf) >= 140:
            space = self.buf.rfind(" ", 40, 120)
            if space > 40:
                return space + 1
        return None

    def _skip_period(self, index: int, char: str) -> bool:
        previous = self.buf[index - 1] if index else ""
        nxt = self.buf[index + 1] if index + 1 < len(self.buf) else ""
        if char == "." and previous.isdigit() and nxt.isdigit():
            return True
        if nxt in ".!?":
            return True
        if char == "." and nxt.isalnum():
            return True
        if char == ".":
            word = re.search(r"([A-Za-z]+)\.$", self.buf[: index + 1])
            if word and word.group(1).lower() in _ABBREVIATIONS:
                return True
        if index + 1 >= len(self.buf):
            return False
        if nxt.isspace() or nxt in "\"'”)":
            return False
        return True

    def _close_punct(self, index: int) -> int:
        cursor = index + 1
        while cursor < len(self.buf) and self.buf[cursor] in "\"'”)":
            cursor += 1
        return cursor

    def _comma_break(self, index: int) -> bool:
        previous = self.buf[index - 1] if index else ""
        nxt = self.buf[index + 1] if index + 1 < len(self.buf) else ""
        if previous.isdigit() and nxt.isdigit():
            return False
        return index >= 2


def _speakable(text: str) -> bool:
    return any(character.isalnum() for character in text)
