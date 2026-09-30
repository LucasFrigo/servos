from __future__ import annotations

import logging
from pathlib import Path

import httpx

from servos.paths import project_root

log = logging.getLogger("servos.downloads")

ROOT = project_root()
MODELS = ROOT / "models"
VOICE_DIR = MODELS / "voices"
WHISPER_DIR = MODELS / "whisper"
VAD_PATH = MODELS / "silero_vad.onnx"
SILERO_URL = (
    "https://github.com/snakers4/silero-vad/raw/master/src/silero_vad/data/silero_vad.onnx"
)

VOICES = {
    "en-US": (
        "en_GB-alan-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx",
    ),
    "pt-BR": (
        "pt_BR-cadu-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/pt/pt_BR/cadu/medium/pt_BR-cadu-medium.onnx",
    ),
}


def _download(url: str, dest: Path, min_bytes: int) -> None:
    if dest.exists() and dest.stat().st_size >= min_bytes:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".part")
    log.info("downloading %s", dest.name)
    timeout = httpx.Timeout(30.0, read=180.0)
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as response:
        response.raise_for_status()
        with temporary.open("wb") as handle:
            for chunk in response.iter_bytes(256 * 1024):
                handle.write(chunk)
    if temporary.stat().st_size < min_bytes:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Download of {dest.name} was incomplete")
    temporary.replace(dest)


def voice_path(language: str) -> Path:
    filename, _url = VOICES.get(language, VOICES["en-US"])
    return VOICE_DIR / filename


def ensure_voice(language: str) -> Path:
    filename, url = VOICES.get(language, VOICES["en-US"])
    onnx = VOICE_DIR / filename
    _download(url, onnx, 1_000_000)
    _download(url + ".json", VOICE_DIR / f"{filename}.json", 100)
    return onnx


def ensure_assets(language: str = "en-US") -> None:
    ensure_voice(language)
    _download(SILERO_URL, VAD_PATH, 100_000)
