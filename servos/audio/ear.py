from __future__ import annotations

import ctypes
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

from servos.config import Config
from servos.downloads import VAD_PATH, WHISPER_DIR
from servos.state import SharedState

log = logging.getLogger("servos.ear")

SAMPLE_RATE = 16000
FRAME = 512
START_THRESHOLD = 0.55
END_THRESHOLD = 0.35
MIN_SPEECH_FRAMES = 10  # ~320ms
MAX_SPEECH_FRAMES = int(20_000 / 32)
SILENCE_FRAMES = 10  # 320ms


class Utterance:
    def __init__(self, audio: np.ndarray, source: str) -> None:
        self.audio = audio
        self.source = source


class SileroVAD:
    def __init__(self, path) -> None:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = onnxruntime.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self._sr = self._sample_rate_input()
        self.reset()

    def _sample_rate_input(self) -> np.ndarray:
        for item in self.session.get_inputs():
            if item.name == "sr" and list(item.shape) == [1]:
                return np.array([SAMPLE_RATE], dtype=np.int64)
        return np.array(SAMPLE_RATE, dtype=np.int64)

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> float:
        audio = np.concatenate([self.context, frame.reshape(1, FRAME)], axis=1)
        outputs = self.session.run(
            None,
            {
                "input": audio.astype(np.float32),
                "state": self.state,
                "sr": self._sr,
            },
        )
        probability, self.state = outputs[0], outputs[1]
        self.context = audio[:, -64:]
        return float(np.asarray(probability).reshape(-1)[0])


