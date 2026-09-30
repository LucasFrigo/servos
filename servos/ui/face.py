from __future__ import annotations

import ctypes
import math
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from PIL import Image, ImageEnhance, ImageTk

from servos.config import APP_DIR, Config
from servos.state import SharedState
from servos.ui.theme import MID, SCREEN, SCREEN_DIM, SHELL, SPRITE

# Lightest to darkest: space, ░, ▒, ▓, █.
_SHADE = {
    " ": 0,
    "\u2591": 1,
    "\u2592": 2,
    "\u2593": 3,
    "\u2588": 4,
}
_SCALE = 1
_BEZEL = 8
_GAP = 6
_FAULT_MARKERS = ("NO LINK", "NO MIC", "NO EAR", "NO VOICE", "NO MODEL", "NO FILES", "FAULT")


def _load_grid() -> list[bytearray]:
    path = Path(__file__).with_name("pardilston.txt")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    width = len(lines[0])
    grid: list[bytearray] = []
    for line in lines:
        if len(line) != width:
            raise ValueError("pardilston art is not rectangular")
        grid.append(bytearray(_SHADE[ch] for ch in line))
    return grid


def _shift_figure(grid: list[bytearray], dy: int) -> list[bytearray]:
    if dy == 0:
        return grid
    height = len(grid)
    width = len(grid[0])
    out = [bytearray(width) for _ in range(height)]
    for y, row in enumerate(grid):
        ny = y + dy
        if 0 <= ny < height:
            out[ny][:] = row
    return out


def _darken(grid: list[bytearray]) -> list[bytearray]:
    last = len(SPRITE) - 1
    return [bytearray(min(last, shade + 1) for shade in row) for row in grid]


def _paint(grid: list[bytearray]) -> Image.Image:
    height = len(grid)
    width = len(grid[0])
    image = Image.new("RGB", (width, height))
    image.putdata([SPRITE[shade] for row in grid for shade in row])
    if _SCALE != 1:
        image = image.resize((width * _SCALE, height * _SCALE), Image.Resampling.NEAREST)
    return image


_BASE = _load_grid()
_SPRITE_W = len(_BASE[0]) * _SCALE
_SPRITE_H = len(_BASE) * _SCALE
WIN_W = _SPRITE_W + _BEZEL * 2
_GRIDS = {
    "base": _BASE,
    "dim": _darken(_BASE),
}


def portrait_image(path: str) -> Image.Image:
    photo = _open_photo(path)
    if photo is None:
        return _paint(_BASE)
    return _cover(photo, _SPRITE_W, _SPRITE_H)


def tray_image(path: str = "") -> Image.Image:
    photo = _open_photo(path)
    if photo is None:
        return _paint(_BASE).resize((64, 64), Image.Resampling.NEAREST)
    return _cover(photo, 64, 64)


def save_portrait(source: str) -> str:
    dest = APP_DIR / "assistant.png"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        flat = _on_shell(image)
    temporary = dest.with_name("assistant.tmp.png")
    flat.save(temporary, "PNG")
    temporary.replace(dest)
    return str(dest)


def remove_portrait() -> None:
    try:
        (APP_DIR / "assistant.png").unlink(missing_ok=True)
    except OSError:
        return


def _open_photo(path: str) -> Image.Image | None:
    if not path:
        return None
    try:
        file = Path(path)
        if not file.is_file():
            return None
        with Image.open(file) as image:
            return _on_shell(image)
    except Exception:
        return None


