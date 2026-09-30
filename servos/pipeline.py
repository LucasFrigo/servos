from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

from servos.audio.ear import Ear, Utterance
from servos.audio.voice import Voice
from servos.brain.llm import Brain
from servos.brain.wake import gate
from servos.config import LANGUAGE_LABELS, Config
from servos.downloads import ensure_assets
from servos.state import SharedState, wrap_terminal
from servos.ui.debug import TranscriptLog

log = logging.getLogger("servos")


class Pipeline:
    def __init__(self, config: Config, state: SharedState) -> None:
        self.config = config
        self.state = state
        self._out: queue.Queue[Utterance] = queue.Queue()
        self.ear = Ear(config, state, self._out)
        self.voice = Voice(config)
        self.brain = Brain(config)
        self._turn_id = 0
        self._turn_lock = threading.Lock()
        self._ptt_held = False
        self._followup_until = 0.0
        self._followup_latched = False
        self._stop = threading.Event()
        self.transcript = TranscriptLog()
        self._booted = False

    def start(self) -> None:
        self.voice.start()
        self.ear.start()
        threading.Thread(target=self._loop, name="servos-loop", daemon=True).start()
        threading.Thread(target=self._boot, name="servos-boot", daemon=True).start()

    def stop(self) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self._bump()
        self.voice.close()
        self.ear.stop()

    def ptt(self, held: bool) -> None:
        if held:
            if self._ptt_held:
                return
            self._ptt_held = True
            self._bump()
            self.voice.interrupt()
            self.ear.suspend(False)
            self.ear.begin_ptt()
            self.state.update(
                status="listening",
                lines=("LISTEN", "HOLD", "_"),
                level=0.0,
                hearing=True,
            )
            self.note_debug("mic", "listening")
            return
        if not self._ptt_held:
            return
        self._ptt_held = False
        queued = self.ear.end_ptt()
        self.state.update(hearing=False)
        snap = self.state.snapshot()
        if not queued and snap.status == "listening":
            self.state.update(status="idle", lines=("", "", "_"), level=0.0)

    def set_mic(self) -> None:
        self.ear.request_reopen()
        self.note_debug("mic", "input device changed")

    def note_debug(self, kind: str, text: str) -> None:
        if self.config.debug:
            self.transcript.add(kind, text)

    def set_model(self) -> None:
        threading.Thread(target=self._safe_warmup, name="servos-warm", daemon=True).start()

    def set_language(self, on_done=None) -> None:
        threading.Thread(
            target=self._apply_language,
            args=(on_done,),
            name="servos-language",
            daemon=True,
        ).start()

    def test_voice(self) -> str:
        if not self.voice.ready:
            return "Voice is not ready yet."
        threading.Thread(target=self._speak_test, name="servos-test", daemon=True).start()
        return ""

    def _boot(self) -> None:
        try:
            self._boot_body()
        finally:
            self._booted = True

    def _boot_body(self) -> None:
        self.state.update(status="thinking", lines=("LOAD", "ASSETS", "_"))
        try:
            ensure_assets(self.config.language)
        except Exception:
            log.exception("asset download failed")
            self.state.update(status="idle", lines=("NO FILES", "", "_"))
            return

        voice_error: Exception | None = None
        link_error: Exception | None = None

        def load_voice() -> None:
            nonlocal voice_error
            try:
                self.voice.load()
            except Exception as exc:
                voice_error = exc
                log.exception("voice load failed")

        def warm() -> None:
            nonlocal link_error
            try:
                self.brain.warmup()
            except httpx.ConnectError as exc:
                link_error = exc
                log.warning("Ollama is not running")
            except Exception as exc:
                link_error = exc
                log.exception("ollama warmup failed")

        self.state.update(lines=("LOAD", "MODEL", "_"))
        with ThreadPoolExecutor(max_workers=2) as pool:
            voice_future = pool.submit(load_voice)
            warm_future = pool.submit(warm)
            voice_future.result()
            warm_future.result()

        self.state.update(lines=("LOAD", "EAR", "_"))
        try:
            self.ear.load(prefer_cuda=link_error is None)
        except Exception:
            log.exception("speech model load failed")
            self.state.update(status="idle", lines=("NO EAR", "", "_"))
            return

        if voice_error is not None:
            log.warning("ready without a voice")
            self.state.update(status="idle", lines=("NO VOICE", "", "_"), ready=True)
            return
        if link_error is not None:
            log.info("ready, Ollama is not available yet")
            self.state.update(status="idle", lines=("NO LINK", "", "_"), ready=True)
            return
        log.info("ready")
        self.state.update(status="idle", lines=("", "", "_"), level=0.0, ready=True)

    def _apply_language(self, on_done) -> None:
        while not self._booted and not self._stop.is_set():
            time.sleep(0.2)
        if self._stop.is_set():
            return
        language = self.config.language
        self._bump()
        self.voice.interrupt()
        self.brain.forget()
        label = "PT-BR" if language == "pt-BR" else "EN-US"
        self.state.update(status="thinking", lines=("LOAD", label, "_"))
        try:
            self.voice.load()
            self.state.update(lines=("LOAD", "EAR", "_"))
            self.ear.reload_speech()
        except Exception:
            log.exception("language switch failed")
            self.state.update(status="idle", lines=("FAULT", "", "_"), level=0.0)
            if on_done is not None:
                on_done("Language failed to load.")
            return
        self.state.update(status="idle", lines=("", "", "_"), level=0.0, ready=True)
        if on_done is not None:
            on_done(f"{LANGUAGE_LABELS.get(language, language)} is ready.")

    def _safe_warmup(self) -> None:
        try:
            self.brain.warmup()
        except Exception:
            log.exception("model warmup failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._housekeep()
            try:
                item = self._out.get(timeout=0.1)
            except queue.Empty:
                continue
            if self._stop.is_set():
                break
            try:
                self._handle(item)
            except Exception:
                log.exception("turn failed")
                self.state.update(status="idle", lines=("FAULT", "", "_"), level=0.0)
                self.ear.suspend(False)

    def _handle(self, item: Utterance) -> None:
        if not self.ear.ready.is_set():
            if self.state.snapshot().lines[0] in {"NO EAR", "NO FILES"}:
                return
            self._out.put(item)
            time.sleep(0.4)
            return
        turn = self._bump()
        self.ear.suspend(True)
        played = False
        owns_face = False
        try:
            text = self.ear.transcribe(item.audio)
            if turn != self._current():
                return
            self.note_debug("heard", text or "(nothing recognized)")
            if not text:
                return
            action, utterance = gate(
                text,
                always=item.source != "ptt" and self.config.listen_mode == "always",
                wake_word=self.config.wake_word,
                followup=self._consume_followup(item),
                name=self.config.assistant_name,
            )
            if action == "skip":
                self.note_debug("skip", "ignored, wake word missing")
                return
            if action == "arm":
                self.note_debug("wait", "name only, say the request")
                self._followup_until = time.monotonic() + float(self.config.followup_seconds)
                self._followup_latched = False
                self.state.update(status="listening", lines=("YES?", "", "_"))
                return
            if not self.voice.ready:
                self.state.update(status="idle", lines=("NO VOICE", "", "_"))
                return
            self.note_debug("send", utterance)
            log.info("heard: %s", utterance)
            preview = utterance if len(utterance) <= 22 else utterance[:21] + "…"
            owns_face = True
            self.state.update(status="thinking", lines=(preview, "...", "_"), level=0.0)

            def alive() -> bool:
                return turn == self._current()

            def on_tool() -> None:
                self.voice.interrupt()
                if alive():
                    self.state.update(status="thinking", lines=("SEARCH", "...", "_"), level=0.0)

            for sentence in self.brain.iter_sentences(utterance, alive, on_tool):
                if not alive():
                    self.voice.interrupt()
                    return
                if not any(character.isalnum() for character in sentence):
                    continue
                audio, sample_rate, join = self.voice.synthesize(sentence)
                if not alive():
                    self.voice.interrupt()
                    return

                def on_start(spoken=sentence) -> None:
                    if alive():
                        self.state.update(status="speaking", lines=wrap_terminal(spoken), level=0.0)

                self.voice.enqueue(
                    audio,
                    sample_rate,
                    on_start,
                    lambda level: self.state.update(level=level),
                    alive,
                    join,
                )
                played = True
            self.voice.wait_until_idle(alive)
            if alive():
                time.sleep(0.2)
        except httpx.ConnectError:
            log.warning("ollama is not running")
            if turn == self._current() and not played:
                self.state.update(status="idle", lines=("NO LINK", "", "_"), level=0.0)
        except httpx.HTTPStatusError as exc:
            log.warning("ollama status %s", exc.response.status_code)
            if turn == self._current() and not played:
                lines = ("NO MODEL", "", "_") if exc.response.status_code == 404 else ("FAULT", "", "_")
                self.state.update(status="idle", lines=lines, level=0.0)
        except Exception:
            log.exception("reply failed")
            if turn == self._current() and not played:
                self.state.update(status="idle", lines=("FAULT", "", "_"), level=0.0)
        finally:
            if turn == self._current() and not self._ptt_held:
                self.ear.suspend(False)
                snap = self.state.snapshot()
                if owns_face and snap.status in {"thinking", "speaking"}:
                    self.state.update(status="idle", lines=("", "", "_"), level=0.0)

    def _consume_followup(self, item: Utterance) -> bool:
        if item.source == "ptt":
            return False
        open_now = self._followup_latched or time.monotonic() < self._followup_until
        self._followup_latched = False
        self._followup_until = 0.0
        return open_now

    def _housekeep(self) -> None:
        snap = self.state.snapshot()
        now = time.monotonic()
        if snap.hearing and self._followup_until:
            self._followup_latched = True
            self._followup_until = max(self._followup_until, now + 2.0)
        elif (
            self._followup_until
            and now >= self._followup_until
            and not snap.hearing
            and not self._ptt_held
            and self._out.empty()
            and not self.voice.playing
            and snap.status == "listening"
        ):
            self._followup_until = 0.0
            self._followup_latched = False
            self.state.update(status="idle", lines=("", "", "_"))
            snap = self.state.snapshot()
        if self.ear.input_error and snap.status == "idle" and not snap.hearing:
            if snap.lines[0] != "NO MIC":
                detail = self.ear.input_error.replace("\n", " ")[:22]
                self.state.update(lines=("NO MIC", detail, "_"))
        elif snap.status == "idle" and snap.lines[0] == "NO MIC" and not self.ear.input_error:
            self.state.update(lines=("", "", "_"))

    def _speak_test(self) -> None:
        turn = self._bump()
        self.voice.interrupt()
        self.ear.suspend(True)
        text = _sample_line(self.config.language, self.config.assistant_name)
        failed = False
        try:
            audio, sample_rate, join = self.voice.synthesize(text)

            def alive() -> bool:
                return turn == self._current()

            self.voice.enqueue(
                audio,
                sample_rate,
                lambda: self.state.update(status="speaking", lines=wrap_terminal(text), level=0.0),
                lambda level: self.state.update(level=level),
                alive,
                join,
            )
            self.voice.wait_until_idle(alive)
            if alive():
                time.sleep(0.15)
        except Exception:
            log.exception("test voice failed")
            failed = True
            if turn == self._current():
                self.state.update(status="idle", lines=("NO VOICE", "", "_"), level=0.0)
        finally:
            if turn == self._current() and not self._ptt_held:
                self.ear.suspend(False)
                if not failed:
                    self.state.update(status="idle", lines=("", "", "_"), level=0.0)

    def _bump(self) -> int:
        with self._turn_lock:
            self._turn_id += 1
            return self._turn_id

    def _current(self) -> int:
        with self._turn_lock:
            return self._turn_id


def _sample_line(language: str, name: str) -> str:
    if language == "pt-BR":
        return f"Eu sou o {name}. Tente dizer algo que valha o processamento, Lucas."
    return f"I am {name}. Do try to say something worth the runtime, Lucas."
