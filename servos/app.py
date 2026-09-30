from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

def main() -> None:
    _set_dpi()
    _setup_logging()
    log = logging.getLogger("servos")

    from servos.runtime import Runtime

    runtime = Runtime()
    try:
        runtime.prepare()
    except Exception as exc:
        log.exception("startup failed")
        runtime.stop()
        _fatal(str(exc))
        return

    try:
        import customtkinter as ctk

        from servos.config import Config
        from servos.hotkeys import Hotkeys
        from servos.pipeline import Pipeline
        from servos.state import SharedState
        from servos.ui.debug import DebugWindow
        from servos.ui.face import FaceWindow, tray_image
        from servos.ui.settings import SettingsWindow

        config = Config.load()
        state = SharedState()
        pipeline = Pipeline(config, state)

        ctk.set_appearance_mode("dark")
        root = ctk.CTk()
        root.withdraw()

        settings_box: dict[str, SettingsWindow] = {}
        debug_window = DebugWindow(root, pipeline, on_close=lambda: settings_box["window"].debug_closed())

        def open_settings() -> None:
            settings_box["window"].show()

        def on_captured(spec: str | None) -> None:
            root.after(0, lambda: settings_box["window"].apply_shortcut(spec))

        hotkeys = Hotkeys(
            get_shortcut=lambda: config.ptt_shortcut,
            on_ptt=pipeline.ptt,
            on_captured=on_captured,
        )
        face_box: dict[str, FaceWindow] = {}
        icon_holder: dict[str, object] = {}

        def apply_identity() -> None:
            face = face_box.get("window")
            if face is not None:
                face.apply_portrait()
            icon = icon_holder.get("icon")
            if icon is not None:
                icon.title = config.assistant_name
                icon.icon = tray_image(config.face_image)

        settings_box["window"] = SettingsWindow(
            root,
            config,
            pipeline,
            hotkeys,
            debug_window,
            on_identity=apply_identity,
        )
        if config.debug:
            root.after(300, debug_window.show)
        face_box["window"] = FaceWindow(root, config, state, on_settings=lambda: root.after(0, open_settings))
        closed = {"done": False}

        def shutdown(_event=None) -> None:
            if closed["done"]:
                return
            closed["done"] = True
            log.info("shutting down")
            try:
                hotkeys.stop()
            except Exception:
                log.exception("hotkey stop failed")
            pipeline.stop()
            runtime.stop()
            icon = icon_holder.get("icon")
            if icon is not None:
                try:
                    icon.stop()
                except Exception:
                    log.exception("tray stop failed")
            root.quit()

        root.bind("<<ServosQuit>>", shutdown)
        try:
            icon_holder["icon"] = _tray(
                lambda: root.after(0, open_settings),
                lambda: root.after(0, shutdown),
                tray_image(config.face_image),
                config.assistant_name,
            )
        except Exception:
            log.exception("tray icon failed")
        pipeline.start()
        try:
            root.mainloop()
        finally:
            if not closed["done"]:
                shutdown()
            try:
                root.destroy()
            except Exception:
                pass
    finally:
        runtime.stop()


def _set_dpi() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("servos.local.assistant")
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _setup_logging() -> None:
    from servos.config import APP_DIR

    APP_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.FileHandler(APP_DIR / "servos.log", encoding="utf-8"),
    ]
    if sys.stdout is not None:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("huggingface_hub").setLevel(logging.WARNING)


def _fatal(message: str) -> None:
    import tkinter as tk
    from tkinter import messagebox

    from servos.config import APP_DIR

    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("Pardilston", f"{message}\n\nDetails are in {APP_DIR / 'servos.log'}")
    root.destroy()


def _tray(on_settings, on_quit, image, title: str):
    import pystray

    menu = pystray.Menu(
        pystray.MenuItem("Settings", lambda _icon, _item: on_settings()),
        pystray.MenuItem("Quit", lambda _icon, _item: on_quit()),
    )
    icon = pystray.Icon("servos", image, title, menu)
    threading.Thread(target=icon.run, name="servos-tray", daemon=True).start()
    return icon


if __name__ == "__main__":
    main()
