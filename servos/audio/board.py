from __future__ import annotations

import numpy as np

_HALLS: dict[int, np.ndarray] = {}


def apply_board(
    clean: np.ndarray,
    present: np.ndarray,
    sample_rate: int,
    amount: float,
) -> tuple[np.ndarray, int]:
    """Several low copies of one voice, a throat-heavy bass drone, and a long dark room.

    The intelligible line stays in `present`. Pitch-shifted copies are taken
    from `clean` so the bass does not inherit the stepped droid edge.
    """
    amount = float(max(0.0, min(1.0, amount)))
    clean = np.asarray(clean, dtype=np.float32).reshape(-1)
    present = np.asarray(present, dtype=np.float32).reshape(-1)
    if present.size != clean.size:
        present = _fit_length(present, clean.size)
    if amount <= 0.001 or clean.size < 8:
        clipped = np.clip(present, -1.0, 1.0).astype(np.float32)
        return clipped, clipped.size

    sample_rate = int(sample_rate)
    if clean.size < int(sample_rate * 0.12):
        body = present
    else:
        lowered = _pitch_shift(clean, -7.0)
        lowered = _lowpass(lowered, sample_rate, 2800.0)
        lowered = _delay(lowered, int(sample_rate * 0.034))
        lowered_late = _delay(lowered, int(sample_rate * 0.041))
        sub = _lowpass(_pitch_shift(clean, -12.0), sample_rate, 240.0)
        sub = _delay(sub, int(sample_rate * 0.016))
        voice_rms = _rms(present)
        lowered = _at_rms(lowered, voice_rms)
        lowered_late = _at_rms(lowered_late, voice_rms)
        sub = _at_rms(sub, voice_rms * 1.45)
        crowd_a = _chorus(present, sample_rate, delay_ms=14.0, depth_ms=9.0, rate_hz=0.33, phase=0.4)
        crowd_b = _chorus(present, sample_rate, delay_ms=27.0, depth_ms=13.0, rate_hz=0.51, phase=2.1)
        drone = (0.5 * lowered + 0.3 * lowered_late + sub).astype(np.float32)
        drone = _lowpass(_growl(drone, amount), sample_rate, 980.0)
        drone = _chest(drone, sample_rate, amount)
        body = (
            (1.0 - 0.05 * amount) * present
            + amount * 0.18 * crowd_a
            + amount * 0.12 * crowd_b
            + amount * 0.92 * drone
        ).astype(np.float32)
        body = _rasp(body, amount)

    wet = _convolve(body, _hall(sample_rate))
    tail = int(sample_rate * 1.05)
    wet = wet[: body.size + tail]
    wet = _match_overlap(wet, body.size, _rms(present) * 0.5)
    padded = np.pad(body, (0, max(0, wet.size - body.size))).astype(np.float32)
    mixed = padded + np.float32(0.58 * amount) * wet
    mixed = _boost_lows(mixed, sample_rate, amount)
    limited = _limit(mixed, sample_rate)
    # The extra length is the room after the words. The next phrase may start
    # at the end of the words instead of waiting that tail out.
    return limited, min(body.size, limited.size)


def _pitch_shift(audio: np.ndarray, semitones: float) -> np.ndarray:
    if abs(semitones) < 0.05 or audio.size < 256:
        return np.asarray(audio, dtype=np.float32)
    ratio = 2.0 ** (semitones / 12.0)
    longer = _resample(audio, max(2, int(round(audio.size / ratio))))
    return _time_fit(longer, audio.size)


def _resample(audio: np.ndarray, length: int) -> np.ndarray:
    if length == audio.size:
        return np.asarray(audio, dtype=np.float32)
    old = np.linspace(0.0, 1.0, audio.size, endpoint=False)
    new = np.linspace(0.0, 1.0, max(length, 1), endpoint=False)
    return np.interp(new, old, audio).astype(np.float32)


