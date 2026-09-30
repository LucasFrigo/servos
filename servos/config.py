from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "Servos"
CONFIG_PATH = APP_DIR / "config.json"
DEFAULT_NAME = "Pardilston"
NAME_LIMIT = 32

MODELS = {
    "qwen2.5:7b": "qwen2.5:7b",
    "qwen2.5:3b": "qwen2.5:3b",
}

LANGUAGE_LABELS = {
    "en-US": "English (US)",
    "pt-BR": "Português (BR)",
}


@dataclass
class Config:
    listen_mode: str = "push_to_talk"
    wake_word: bool = True
    ptt_shortcut: str = "ctrl+space"
    mic_device: int | None = None
    model: str = "qwen2.5:7b"
    reply_length: str = "short"
    speech_rate: float = 0.92
    droid_edge: float = 0.22
    board_weight: float = 0.7
    followup_seconds: float = 5.0
    face_x: int = 80
    face_y: int = 80
    debug: bool = False
    language: str = "en-US"
    assistant_name: str = DEFAULT_NAME
    face_image: str = ""

    def num_predict(self) -> int:
        return 120 if self.reply_length == "short" else 480

    def save(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2)
        temporary = CONFIG_PATH.with_suffix(".json.tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(CONFIG_PATH)

    def normalized(self) -> Config:
        if self.listen_mode not in ("always", "push_to_talk"):
            self.listen_mode = "push_to_talk"
        if self.model not in MODELS:
            self.model = "qwen2.5:7b"
        if self.reply_length not in ("short", "long"):
            self.reply_length = "short"
        self.speech_rate = max(0.8, min(1.2, float(self.speech_rate)))
        self.droid_edge = max(0.0, min(1.0, float(self.droid_edge)))
        self.board_weight = max(0.0, min(1.0, float(self.board_weight)))
        self.followup_seconds = max(2.0, min(12.0, float(self.followup_seconds)))
        if self.mic_device is not None:
            self.mic_device = int(self.mic_device)
        if self.language not in LANGUAGE_LABELS:
            self.language = "en-US"
        self.assistant_name = normalize_assistant_name(self.assistant_name)
        face = str(self.face_image or "").strip()
        self.face_image = face if face and Path(face).is_file() else ""
        return self

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_PATH.exists():
            config = cls()
            config.save()
            return config
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        values = asdict(cls())
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in values:
                    values[key] = value
        return cls(**values).normalized()


def field_names() -> set[str]:
    return {item.name for item in fields(Config)}


def normalize_assistant_name(value: object) -> str:
    kept = [char for char in str(value or "") if char.isalnum() or char in " -'"]
    text = re.sub(r"\s+", " ", "".join(kept)).strip()[:NAME_LIMIT].strip()
    return text or DEFAULT_NAME
