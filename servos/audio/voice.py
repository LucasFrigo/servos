from __future__ import annotations

import logging
import queue
import threading

import numpy as np
import sounddevice as sd

from servos.audio.board import apply_board
from servos.config import Config
from servos.downloads import ensure_voice

log = logging.getLogger("servos.voice")


class Voice:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._piper = None
        self._synth_lock = threading.Lock()
        self._gen_lock = threading.Lock()
        self._gen = 0
        self._queue: queue.Queue = queue.Queue()
        self.playing = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._player, name="servos-mouth", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def load(self) -> None:
        from piper import PiperVoice

        path = ensure_voice(self.config.language)
        with self._synth_lock:
            self._piper = PiperVoice.load(str(path), use_cuda=False)

    @property
    def ready(self) -> bool:
        return self._piper is not None

    def close(self) -> None:
        self._stop.set()
        self.interrupt()
        self._queue.put(None)

    def synthesize(self, text: str) -> tuple[np.ndarray, int, int]:
        if self._piper is None:
            raise RuntimeError("Voice is not loaded")
        from piper import SynthesisConfig

        rate = max(0.8, min(1.2, float(self.config.speech_rate)))
        synthesis = SynthesisConfig(
            volume=1.0,
            length_scale=1.08 / rate,
            noise_scale=0.18,
            noise_w_scale=0.22,
            normalize_audio=True,
        )
        chunks: list[np.ndarray] = []
        sample_rate = 22050
        with self._synth_lock:
            for chunk in self._piper.synthesize(text, syn_config=synthesis):
                chunks.append(np.asarray(chunk.audio_float_array, dtype=np.float32))
                sample_rate = int(chunk.sample_rate)
        if not chunks:
            return np.zeros(1, dtype=np.float32), sample_rate, 1
        raw = np.concatenate(chunks)
        try:
            audio, join = self._shape(raw, sample_rate)
        except Exception:
            log.exception("voice effect failed")
            audio = np.clip(raw, -1.0, 1.0).astype(np.float32)
            join = audio.size
        return audio, sample_rate, join

    def enqueue(self, audio, sample_rate, on_start, on_level, alive, join: int | None = None) -> None:
        with self._gen_lock:
            generation = self._gen
        if join is None:
            join = len(audio)
        self._queue.put((generation, audio, sample_rate, on_start, on_level, alive, int(join)))

    def interrupt(self) -> None:
        with self._gen_lock:
            self._gen += 1
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    def wait_until_idle(self, alive, timeout: float = 120.0) -> None:
        import time

        started = time.monotonic()
        while alive() and (self.playing or not self._queue.empty()):
            if time.monotonic() - started > timeout:
                return
            time.sleep(0.03)

    def _shape(self, audio: np.ndarray, sample_rate: int) -> tuple[np.ndarray, int]:
        board = max(0.0, min(1.0, float(self.config.board_weight)))
        if board <= 0.001:
            colored = self._color(audio, sample_rate)
            return colored, colored.size
        return apply_board(audio, self._color(audio, sample_rate), sample_rate, board)

    def _color(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        amount = max(0.0, min(1.0, float(self.config.droid_edge)))
        if amount <= 0.001:
            return np.clip(audio, -1.0, 1.0).astype(np.float32)
        hold = max(1, int(sample_rate * 0.0012 * max(amount, 0.15)))
        if hold > 1:
            index = (np.arange(len(audio)) // hold) * hold
            stepped = audio[index]
        else:
            stepped = audio
        delay = max(1, int(sample_rate * 0.012))
        comb = np.zeros_like(audio)
        if delay < len(audio):
            comb[delay:] = audio[:-delay]
        mixed = (1.0 - 0.55 * amount) * audio + (0.40 * amount) * stepped + (0.18 * amount) * comb
        mixed = np.tanh(mixed * (1.0 + 0.6 * amount))
        peak = float(np.max(np.abs(mixed))) if mixed.size else 1.0
        if peak > 0.98:
            mixed = mixed / peak * 0.98
        return mixed.astype(np.float32)

    def _player(self) -> None:
        stream = None
        current_rate = None
        try:
            while not self._stop.is_set():
                item = self._queue.get()
                if item is None:
                    break
                generation, audio, sample_rate, on_start, on_level, alive, join = item
                with self._gen_lock:
                    current = self._gen
                if generation != current or not alive():
                    continue
                audio = np.ascontiguousarray(audio, dtype=np.float32)
                if audio.size < 2:
                    continue
                try:
                    if stream is None or current_rate != sample_rate:
                        if stream is not None:
                            stream.stop()
                            stream.close()
                        stream = sd.OutputStream(
                            samplerate=sample_rate,
                            channels=1,
                            dtype="float32",
                            latency="low",
                        )
                        stream.start()
                        current_rate = sample_rate
                    self.playing = True
                    on_start()
                    block = 2048
                    join = min(max(int(join), 0), len(audio))
                    for start in range(0, len(audio), block):
                        with self._gen_lock:
                            if self._gen != generation:
                                break
                        if not alive():
                            break
                        if start >= join and not self._queue.empty():
                            break
                        chunk = audio[start : start + block]
                        level = float(np.sqrt(np.mean(np.square(chunk)) + 1e-12))
                        on_level(level)
                        stream.write(chunk.reshape(-1, 1))
                except Exception:
                    log.exception("playback failed")
                finally:
                    with self._gen_lock:
                        same_turn = self._gen == generation
                    if not (same_turn and alive() and not self._queue.empty()):
                        self.playing = False
                        try:
                            on_level(0.0)
                        except Exception:
                            pass
        finally:
            if stream is not None:
                stream.stop()
                stream.close()