def _time_fit(audio: np.ndarray, target: int) -> np.ndarray:
    """Change duration to `target` samples without changing pitch."""
    if target < 8 or audio.size < 256:
        return _resample(audio, max(target, 1))
    n_fft = 1024 if audio.size >= 1024 else 512
    hop = n_fft // 4
    window = np.hanning(n_fft).astype(np.float64)
    padded = np.pad(audio.astype(np.float64), (0, hop))
    n_frames = 1 + (padded.size - n_fft) // hop
    if n_frames < 2:
        return _resample(audio, target)
    hop_out = target / n_frames
    out = np.zeros(target + n_fft, dtype=np.float64)
    weight = np.zeros(target + n_fft, dtype=np.float64)
    bins = n_fft // 2 + 1
    expected = 2.0 * np.pi * hop * np.arange(bins) / n_fft
    prev = None
    phase = np.zeros(bins, dtype=np.float64)
    cursor = 0.0
    for frame_index in range(n_frames):
        start = frame_index * hop
        frame = padded[start : start + n_fft]
        if frame.size < n_fft:
            break
        spec = np.fft.rfft(frame * window)
        angle = np.angle(spec)
        if prev is None:
            synth = angle
        else:
            delta = angle - prev - expected
            delta -= 2.0 * np.pi * np.round(delta / (2.0 * np.pi))
            synth = phase + (expected + delta) * (hop_out / hop)
        prev = angle
        phase = synth
        rebuilt = np.fft.irfft(np.abs(spec) * np.exp(1j * synth), n=n_fft).real * window
        dest = int(cursor)
        if dest >= target:
            break
        end = min(dest + n_fft, target)
        piece = end - dest
        out[dest:end] += rebuilt[:piece]
        weight[dest:end] += window[:piece] ** 2
        cursor += hop_out
    voiced = weight[:target] > 1e-8
    result = np.zeros(target, dtype=np.float32)
    result[voiced] = (out[:target][voiced] / weight[:target][voiced]).astype(np.float32)
    return result


def _chorus(
    audio: np.ndarray,
    sample_rate: int,
    delay_ms: float,
    depth_ms: float,
    rate_hz: float,
    phase: float,
) -> np.ndarray:
    n = audio.size
    t = np.arange(n, dtype=np.float32) / np.float32(sample_rate)
    delay = (delay_ms + depth_ms * np.sin(2.0 * np.pi * rate_hz * t + phase)) * (sample_rate / 1000.0)
    idx = np.arange(n, dtype=np.float32) - delay.astype(np.float32)
    np.clip(idx, 0.0, n - 1.001, out=idx)
    i0 = idx.astype(np.int32)
    frac = idx - i0
    i1 = np.minimum(i0 + 1, n - 1)
    return (audio[i0] * (1.0 - frac) + audio[i1] * frac).astype(np.float32)


def _delay(audio: np.ndarray, samples: int) -> np.ndarray:
    samples = int(samples)
    if samples <= 0:
        return audio
    out = np.zeros_like(audio)
    if samples < audio.size:
        out[samples:] = audio[:-samples]
    return out


