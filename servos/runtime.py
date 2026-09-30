from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

log = logging.getLogger("servos.runtime")

OLLAMA_TAGS = "http://127.0.0.1:11434/api/tags"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Runtime:
    def __init__(self) -> None:
        self._serve: subprocess.Popen | None = None

    def prepare(self) -> None:
        from servos.config import Config

        config = Config.load()
        splash: _Splash | None = None
        try:
            if not _api_up():
                exe = find_ollama()
                if exe is None:
                    if getattr(sys, "frozen", False):
                        raise RuntimeError(
                            "Ollama is not installed. Run the Pardilston setup again so it can install Ollama."
                        )
                    log.warning("Ollama is not installed")
                    return
                log.info("starting Ollama from %s", exe)
                splash = _Splash("Starting Ollama...")
                self._serve = _start_serve(exe)
                if not _wait_for_api(self._serve, 45):
                    raise RuntimeError("Ollama did not start. Check that nothing else is using port 11434.")
            if _model_installed(config.model):
                return
            exe = find_ollama()
            if exe is None:
                if getattr(sys, "frozen", False):
                    raise RuntimeError(f"Ollama is running, but {config.model} is not installed.")
                log.warning("%s is not installed", config.model)
                return
            if not getattr(sys, "frozen", False):
                log.warning("%s is not installed", config.model)
                return
            log.info("pulling %s", config.model)
            if splash is None:
                splash = _Splash(f"Downloading {config.model}...")
            else:
                splash.set(f"Downloading {config.model}...")
            _pull(exe, config.model, splash.set)
        finally:
            if splash is not None:
                splash.close()

    def stop(self) -> None:
        proc = self._serve
        self._serve = None
        if proc is None or proc.poll() is not None:
            return
        log.info("stopping Ollama")
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            check=False,
        )


def find_ollama() -> Path | None:
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "Programs" / "Ollama" / "ollama.exe")
    program_files = os.environ.get("ProgramFiles", "")
    if program_files:
        candidates.append(Path(program_files) / "Ollama" / "ollama.exe")
    found = shutil.which("ollama")
    if found:
        candidates.append(Path(found))
    for path in candidates:
        if path.is_file():
            return path
    return None


def _api_up() -> bool:
    try:
        response = httpx.get(OLLAMA_TAGS, timeout=1.5)
    except Exception:
        return False
    return response.status_code == 200


def _model_installed(model: str) -> bool:
    try:
        response = httpx.get(OLLAMA_TAGS, timeout=3)
        response.raise_for_status()
    except Exception:
        return False
    names = [str(item.get("name") or "") for item in response.json().get("models", [])]
    return any(name == model or name.startswith(model + ":") for name in names)


def _start_serve(exe: Path) -> subprocess.Popen:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    return subprocess.Popen(
        [str(exe), "serve"],
        cwd=str(exe.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
        startupinfo=startup,
    )


def _wait_for_api(proc: subprocess.Popen, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _api_up():
            return True
        if proc.poll() is not None:
            time.sleep(1.0)
            return _api_up()
        time.sleep(0.3)
    return _api_up()


def _pull(exe: Path, model: str, update) -> None:
    startup = subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    proc = subprocess.Popen(
        [str(exe), "pull", model],
        cwd=str(exe.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_NO_WINDOW,
        startupinfo=startup,
    )
    assert proc.stdout is not None
    pending = ""
    while True:
        chunk = proc.stdout.read(512)
        if not chunk:
            break
        pending += chunk
        lines = pending.replace("\r", "\n").split("\n")
        pending = lines[-1]
        for line in lines[:-1]:
            text = line.strip()
            if text:
                update(text[-200:])
    tail = pending.strip()
    if tail:
        update(tail[-200:])
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"Could not download {model}.")


class _Splash:
    def __init__(self, message: str) -> None:
        import tkinter as tk

        self._tk = tk
        self.root = tk.Tk()
        self.root.title("Pardilston")
        self.root.configure(bg="#141414")
        self.root.resizable(False, False)
        self.label = tk.Label(
            self.root,
            text=message,
            bg="#141414",
            fg="#d7d2c8",
            font=("Segoe UI", 12),
            wraplength=400,
            justify="left",
        )
        self.label.pack(expand=True, fill="both", padx=28, pady=28)
        self.root.update_idletasks()
        width, height = 460, 140
        x = max(0, (self.root.winfo_screenwidth() - width) // 2)
        y = max(0, (self.root.winfo_screenheight() - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.update()

    def set(self, message: str) -> None:
        self.label.configure(text=message)
        self.root.update()

    def close(self) -> None:
        try:
            self.root.destroy()
        except self._tk.TclError:
            pass
