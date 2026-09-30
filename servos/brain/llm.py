from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator

import httpx

from servos.brain.memory import Memory
from servos.brain.personality import WEB_SEARCH_TOOL, system_prompt
from servos.brain.search import web_search
from servos.brain.sentences import SentenceBuffer
from servos.config import Config

log = logging.getLogger("servos.brain")

OLLAMA_CHAT = "http://127.0.0.1:11434/api/chat"
OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
_TOOL_TAG_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
_TOOL_NAME_RE = re.compile(r"web_search\b", re.IGNORECASE)
_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)
_PROSE_SEARCH_RE = re.compile(
    r"web[\s_]+search\b[\s:;,\-]*(.*)\Z",
    re.IGNORECASE | re.DOTALL,
)
_PROSE_COMMENT_RE = re.compile(
    r"^(?:is|isn't|is not|was|wasn't|were|failed|cannot|can't|won't|will not|"
    r"não|nao|está|esta|parece|falhou|funcionou)\b",
    re.IGNORECASE,
)
_MARKERS = ("web_search", "web search", "<tool_call")


class Brain:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.memory = Memory()

    def warmup(self) -> None:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": "Say ready."}],
            "stream": False,
            "keep_alive": -1,
            "options": {"num_predict": 4, "num_ctx": 4096, "temperature": 0},
        }
        response = httpx.post(OLLAMA_CHAT, json=payload, timeout=180)
        response.raise_for_status()

    def iter_sentences(self, user_text: str, alive, on_tool) -> Iterator[str]:
        messages = self.memory.for_prompt()
        messages.append({"role": "user", "content": user_text})
        spoken, calls = yield from self._round(
            messages,
            use_tools=True,
            num_predict=max(self.config.num_predict(), 220),
            alive=alive,
            on_tool=on_tool,
        )
        final = spoken
        if calls and alive():
            query = _query_from_call(calls[0])
            name = _tool_name(calls[0])
            log.info("tool %s: %s", name or "web_search", query)
            result = web_search(query) if name == "web_search" else "That tool is not available."
            messages.append(
                {
                    "role": "assistant",
                    "content": spoken,
                    "tool_calls": calls,
                }
            )
            tool_message: dict = {
                "role": "tool",
                "content": result,
                "tool_name": name or "web_search",
            }
            call_id = calls[0].get("id")
            if call_id:
                tool_message["tool_call_id"] = call_id
            messages.append(tool_message)
            final, _extra = yield from self._round(
                messages,
                use_tools=False,
                num_predict=self.config.num_predict(),
                alive=alive,
                on_tool=lambda: None,
            )
        if alive() and final.strip():
            self._remember(user_text, final)

    def _round(self, messages, use_tools, num_predict, alive, on_tool):
        buffer = SentenceBuffer()
        spoken: list[str] = []
        raw: list[str] = []
        calls: list[dict] = []
        announced = False
        released = 0

        for token, chunk_calls in self._tokens(messages, use_tools, num_predict, alive):
            if not alive():
                break
            if token:
                raw.append(token)
            if chunk_calls:
                calls = chunk_calls
            text = "".join(raw)
            if use_tools and not chunk_calls:
                parsed = _embedded_tools(text)
                if parsed:
                    calls = parsed
            if calls and not announced:
                announced = True
                on_tool()
            if use_tools:
                hold = _tool_hold_index(text)
                if hold is not None and hold < released and not spoken:
                    buffer.buf = ""
                    released = hold
            end = _speakable_end(text, use_tools, calls, released)
            if end > released:
                for sentence in buffer.push(text[released:end]):
                    if _is_tool_noise(sentence):
                        continue
                    spoken.append(sentence)
                    yield sentence
                released = end

        text = "".join(raw)
        if use_tools and not calls:
            parsed = _embedded_tools(text, allow_prose=True)
            if parsed:
                calls = parsed
                if not announced:
                    on_tool()
        if alive() and not calls:
            if released < len(text):
                for sentence in buffer.push(text[released:]):
                    if _is_tool_noise(sentence):
                        continue
                    spoken.append(sentence)
                    yield sentence
            tail = buffer.flush()
            if tail and not _is_tool_noise(tail):
                spoken.append(tail)
                yield tail
        return " ".join(spoken).strip(), calls

    def _tokens(self, messages, use_tools, num_predict, alive):
        payload = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt(
                        self.config.reply_length,
                        self.config.language,
                        self.config.assistant_name,
                    ),
                },
                *messages,
            ],
            "stream": True,
            "keep_alive": -1,
            "options": {
                "num_ctx": 4096,
                "num_predict": num_predict,
                "temperature": 0.6,
            },
        }
        if use_tools:
            payload["tools"] = [WEB_SEARCH_TOOL]
        timeout = httpx.Timeout(15.0, read=120.0)
        with httpx.stream("POST", OLLAMA_CHAT, json=payload, timeout=timeout) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not alive():
                    return
                if not line:
                    continue
                data = json.loads(line)
                message = data.get("message") or {}
                content = message.get("content") or ""
                tool_calls = message.get("tool_calls") or []
                if content or tool_calls:
                    yield content, tool_calls
                if data.get("done"):
                    break

    def forget(self) -> None:
        self.memory.clear()

    def _remember(self, user_text: str, answer: str) -> None:
        self.memory.add_turn(user_text, answer)