class Ear:
    def __init__(self, config: Config, state: SharedState, outgoing: queue.Queue) -> None:
        self.config = config
        self.state = state
        self._out = outgoing
        self.ready = threading.Event()
        self.input_error: str | None = None
        self._running = threading.Event()
        self._reopen = threading.Event()
        self._ptt = threading.Event()
        self._suspended = threading.Event()
        self._audio: queue.Queue[np.ndarray] = queue.Queue()
        self._pending = np.zeros(0, dtype=np.float32)
        self._ptt_buf: list[np.ndarray] = []
        self._vad: SileroVAD | None = None
        self._whisper = None
        self._preroll: deque[np.ndarray] = deque(maxlen=6)
        self._speech: list[np.ndarray] = []
        self._in_speech = False
        self._hot_frames = 0
        self._silence_frames = 0
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running.set()
        self._thread = threading.Thread(target=self._capture_loop, name="servos-ear", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running.clear()
        self._reopen.set()
        self._audio.put(np.zeros(0, dtype=np.float32))

    def load(self, prefer_cuda: bool = False) -> None:
        self._prefer_cuda = prefer_cuda
        if self._vad is None:
            self._vad = SileroVAD(VAD_PATH)
        self._whisper = _load_whisper(prefer_cuda, self.config.language)
        self.ready.set()

    def reload_speech(self) -> None:
        self.ready.clear()
        try:
            self._whisper = _load_whisper(getattr(self, "_prefer_cuda", False), self.config.language)
        finally:
            if self._whisper is not None:
                self.ready.set()

    def request_reopen(self) -> None:
        self._reopen.set()
        self._clear_audio()

    def suspend(self, paused: bool) -> None:
        if paused:
            self._suspended.set()
            self._reset_capture()
            self.state.update(hearing=False)
        else:
            self._suspended.clear()

    def begin_ptt(self) -> None:
        self._ptt_buf = []
        self._reset_capture()
        self._ptt.set()

    def end_ptt(self) -> bool:
        self._ptt.clear()
        if not self._ptt_buf:
            return False
        audio = np.concatenate(self._ptt_buf)
        self._ptt_buf = []
        audio = _trim(audio)
        if len(audio) < int(0.25 * SAMPLE_RATE):
            return False
        self._out.put(Utterance(audio, "ptt"))
        return True

    def transcribe(self, audio: np.ndarray) -> str:
        if self._whisper is None:
            raise RuntimeError("Speech model is not loaded")
        samples = np.ascontiguousarray(audio, dtype=np.float32)
        if samples.size < SAMPLE_RATE // 4:
            return ""
        rms = float(np.sqrt(np.mean(np.square(samples))))
        if rms < 0.004:
            return ""
        try:
            segments, _info = self._whisper.transcribe(
                samples,
                language=getattr(self._whisper, "_servos_lang", "en"),
                beam_size=1,
                vad_filter=False,
                condition_on_previous_text=False,
                without_timestamps=True,
                temperature=0.0,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except RuntimeError as exc:
            if not _cuda_runtime_error(exc) or not getattr(self._whisper, "_servos_cuda", False):
                raise
            log.exception("CUDA speech failed, switching to CPU")
            self._whisper = _load_whisper(False, self.config.language)
            return self.transcribe(audio)
        text = " ".join(text.split())
        if _is_hallucination(text, samples.size):
            return ""
        return text

    def _capture_loop(self) -> None:
        while self._running.is_set():
            device = self.config.mic_device
            self._pending = np.zeros(0, dtype=np.float32)
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=FRAME,
                    device=device,
                    callback=self._on_audio,
                )
                stream.start()
            except Exception as exc:
                self.input_error = str(exc)
                log.warning("microphone unavailable: %s", exc)
                time.sleep(1.0)
                continue
            self.input_error = None
            try:
                while self._running.is_set() and not self._reopen.is_set():
                    try:
                        chunk = self._audio.get(timeout=0.2)
                    except queue.Empty:
                        continue
                    if not self._running.is_set():
                        break
                    self._feed(chunk)
                    while True:
                        try:
                            extra = self._audio.get_nowait()
                        except queue.Empty:
                            break
                        self._feed(extra)
            finally:
                stream.stop()
                stream.close()
                self._reopen.clear()

    def _on_audio(self, indata, _frames, _time_info, _status) -> None:
        if indata.size == 0:
            return
        self._audio.put(np.array(indata[:, 0], dtype=np.float32, copy=True))

    def _feed(self, chunk: np.ndarray) -> None:
        if chunk.size == 0:
            return
        pending = np.concatenate((self._pending, chunk)) if self._pending.size else chunk
        usable = (len(pending) // FRAME) * FRAME
        for start in range(0, usable, FRAME):
            self._consume(pending[start : start + FRAME])
        self._pending = pending[usable:]

    def _consume(self, frame: np.ndarray) -> None:
        if self._ptt.is_set():
            self._ptt_buf.append(frame)
            total = sum(len(piece) for piece in self._ptt_buf)
            if total > SAMPLE_RATE * 20:
                self._ptt_buf = self._ptt_buf[-MAX_SPEECH_FRAMES:]
            return
        if self._suspended.is_set() or self.config.listen_mode != "always" or self._vad is None:
            if self._in_speech:
                self._reset_capture()
                self.state.update(hearing=False)
            return

        probability = self._vad(frame)
        if not self._in_speech:
            self._preroll.append(frame)
            if probability >= START_THRESHOLD:
                self._hot_frames += 1
                if self._hot_frames >= 2:
                    self._in_speech = True
                    self._silence_frames = 0
                    self._speech = list(self._preroll)
                    self.state.update(hearing=True)
            else:
                self._hot_frames = 0
            return

        self._speech.append(frame)
        if probability < END_THRESHOLD:
            self._silence_frames += 1
        else:
            self._silence_frames = 0
        if self._silence_frames >= SILENCE_FRAMES or len(self._speech) >= MAX_SPEECH_FRAMES:
            audio = np.concatenate(self._speech) if self._speech else np.zeros(0, np.float32)
            long_enough = len(self._speech) >= MIN_SPEECH_FRAMES
            self._reset_capture()
            self.state.update(hearing=False)
            if long_enough:
                self._out.put(Utterance(_trim(audio), "vad"))

    def _reset_capture(self) -> None:
        self._in_speech = False
        self._hot_frames = 0
        self._silence_frames = 0
        self._speech = []
        self._preroll.clear()
        if self._vad is not None:
            self._vad.reset()

    def _clear_audio(self) -> None:
        try:
            while True:
                self._audio.get_nowait()
        except queue.Empty:
            pass


def list_mics() -> list[tuple[int | None, str]]:
    devices: list[tuple[int | None, str]] = [(None, "System default")]
    try:
        hosts = sd.query_hostapis()
        seen: set[str] = set()
        for index, device in enumerate(sd.query_devices()):
            if int(device["max_input_channels"]) <= 0:
                continue
            host = str(hosts[int(device["hostapi"])]["name"])
            label = f"{device['name']} — {host}"
            if label in seen:
                label = f"{label} ({index})"
            seen.add(label)
            devices.append((index, label))
    except Exception:
        log.exception("could not list microphones")
    return devices


def free_vram_mb() -> int | None:
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True,
            timeout=3,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    values = []
    for line in output.splitlines():
        line = line.strip()
        if line.isdigit():
            values.append(int(line))
    return max(values) if values else None


def _cuda_library_dirs() -> list[Path]:
    roots = [Path(sys.prefix) / "Lib" / "site-packages" / "nvidia"]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass) / "nvidia")
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent / "_internal" / "nvidia")
    folders: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        for folder in (root / "cublas" / "bin", root / "cudnn" / "bin", root / "cuda_nvrtc" / "bin"):
            key = str(folder).casefold()
            if key in seen or not folder.is_dir():
                continue
            seen.add(key)
            folders.append(folder)
    return folders


