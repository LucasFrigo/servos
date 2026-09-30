from __future__ import annotations

from pynput import keyboard

_MODIFIERS = {"ctrl", "alt", "shift"}


class Hotkeys:
    def __init__(self, get_shortcut, on_ptt, on_captured) -> None:
        self.get_shortcut = get_shortcut
        self.on_ptt = on_ptt
        self.on_captured = on_captured
        self.pressed: set[str] = set()
        self.capturing = False
        self._down = False
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)
        self.listener.start()

    def start_capture(self) -> None:
        self.capturing = True
        if self._down:
            self._down = False
            self.on_ptt(False)

    def stop(self) -> None:
        self.listener.stop()

    def _press(self, key) -> None:
        name = _key_name(key)
        if not name or name in self.pressed:
            return
        self.pressed.add(name)
        if self.capturing:
            return
        if self._matches() and not self._down:
            self._down = True
            self.on_ptt(True)

    def _release(self, key) -> None:
        name = _key_name(key)
        if self.capturing and name and name not in _MODIFIERS:
            self.capturing = False
            if name == "esc":
                self.on_captured(None)
            else:
                self.on_captured(_format(self.pressed))
        if name:
            self.pressed.discard(name)
        if self._down and not self._matches():
            self._down = False
            self.on_ptt(False)

    def _matches(self) -> bool:
        parts = {part for part in self.get_shortcut().lower().split("+") if part}
        return bool(parts) and parts <= self.pressed


def _format(names: set[str]) -> str | None:
    mods = [name for name in ("ctrl", "alt", "shift") if name in names]
    keys = sorted(name for name in names if name not in _MODIFIERS)
    if len(keys) != 1:
        return None
    return "+".join([*mods, keys[0]])


def _key_name(key) -> str | None:
    if key in (keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
        return "ctrl"
    if key in (keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r):
        return "shift"
    if key in (keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr):
        return "alt"
    if key == keyboard.Key.space:
        return "space"
    if key == keyboard.Key.enter:
        return "enter"
    if key == keyboard.Key.tab:
        return "tab"
    if key == keyboard.Key.esc:
        return "esc"
    if isinstance(key, keyboard.KeyCode):
        if key.char and len(key.char) == 1 and key.char.isprintable():
            return key.char.lower()
        if key.vk is not None and 0x41 <= key.vk <= 0x5A:
            return chr(key.vk).lower()
        if key.vk is not None and 0x30 <= key.vk <= 0x39:
            return chr(key.vk)
    return None