def ollama_status(model: str) -> str:
    try:
        response = httpx.get(OLLAMA_TAGS, timeout=1.5)
        response.raise_for_status()
    except Exception:
        return "Ollama is not running."
    names = [str(item.get("name") or "") for item in response.json().get("models", [])]
    if any(name == model or name.startswith(model) for name in names):
        return f"Ollama is running. {model} is installed."
    return f"Ollama is running. {model} is not pulled yet."


def _tool_name(call: dict) -> str:
    function = call.get("function") or {}
    return str(function.get("name") or "")


def _query_from_call(call: dict) -> str:
    function = call.get("function") or {}
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
    if isinstance(arguments, dict):
        return str(arguments.get("query") or "")
    return ""


def _embedded_tools(text: str, allow_prose: bool = False) -> list[dict]:
    calls: list[dict] = []
    for match in _TOOL_TAG_RE.finditer(text):
        call = _call_from_fragment(match.group(1))
        if call:
            calls.append(call)
    if calls:
        return calls
    for match in _TOOL_NAME_RE.finditer(text):
        call = _call_after_name(text[match.end() :])
        if call:
            return [call]
    start = text.find("{")
    while start >= 0:
        call = _named_search_call(text, start)
        if call:
            return [call]
        start = text.find("{", start + 1)
    if allow_prose:
        call = _prose_search(text)
        if call:
            return [call]
    return calls


def _prose_search(text: str) -> dict | None:
    """Catch a spoken tool call such as 'web search escolhe um estado'."""
    match = _PROSE_SEARCH_RE.match(text.strip())
    if match is None:
        return None
    query = re.sub(r"\s+", " ", match.group(1)).strip(" \t\"'`")
    query = query.rstrip(".!?").strip()
    if len(query) < 3 or _PROSE_COMMENT_RE.match(query):
        return None
    if query.startswith("{"):
        return None
    return {"function": {"name": "web_search", "arguments": {"query": query}}}


def _call_from_fragment(fragment: str) -> dict | None:
    start = fragment.find("{")
    if start < 0:
        return None
    return _call_after_name(fragment[start:])


def _call_after_name(fragment: str) -> dict | None:
    fragment = fragment.lstrip()
    fence = _FENCE_RE.match(fragment)
    if fence:
        fragment = fragment[fence.end() :].lstrip()
    if not fragment.startswith("{"):
        return None
    data, _end = _read_json_object(fragment, 0)
    if not isinstance(data, dict):
        return None
    if data.get("name") not in (None, "web_search"):
        return None
    arguments = _arguments_of(data)
    if arguments is None:
        return None
    return {"function": {"name": "web_search", "arguments": arguments}}


def _named_search_call(text: str, start: int) -> dict | None:
    data, _end = _read_json_object(text, start)
    if not isinstance(data, dict) or data.get("name") != "web_search":
        return None
    arguments = _arguments_of(data)
    if arguments is None:
        return None
    return {"function": {"name": "web_search", "arguments": arguments}}


def _arguments_of(data: dict) -> dict | None:
    arguments = data.get("arguments", data.get("parameters"))
    if arguments is None and "query" in data:
        arguments = {"query": data.get("query")}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            return None
    if isinstance(arguments, dict) and str(arguments.get("query") or "").strip():
        return arguments
    return None


def _read_json_object(text: str, start: int) -> tuple[dict | None, int]:
    if start >= len(text) or text[start] != "{":
        return None, start
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(text[start : index + 1])
                except json.JSONDecodeError:
                    return None, start
                if isinstance(data, dict):
                    return data, index + 1
                return None, start
    return None, start


def _tool_hold_index(text: str) -> int | None:
    tagged = re.search(r"<tool_call\b", text, re.IGNORECASE)
    if tagged:
        return tagged.start()
    named = _TOOL_NAME_RE.search(text)
    if named and re.match(r"\s*(?:```(?:json)?\s*)?(?:\{|$)", text[named.end() :], re.IGNORECASE):
        return named.start()
    bare = re.search(r"\{\s*\"name\"\s*:\s*\"web_search\"", text, re.IGNORECASE)
    if bare:
        return bare.start()
    return _leading_search_hold(text)


def _leading_search_hold(text: str) -> int | None:
    raw = text.lstrip()
    if not raw:
        return None
    offset = len(text) - len(raw)
    lowered = raw.lower()
    for marker in ("web search", "web_search"):
        if lowered.startswith(marker) or marker.startswith(lowered):
            return offset
    return None


def _speakable_end(text: str, use_tools: bool, calls: list, released: int) -> int:
    if not use_tools:
        return len(text)
    hold = _tool_hold_index(text)
    if hold is not None:
        return hold
    if calls:
        return released
    return _trim_marker_prefix(text)


def _trim_marker_prefix(text: str) -> int:
    end = len(text)
    for marker in _MARKERS:
        for size in range(1, len(marker)):
            if text.endswith(marker[:size]):
                end = min(end, len(text) - size)
    return end


def _is_tool_noise(text: str) -> bool:
    sample = text.strip()
    if not sample:
        return True
    if _embedded_tools(sample, allow_prose=True):
        return True
    return bool(re.match(r"web_search\b", sample, re.IGNORECASE) and "{" in sample)