def _lowpass(audio: np.ndarray, sample_rate: int, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(audio.size, d=1.0 / sample_rate)
    spec *= (1.0 / (1.0 + (freqs / cutoff) ** 4)).astype(np.float32)
    return np.fft.irfft(spec, n=audio.size).astype(np.float32)


def _growl(audio: np.ndarray, amount: float) -> np.ndarray:
    """Asymmetric drive. The offset adds the even harmonics of a throat drone."""
    drive = 1.8 + 4.5 * amount
    x = audio * drive
    bias = 0.28 * amount
    shaped = np.tanh(x + bias) - np.tanh(bias)
    fold = np.sin(x * (0.85 + 0.35 * amount))
    mixed = (1.0 - 0.18 * amount) * shaped + (0.18 * amount) * fold
    return _at_rms(mixed.astype(np.float32), _rms(audio) * (1.05 + 0.25 * amount))


def _rasp(audio: np.ndarray, amount: float) -> np.ndarray:
    """A little of the same drive on the whole line, so the words pick up grit."""
    if amount <= 0.001:
        return audio
    drive = 1.0 + 1.15 * amount
    x = audio * drive
    bias = 0.08 * amount
    shaped = np.tanh(x + bias) - np.tanh(bias)
    return _at_rms(shaped.astype(np.float32), _rms(audio))


def _chest(audio: np.ndarray, sample_rate: int, amount: float) -> np.ndarray:
    spec = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(audio.size, d=1.0 / sample_rate)
    peak = np.exp(-0.5 * ((freqs - 230.0) / 85.0) ** 2)
    spec *= (1.0 + 1.35 * amount * peak).astype(np.float32)
    return np.fft.irfft(spec, n=audio.size).astype(np.float32)


def _boost_lows(audio: np.ndarray, sample_rate: int, amount: float) -> np.ndarray:
    spec = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(audio.size, d=1.0 / sample_rate)
    low_weight = 1.0 / (1.0 + (freqs / 280.0) ** 6)
    low = np.fft.irfft(spec * low_weight, n=audio.size)
    high = np.fft.irfft(spec * (1.0 - low_weight), n=audio.size)
    drive = 1.0 + 4.6 * amount
    return (high + np.tanh(low * drive)).astype(np.float32)


def _hall(sample_rate: int) -> np.ndarray:
    cached = _HALLS.get(sample_rate)
    if cached is not None:
        return cached
    seconds = 1.35
    n = int(sample_rate * seconds)
    t = np.arange(n, dtype=np.float32) / np.float32(sample_rate)
    ir = np.random.default_rng(4).standard_normal(n).astype(np.float32)
    ir *= np.exp(-6.907 * t / np.float32(1.5)).astype(np.float32)
    for ms, tap in ((11, 0.85), (23, 0.55), (41, 0.38), (67, 0.24), (97, 0.14)):
        index = int(sample_rate * ms / 1000.0)
        if 0 < index < n:
            ir[index] += np.float32(tap)
    spec = np.fft.rfft(ir)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    high = 1.0 / (1.0 + (freqs / 1800.0) ** 4)
    low = 1.0 - 1.0 / (1.0 + (freqs / 50.0) ** 2)
    spec *= (high * low).astype(np.float32)
    ir = np.fft.irfft(spec, n=n).astype(np.float32)
    ir[0] = 0.0
    peak = float(np.max(np.abs(ir))) or 1.0
    ir = (ir / peak).astype(np.float32)
    _HALLS[sample_rate] = ir
    return ir


def _convolve(audio: np.ndarray, impulse: np.ndarray) -> np.ndarray:
    n = audio.size + impulse.size - 1
    wet = np.fft.irfft(np.fft.rfft(audio, n=n) * np.fft.rfft(impulse, n=n), n=n)
    return wet.astype(np.float32)


def _limit(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak < 1e-6:
        return audio.astype(np.float32)
    driven = audio
    if peak > 0.98:
        driven = audio / peak * 0.98
    fade = min(driven.size // 2, max(2, int(0.03 * sample_rate)))
    if fade > 1:
        driven[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return driven.astype(np.float32)


def _match_overlap(audio: np.ndarray, overlap: int, target: float) -> np.ndarray:
    current = _rms(audio[:overlap])
    if current < 1e-5 or target <= 0.0:
        return audio
    return (audio * min(target / current, 4.0)).astype(np.float32)


def _fit_length(audio: np.ndarray, length: int) -> np.ndarray:
    if audio.size == length:
        return audio
    if audio.size > length:
        return audio[:length].astype(np.float32)
    return np.pad(audio, (0, length - audio.size)).astype(np.float32)


def _rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio)) + 1e-12))


def _at_rms(audio: np.ndarray, target: float) -> np.ndarray:
    current = _rms(audio)
    if current < 1e-5 or target <= 0.0:
        return audio
    return (audio * min(target / current, 4.0)).astype(np.float32)