def _on_shell(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    background = Image.new("RGB", rgba.size, SPRITE[-1])
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, math.ceil(image.width * scale)), max(height, math.ceil(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def _shift_image(image: Image.Image, dy: int) -> Image.Image:
    if dy == 0:
        return image
    out = Image.new("RGB", image.size, SPRITE[-1])
    out.paste(image, (0, dy))
    return out


def _dim_image(image: Image.Image) -> Image.Image:
    return ImageEnhance.Brightness(image).enhance(0.45)


def _frames(path: str) -> dict[tuple[str, int], Image.Image]:
    photo = _open_photo(path)
    if photo is None:
        return {
            (name, dy): _paint(_shift_figure(grid, dy))
            for name, grid in _GRIDS.items()
            for dy in (0, -1, -2)
        }
    fitted = _cover(photo, _SPRITE_W, _SPRITE_H)
    sources = {"base": fitted, "dim": _dim_image(fitted)}
    return {
        (name, dy): _shift_image(image, dy)
        for name, image in sources.items()
        for dy in (0, -1, -2)
    }


class FaceWindow:
    def __init__(self, root: tk.Misc, config: Config, state: SharedState, on_settings) -> None:
        self.config = config
        self.state = state
        self.on_settings = on_settings
        self._drag_dx = 0
        self._drag_dy = 0
        self._ticks = 0
        self._shown: tuple[str, int] | None = None

        self.win = tk.Toplevel(root)
        self.win.title(config.assistant_name)
        self.font = tkfont.Font(family="Consolas", size=11)
        line = self.font.metrics("linespace")
        self._term_h = line * 3 + 10
        self._height = _BEZEL + _SPRITE_H + _GAP + 1 + self._term_h
        x, y = _onscreen(self.win, config.face_x, config.face_y)
        self.win.geometry(f"{WIN_W}x{self._height}+{x}+{y}")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=SHELL)
        self.win.resizable(False, False)

        self._photos = self._photo_images()
        self.face = tk.Label(
            self.win,
            image=self._photos[("base", 0)],
            bg=SHELL,
            bd=0,
            highlightthickness=0,
        )
        self.scan = tk.Frame(self.win, bg=MID, height=2, bd=0, highlightthickness=0)
        self.rule = tk.Frame(self.win, bg=MID, height=1, bd=0, highlightthickness=0)
        self.term = tk.Label(
            self.win,
            text="",
            font=self.font,
            fg=SCREEN_DIM,
            bg=SHELL,
            justify="left",
            anchor="nw",
            padx=8,
            pady=4,
        )
        self._place()
        for widget in (self.face, self.term, self.rule, self.scan, self.win):
            widget.bind("<ButtonPress-1>", self._press)
            widget.bind("<B1-Motion>", self._motion)
            widget.bind("<ButtonRelease-1>", self._release)
            widget.bind("<Button-3>", self._settings)
        self.win.after(80, self._hide_from_taskbar)
        self.win.after(50, self._tick)

    def apply_portrait(self) -> None:
        self.win.title(self.config.assistant_name)
        photos = self._photo_images()
        self.face.configure(image=photos[("base", 0)])
        self._photos = photos
        self._shown = ("base", 0)

    def _photo_images(self) -> dict[tuple[str, int], ImageTk.PhotoImage]:
        return {
            key: ImageTk.PhotoImage(image, master=self.win)
            for key, image in _frames(self.config.face_image).items()
        }

    def _place(self) -> None:
        self.face.place(x=_BEZEL, y=_BEZEL, width=_SPRITE_W, height=_SPRITE_H)
        rule_y = _BEZEL + _SPRITE_H + _GAP
        self.rule.place(x=_BEZEL, y=rule_y, width=_SPRITE_W, height=1)
        self.term.place(x=0, y=rule_y + 1, width=WIN_W, height=self._term_h)

    def _tick(self) -> None:
        self._ticks += 1
        if self._ticks % 40 == 0:
            try:
                self.win.attributes("-topmost", True)
            except tk.TclError:
                return
        snap = self.state.snapshot()
        now = time.monotonic()
        show = snap.status
        if show == "idle" and snap.hearing:
            show = "listening"
        cursor = int(now * 2) % 2 == 0
        lines = _terminal_lines(snap, show, cursor)
        fault = _is_fault(lines)
        key = _expression(show, now, fault)
        bob = _bob(show, now, snap.level)
        token = (key, bob)
        if token != self._shown:
            self._shown = token
            self.face.configure(image=self._photos[token])
        if show == "thinking" and not fault:
            scan_y = _BEZEL + int((now * 52) % (_SPRITE_H - 2))
            self.scan.place(x=_BEZEL, y=scan_y, width=_SPRITE_W, height=2)
            self.scan.lift()
        else:
            self.scan.place_forget()
        if fault:
            self.term.configure(text="\n".join(lines), fg=SHELL, bg=SCREEN)
        else:
            fg = SCREEN if show in {"listening", "thinking", "speaking"} else SCREEN_DIM
            self.term.configure(text="\n".join(lines), fg=fg, bg=SHELL)
        self.win.after(50, self._tick)

    def _press(self, event) -> None:
        self._drag_dx = event.x_root - self.win.winfo_x()
        self._drag_dy = event.y_root - self.win.winfo_y()

    def _motion(self, event) -> None:
        x = event.x_root - self._drag_dx
        y = event.y_root - self._drag_dy
        self.win.geometry(f"{WIN_W}x{self._height}+{int(x)}+{int(y)}")

    def _release(self, _event) -> None:
        self.config.face_x = self.win.winfo_x()
        self.config.face_y = self.win.winfo_y()
        self.config.save()

    def _settings(self, _event) -> None:
        self.on_settings()

    def _hide_from_taskbar(self) -> None:
        try:
            hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id()) or self.win.winfo_id()
            style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
            style = (style | 0x00000080) & ~0x00040000
            ctypes.windll.user32.SetWindowLongW(hwnd, -20, style)
            ctypes.windll.user32.SetWindowPos(
                hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020
            )
        except Exception:
            return


def _expression(show: str, now: float, fault: bool) -> str:
    if fault and int(now * 3) % 2 == 0:
        return "dim"
    return "base"


def _bob(show: str, now: float, level: float) -> int:
    if show == "speaking":
        speed = 7.0 + min(level, 0.15) * 36.0
        wave = math.sin(now * speed)
        if level >= 0.09 and wave > 0.45:
            return -2
        return -1 if wave > 0.0 else 0
    if show == "thinking":
        return 0
    speed = 3.2 if show == "listening" else 1.5
    return -1 if math.sin(now * speed) > 0.35 else 0


def _is_fault(lines: tuple[str, str, str]) -> bool:
    blob = " ".join(lines)
    return any(marker in blob for marker in _FAULT_MARKERS)


def _terminal_lines(snap, show: str, cursor: bool) -> tuple[str, str, str]:
    lines = snap.lines
    empty = not any(part.strip("_ ") for part in lines)
    if show == "listening" and (empty or lines[0] in {"", "_"}):
        return ("LISTEN", "", "█" if cursor else " ")
    if show == "idle" and empty:
        return ("", "", "█" if cursor else " ")
    third = lines[2]
    if third == "_":
        third = "█" if cursor else " "
    return (lines[0], lines[1], third)


def _onscreen(widget: tk.Misc, x: int, y: int) -> tuple[int, int]:
    widget.update_idletasks()
    width = widget.winfo_screenwidth()
    height = widget.winfo_screenheight()
    if not (0 <= x < width - 40 and 0 <= y < height - 40):
        return 80, 80
    return int(x), int(y)
