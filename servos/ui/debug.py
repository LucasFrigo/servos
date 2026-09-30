from __future__ import annotations

import threading
import time
from collections import deque

import customtkinter as ctk

from servos.audio.ear import list_mics
from servos.ui.theme import MID, SCREEN, SHELL

PANEL = SHELL
AMBER = SCREEN


class TranscriptLog:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._lines: deque[str] = deque(maxlen=200)
        self.revision = 0

    def add(self, kind: str, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        line = f"{stamp}  {kind:<5}  {text.strip() or '(blank)'}"
        with self._lock:
            self._lines.append(line)
            self.revision += 1

    def snapshot(self) -> tuple[int, str]:
        with self._lock:
            return self.revision, "\n".join(self._lines)


class DebugWindow:
    def __init__(self, root, pipeline, on_close) -> None:
        self.root = root
        self.pipeline = pipeline
        self.on_close = on_close
        self.win: ctk.CTkToplevel | None = None
        self._revision = -1
        self._ticking = False
        self._ticks = 0

    def show(self) -> None:
        if self.win is None or not self.win.winfo_exists():
            self._build()
        assert self.win is not None
        self.win.deiconify()
        self.win.lift()
        self._refresh()
        if not self._ticking:
            self._ticking = True
            self.win.after(200, self._tick)

    def hide(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.withdraw()

    def _build(self) -> None:
        win = ctk.CTkToplevel(self.root)
        self.win = win
        win.title("Pardilston debug")
        win.geometry("520x320")
        win.minsize(360, 200)
        win.configure(fg_color=PANEL)
        win.attributes("-topmost", True)
        win.protocol("WM_DELETE_WINDOW", self._closed)
        self.device_var = ctk.StringVar(value="Input: System default")
        ctk.CTkLabel(
            win,
            textvariable=self.device_var,
            text_color=AMBER,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13),
        ).pack(fill="x", padx=12, pady=(10, 4))
        self.box = ctk.CTkTextbox(
            win,
            fg_color=SHELL,
            text_color=SCREEN,
            border_width=1,
            border_color=MID,
            font=ctk.CTkFont(family="Consolas", size=13),
            wrap="word",
        )
        self.box.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.box.insert("1.0", "Waiting for speech.\n")
        self.box.configure(state="disabled")

    def _closed(self) -> None:
        self.hide()
        self.on_close()

    def _device_line(self) -> None:
        if self.win is None:
            return
        chosen = self.pipeline.config.mic_device
        label = "System default"
        for index, name in list_mics():
            if index == chosen:
                label = name
                break
        error = self.pipeline.ear.input_error
        if error:
            label = f"{label} — {error}"
        self.device_var.set(f"Input: {label}")

    def _tick(self) -> None:
        if self.win is None or not self.win.winfo_exists() or not self.win.winfo_viewable():
            self._ticking = False
            return
        self._ticks += 1
        if self._ticks % 10 == 0:
            self._device_line()
        self._refresh()
        self.win.after(200, self._tick)

    def _refresh(self) -> None:
        if self.win is None:
            return
        revision, text = self.pipeline.transcript.snapshot()
        if revision == self._revision:
            return
        self._revision = revision
        self._device_line()
        self.box.configure(state="normal")
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text or "Waiting for speech.\n")
        self.box.see("end")
        self.box.configure(state="disabled")
