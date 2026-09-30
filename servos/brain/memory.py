from __future__ import annotations

import json
import threading

from servos.config import APP_DIR

CONTEXT_PATH = APP_DIR / "context.jsonl"
# About a megabyte of transcript. Each reply only sees the recent tail.
MAX_BYTES = 1024 * 1024
PROMPT_CHARS = 8000


class Memory:
    def __init__(self, path=CONTEXT_PATH) -> None:
        self.path = path
        self.messages: list[dict] = []
        self._bytes = 0
        self._lock = threading.Lock()
        self._load()

    def for_prompt(self) -> list[dict]:
        with self._lock:
            return _tail(self.messages, PROMPT_CHARS)

    def add_turn(self, user_text: str, answer: str) -> None:
        user_text = user_text.strip()
        answer = answer.strip()
        if not user_text or not answer:
            return
        pair = (
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": answer},
        )
        lines = [json.dumps(message, ensure_ascii=False) for message in pair]
        payload = "".join(line + "\n" for line in lines)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            self.messages.extend(pair)
            self._bytes += len(payload.encode("utf-8"))
            if self._bytes > MAX_BYTES:
                self._trim()

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()
            self._bytes = 0
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                return

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return
        messages: list[dict] = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in {"user", "assistant"} and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content.strip()})
        self.messages = messages
        self._bytes = len(raw.encode("utf-8"))
        if self._bytes > MAX_BYTES:
            self._trim()

    def _trim(self) -> None:
        kept = _tail_bytes(self.messages, MAX_BYTES)
        lines = [json.dumps(message, ensure_ascii=False) for message in kept]
        payload = "".join(line + "\n" for line in lines)
        temporary = self.path.with_suffix(".jsonl.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self.path)
        self.messages = kept
        self._bytes = len(payload.encode("utf-8"))


def _tail(messages: list[dict], limit: int) -> list[dict]:
    chosen: list[dict] = []
    used = 0
    for message in reversed(messages):
        size = len(message["content"]) + 16
        if chosen and used + size > limit:
            break
        chosen.append(dict(message))
        used += size
    chosen.reverse()
    return chosen


def _tail_bytes(messages: list[dict], limit: int) -> list[dict]:
    kept: list[dict] = []
    used = 0
    for message in reversed(messages):
        size = len(json.dumps(message, ensure_ascii=False).encode("utf-8")) + 1
        if kept and used + size > limit:
            break
        kept.append(message)
        used += size
    kept.reverse()
    return kept
