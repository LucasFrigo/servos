from __future__ import annotations

import threading
from dataclasses import dataclass


@dataclass(frozen=True)
class Snapshot:
    status: str
    lines: tuple[str, str, str]
    level: float
    hearing: bool
    ready: bool


class SharedState:
    """Thread-safe snapshot the face window polls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.status = "idle"
        self.lines = ("", "", "_")
        self.level = 0.0
        self.hearing = False
        self.ready = False

    def update(
        self,
        status: str | None = None,
        lines: tuple[str, str, str] | list[str] | None = None,
        level: float | None = None,
        hearing: bool | None = None,
        ready: bool | None = None,
    ) -> None:
        with self._lock:
            if status is not None:
                self.status = status
            if lines is not None:
                text = tuple(lines)
                self.lines = (str(text[0]), str(text[1]), str(text[2]))
            if level is not None:
                self.level = float(level)
            if hearing is not None:
                self.hearing = hearing
            if ready is not None:
                self.ready = ready

    def snapshot(self) -> Snapshot:
        with self._lock:
            return Snapshot(
                status=self.status,
                lines=self.lines,
                level=self.level,
                hearing=self.hearing,
                ready=self.ready,
            )


def wrap_terminal(text: str, width: int = 22, rows: int = 3) -> tuple[str, str, str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        piece = word if not current else f"{current} {word}"
        if len(piece) <= width:
            current = piece
            continue
        if current:
            lines.append(current)
        current = word[:width]
    if current:
        lines.append(current)
    if len(lines) > rows:
        lines = lines[-rows:]
    while len(lines) < rows:
        lines.append("")
    return (lines[0], lines[1], lines[2])
