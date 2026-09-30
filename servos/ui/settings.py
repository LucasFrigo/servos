from __future__ import annotations

import subprocess
import threading
import tkinter as tk
from tkinter import filedialog

import customtkinter as ctk
from PIL import Image

from servos.audio.ear import list_mics
from servos.brain.llm import ollama_status
from servos.config import LANGUAGE_LABELS, Config, normalize_assistant_name
from servos.runtime import find_ollama
from servos.ui.face import portrait_image, remove_portrait, save_portrait
from servos.ui.theme import BUTTON, CHECK, MENU, MID, SCREEN, SCREEN_DIM, SEGMENTED, SHELL, SLIDER

PANEL = SHELL
AMBER = SCREEN
TEXT = SCREEN
MUTED = SCREEN_DIM


class SettingsWindow:
    def __init__(self, root, config: Config, pipeline, hotkeys, debug_window, on_identity=None) -> None:
        self.root = root
        self.config = config
        self.pipeline = pipeline
        self.hotkeys = hotkeys
        self.debug_window = debug_window
        self.on_identity = on_identity
        self.win: ctk.CTkToplevel | None = None
        self._mics: list[tuple[int | None, str]] = []
        self._preview_image: ctk.CTkImage | None = None

    def show(self) -> None:
        if self.win is None or not self.win.winfo_exists():
            self._build()
        self._refresh_mics()
        self._refresh_status()
        assert self.win is not None
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()

    def apply_shortcut(self, spec: str | None) -> None:
        if self.win is None:
            return
        self.bind_button.configure(text="Bind")
        if not spec:
            return
        self.config.ptt_shortcut = spec
        self.config.save()
        self.shortcut_var.set(spec)

    def _build(self) -> None:
        ctk.set_appearance_mode("dark")
        win = ctk.CTkToplevel(self.root)
        self.win = win
        win.title(self.config.assistant_name)
        win.geometry("440x820")
        win.minsize(400, 520)
        win.configure(fg_color=PANEL)
        win.protocol("WM_DELETE_WINDOW", win.withdraw)

        scroll = ctk.CTkScrollableFrame(
            win,
            fg_color=PANEL,
            scrollbar_button_color=MID,
            scrollbar_button_hover_color=SCREEN_DIM,
        )
        scroll.pack(fill="both", expand=True, padx=12, pady=12)

        self.title_label = ctk.CTkLabel(
            scroll,
            text=self.config.assistant_name,
            text_color=AMBER,
            font=ctk.CTkFont(family="Consolas", size=22),
        )
        self.title_label.pack(anchor="w")
        self.status_var = tk.StringVar(value="Checking Ollama...")
        ctk.CTkLabel(
            scroll,
            textvariable=self.status_var,
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        self._section(scroll, "Identity")
        self.name_var = tk.StringVar(value=self.config.assistant_name)
        self.name_entry = ctk.CTkEntry(
            scroll,
            textvariable=self.name_var,
            fg_color=SHELL,
            border_color=MID,
            text_color=TEXT,
            font=ctk.CTkFont(family="Consolas", size=14),
        )
        self.name_entry.pack(fill="x", pady=(0, 4))
        self.name_entry.bind("<Return>", self._apply_name)
        self.name_entry.bind("<FocusOut>", self._apply_name)
        ctk.CTkLabel(
            scroll,
            text="He uses this name when he answers, and when you wake him.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        photo_row = ctk.CTkFrame(scroll, fg_color="transparent")
        photo_row.pack(fill="x", pady=(0, 8))
        self.preview = ctk.CTkLabel(photo_row, text="", fg_color=SHELL)
        self.preview.pack(side="left", padx=(0, 10))
        photo_buttons = ctk.CTkFrame(photo_row, fg_color="transparent")
        photo_buttons.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(photo_buttons, text="Choose photo", command=self._on_choose_photo, **BUTTON).pack(
            fill="x", pady=(0, 6)
        )
        ctk.CTkButton(photo_buttons, text="Use default portrait", command=self._on_default_photo, **BUTTON).pack(
            fill="x"
        )
        self._refresh_preview()

        self._section(scroll, "Language")
        self.language_picker = ctk.CTkSegmentedButton(
            scroll,
            values=list(LANGUAGE_LABELS.values()),
            command=self._on_language,
            **SEGMENTED,
        )
        self.language_picker.pack(fill="x", pady=(0, 8))
        self.language_picker.set(LANGUAGE_LABELS.get(self.config.language, "English (US)"))
        ctk.CTkLabel(
            scroll,
            text="He hears you and answers in this language. Portuguese downloads its voice the first time.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._section(scroll, "Listening")
        self.mode = ctk.CTkSegmentedButton(
            scroll,
            values=["Always listening", "Push to talk"],
            command=self._on_mode,
            **SEGMENTED,
        )
        self.mode.pack(fill="x", pady=(0, 8))
        self.mode.set("Always listening" if self.config.listen_mode == "always" else "Push to talk")

        self.wake = ctk.CTkCheckBox(
            scroll,
            text=self._wake_label(),
            command=self._on_wake,
            **CHECK,
        )
        self.wake.pack(anchor="w", pady=(0, 4))
        if self.config.wake_word:
            self.wake.select()
        ctk.CTkLabel(
            scroll,
            text="Used while always listening. Saying only the name waits a few seconds.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        row = ctk.CTkFrame(scroll, fg_color="transparent")
        row.pack(fill="x", pady=(0, 12))
        self.shortcut_var = tk.StringVar(value=self.config.ptt_shortcut)
        ctk.CTkLabel(row, textvariable=self.shortcut_var, text_color=AMBER, font=ctk.CTkFont(family="Consolas", size=14)).pack(
            side="left"
        )
        self.bind_button = ctk.CTkButton(row, text="Bind", width=80, command=self._on_bind, **BUTTON)
        self.bind_button.pack(side="right")

        self._section(scroll, "Audio")
        ctk.CTkLabel(scroll, text="Input device", text_color=TEXT).pack(anchor="w")
        self.mic_var = tk.StringVar(value="System default")
        self.mic_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self.mic_var,
            values=["System default"],
            command=self._on_mic,
            dynamic_resizing=False,
            width=380,
            **MENU,
        )
        self.mic_menu.pack(fill="x", pady=(0, 8))
        self.debug_box = ctk.CTkCheckBox(
            scroll,
            text="Debug transcript",
            command=self._on_debug,
            **CHECK,
        )
        self.debug_box.pack(anchor="w", pady=(0, 8))
        if self.config.debug:
            self.debug_box.select()
        ctk.CTkLabel(
            scroll,
            text="Shows what the microphone heard, before he answers.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self.rate_var = tk.StringVar(value=self._rate_label())
        ctk.CTkLabel(scroll, textvariable=self.rate_var, text_color=TEXT).pack(anchor="w")
        self.rate = ctk.CTkSlider(scroll, from_=0.8, to=1.2, number_of_steps=40, command=self._on_rate, **SLIDER)
        self.rate.set(self.config.speech_rate)
        self.rate.pack(fill="x", pady=(0, 8))

        self.droid_var = tk.StringVar(value=self._droid_label())
        ctk.CTkLabel(scroll, textvariable=self.droid_var, text_color=TEXT).pack(anchor="w")
        self.droid = ctk.CTkSlider(scroll, from_=0.0, to=1.0, number_of_steps=20, command=self._on_droid, **SLIDER)
        self.droid.set(self.config.droid_edge)
        self.droid.pack(fill="x", pady=(0, 8))

        self.board_var = tk.StringVar(value=self._board_label())
        ctk.CTkLabel(scroll, textvariable=self.board_var, text_color=TEXT).pack(anchor="w")
        self.board = ctk.CTkSlider(scroll, from_=0.0, to=1.0, number_of_steps=20, command=self._on_board)
        self.board.set(self.config.board_weight)
        self.board.pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            scroll,
            text="A low doubled voice, throat-heavy bass, and a little growl.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))

        ctk.CTkButton(
            scroll,
            text="Test voice",
            command=self._on_test,
            **BUTTON,
        ).pack(fill="x", pady=(0, 12))

        self._section(scroll, "Model")
        self.model = ctk.CTkSegmentedButton(
            scroll,
            values=["qwen2.5:7b", "qwen2.5:3b"],
            command=self._on_model,
            **SEGMENTED,
        )
        self.model.pack(fill="x", pady=(0, 8))
        self.model.set(self.config.model if self.config.model in ("qwen2.5:7b", "qwen2.5:3b") else "qwen2.5:7b")

        self.length = ctk.CTkSegmentedButton(
            scroll,
            values=["Short replies", "Longer replies"],
            command=self._on_length,
            **SEGMENTED,
        )
        self.length.pack(fill="x", pady=(0, 8))
        self.length.set("Longer replies" if self.config.reply_length == "long" else "Short replies")
        ctk.CTkLabel(
            scroll,
            text="7B searches more reliably. 3B answers faster.",
            text_color=MUTED,
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(0, 8))
        ctk.CTkButton(
            scroll,
            text="Pull model",
            command=self._on_pull,
            **BUTTON,
        ).pack(fill="x", pady=(0, 16))

        ctk.CTkButton(scroll, text="Quit", command=self._on_quit, **BUTTON).pack(fill="x")
        win.withdraw()

    def _apply_name(self, _event=None) -> None:
        name = normalize_assistant_name(self.name_var.get())
        if self.name_var.get() != name:
            self.name_var.set(name)
        if name == self.config.assistant_name:
            return
        self.config.assistant_name = name
        self.config.save()
        self.pipeline.brain.forget()
        self._refresh_identity_labels()
        self._notify_identity()

    def _on_choose_photo(self) -> None:
        assert self.win is not None
        path = filedialog.askopenfilename(
            parent=self.win,
            title="Choose photo",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            stored = save_portrait(path)
        except Exception:
            self.status_var.set("That photo could not be opened.")
            return
        self.config.face_image = stored
        self.config.save()
        self._refresh_preview()
        self._notify_identity()

    def _on_default_photo(self) -> None:
        if not self.config.face_image:
            return
        self.config.face_image = ""
        self.config.save()
        remove_portrait()
        self._refresh_preview()
        self._notify_identity()

    def _refresh_identity_labels(self) -> None:
        name = self.config.assistant_name
        self.title_label.configure(text=name)
        if self.win is not None:
            self.win.title(name)
        self.wake.configure(text=self._wake_label())

    def _wake_label(self) -> str:
        return f'Require the name "{self.config.assistant_name}"'

    def _refresh_preview(self) -> None:
        image = portrait_image(self.config.face_image)
        resampling = Image.Resampling.LANCZOS if self.config.face_image else Image.Resampling.NEAREST
        image.thumbnail((96, 96), resampling)
        self._preview_image = ctk.CTkImage(light_image=image, dark_image=image, size=image.size)
        self.preview.configure(image=self._preview_image, text="")

    def _notify_identity(self) -> None:
        if self.on_identity is not None:
            self.on_identity()

    def _section(self, parent, title: str) -> None:
        ctk.CTkLabel(parent, text=title, text_color=AMBER, font=ctk.CTkFont(size=15, weight="bold")).pack(
            anchor="w", pady=(8, 4)
        )

    def _on_language(self, value: str) -> None:
        code = next(key for key, label in LANGUAGE_LABELS.items() if label == value)
        if code == self.config.language:
            return
        self.config.language = code
        self.config.save()
        self.status_var.set("Loading language...")

        def done(message: str) -> None:
            self.root.after(0, lambda: self.status_var.set(message))

        self.pipeline.set_language(done)

    def _on_mode(self, value: str) -> None:
        self.config.listen_mode = "always" if value.startswith("Always") else "push_to_talk"
        self.config.save()

    def _on_wake(self) -> None:
        self.config.wake_word = bool(self.wake.get())
        self.config.save()

    def _on_bind(self) -> None:
        self.bind_button.configure(text="Press keys...")
        self.hotkeys.start_capture()

    def _on_debug(self) -> None:
        enabled = bool(self.debug_box.get())
        self.config.debug = enabled
        self.config.save()
        if enabled:
            self.pipeline.note_debug("mic", self.mic_var.get())
            self.debug_window.show()
        else:
            self.debug_window.hide()

    def debug_closed(self) -> None:
        self.config.debug = False
        self.config.save()
        if self.win is not None and self.win.winfo_exists():
            self.debug_box.deselect()

    def _on_mic(self, label: str) -> None:
        for index, name in self._mics:
            if name == label:
                self.config.mic_device = index
                self.config.save()
                self.pipeline.set_mic()
                return

    def _on_rate(self, value: float) -> None:
        self.config.speech_rate = round(float(value), 2)
        self.rate_var.set(self._rate_label())
        self.config.save()

    def _on_droid(self, value: float) -> None:
        self.config.droid_edge = round(float(value), 2)
        self.droid_var.set(self._droid_label())
        self.config.save()

    def _on_board(self, value: float) -> None:
        self.config.board_weight = round(float(value), 2)
        self.board_var.set(self._board_label())
        self.config.save()

    def _on_model(self, value: str) -> None:
        self.config.model = value
        self.config.save()
        self.pipeline.set_model()
        self._refresh_status()

    def _on_length(self, value: str) -> None:
        self.config.reply_length = "long" if value.startswith("Longer") else "short"
        self.config.save()

    def _on_test(self) -> None:
        message = self.pipeline.test_voice()
        if message:
            self.status_var.set(message)

    def _on_pull(self) -> None:
        model = self.config.model
        self.status_var.set(f"Pulling {model}...")

        def run() -> None:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            exe = find_ollama()
            if exe is None:
                self.root.after(0, lambda: self.status_var.set("Install Ollama, then pull the model."))
                return
            process = subprocess.Popen(
                [str(exe), "pull", model],
                cwd=str(exe.parent),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=flags,
            )
            assert process.stdout is not None
            last = ""
            for line in process.stdout:
                last = line.strip() or last
                if last:
                    self.root.after(0, lambda text=last: self.status_var.set(text[-180:]))
            code = process.wait()
            if code == 0:
                self.root.after(0, self._refresh_status)
            else:
                self.root.after(0, lambda: self.status_var.set(f"Pull failed ({code})."))

        threading.Thread(target=run, daemon=True).start()

    def _on_quit(self) -> None:
        self.root.event_generate("<<ServosQuit>>", when="tail")

    def _refresh_mics(self) -> None:
        self._mics = list_mics()
        labels = [name for _index, name in self._mics]
        self.mic_menu.configure(values=labels or ["System default"])
        current = "System default"
        for index, name in self._mics:
            if index == self.config.mic_device:
                current = name
                break
        self.mic_var.set(current)

    def _refresh_status(self) -> None:
        text = ollama_status(self.config.model)
        if self.pipeline.ear.input_error:
            text = f"{text} Microphone: {self.pipeline.ear.input_error}"
        self.status_var.set(text)

    def _rate_label(self) -> str:
        return f"Speech rate {self.config.speech_rate:.2f}"

    def _droid_label(self) -> str:
        return f"Droid edge {self.config.droid_edge:.2f}"

    def _board_label(self) -> str:
        return f"Board weight {self.config.board_weight:.2f}"