def _prepare_cuda_dlls() -> bool:
    if sys.platform != "win32":
        return True
    bins = _cuda_library_dirs()
    cublas = next((folder / "cublas64_12.dll" for folder in bins if (folder / "cublas64_12.dll").is_file()), None)
    if cublas is None:
        return False
    path = os.environ.get("PATH", "")
    for folder in bins:
        folder_text = str(folder)
        try:
            os.add_dll_directory(folder_text)
        except OSError:
            log.warning("could not add CUDA library folder %s", folder_text)
        if folder_text.casefold() not in path.casefold():
            path = folder_text + os.pathsep + path
    os.environ["PATH"] = path
    ctypes.WinDLL(str(cublas))
    for folder in bins:
        cudnn = folder / "cudnn64_9.dll"
        if cudnn.is_file():
            ctypes.WinDLL(str(cudnn))
            break
    return True


def _cuda_runtime_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(word in message for word in ("cublas", "cudnn", "cuda", "nvrtc"))


def _speech_choice(language: str) -> tuple[str, str]:
    if language == "pt-BR":
        return "small", "pt"
    return "distil-small.en", "en"


def _probe_whisper(model, language: str) -> None:
    tone = np.zeros(SAMPLE_RATE, dtype=np.float32)
    tone[::40] = 0.05
    segments, _info = model.transcribe(
        tone,
        language=language,
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
        without_timestamps=True,
        temperature=0.0,
    )
    for _segment in segments:
        break


def _load_whisper(prefer_cuda: bool, language: str = "en-US"):
    from faster_whisper import WhisperModel

    name, whisper_language = _speech_choice(language)
    WHISPER_DIR.mkdir(parents=True, exist_ok=True)
    free = free_vram_mb() if prefer_cuda else None
    if prefer_cuda and free is not None and free >= 1500:
        if not _prepare_cuda_dlls():
            log.warning("CUDA speech libraries are missing, using CPU")
        else:
            model = None
            try:
                log.info("loading %s on CUDA (%s MB free)", name, free)
                model = WhisperModel(
                    name,
                    device="cuda",
                    compute_type="int8_float16",
                    download_root=str(WHISPER_DIR),
                    num_workers=1,
                )
                _probe_whisper(model, whisper_language)
                model._servos_cuda = True
                model._servos_lang = whisper_language
                return model
            except Exception:
                log.exception("CUDA speech model failed, using CPU")
                if model is not None:
                    del model
    log.info("loading %s on CPU", name)
    model = WhisperModel(
        name,
        device="cpu",
        compute_type="int8",
        download_root=str(WHISPER_DIR),
        cpu_threads=4,
        num_workers=1,
    )
    model._servos_cuda = False
    model._servos_lang = whisper_language
    return model


def _trim(audio: np.ndarray) -> np.ndarray:
    if audio.size == 0:
        return audio
    loud = np.where(np.abs(audio) > 0.01)[0]
    if loud.size == 0:
        return audio
    start = max(0, int(loud[0]) - FRAME)
    end = min(len(audio), int(loud[-1]) + FRAME)
    return np.ascontiguousarray(audio[start:end], dtype=np.float32)


_HALLUCINATIONS = (
    "thanks for watching",
    "thank you for watching",
    "subtitles by the amara.org community",
    "thanks for listening",
    "obrigado por assistir",
    "legendas pela comunidade amara.org",
    "inscreva-se no canal",
)


def _is_hallucination(text: str, samples: int) -> bool:
    if samples > SAMPLE_RATE * 2:
        return False
    lowered = text.lower().strip(" .!")
    return any(item in lowered for item in _HALLUCINATIONS)
