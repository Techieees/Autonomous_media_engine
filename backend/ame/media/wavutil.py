from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


def wav_duration_s(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        rate = handle.getframerate()
        if rate <= 0:
            return 0.0
        return handle.getnframes() / float(rate)


def is_valid_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as handle:
            return handle.getnframes() > 0 and handle.getframerate() > 0
    except wave.Error:
        return False


def write_tone_wav(
    path: Path,
    duration_s: float,
    *,
    freq: float = 220.0,
    volume: float = 0.08,
    rate: int = 22050,
) -> None:
    """Write a quiet but non-silent PCM WAV whose duration matches ``duration_s``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(max(duration_s, 0.05) * rate)))
    fade = min(int(rate * 0.02), frames // 4)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        chunks = bytearray()
        for index in range(frames):
            envelope = 1.0
            if fade and index < fade:
                envelope = index / fade
            elif fade and index > frames - fade:
                envelope = (frames - index) / fade
            sample = (
                math.sin(2.0 * math.pi * freq * index / rate)
                + 0.25 * math.sin(2.0 * math.pi * freq * 2.0 * index / rate)
            )
            value = int(max(-1.0, min(1.0, sample * volume * envelope)) * 32767)
            chunks.extend(struct.pack("<h", value))
        handle.writeframes(bytes(chunks))


def fit_wav_duration(path: Path, duration_s: float) -> float:
    """Pad with silence or trim so the WAV lasts ``duration_s`` seconds."""
    target = max(0.05, float(duration_s))
    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        sampwidth = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if rate <= 0 or sampwidth <= 0 or channels <= 0:
        write_tone_wav(path, target)
        return target
    wanted = int(round(target * rate))
    frame_size = sampwidth * channels
    current = len(frames) // frame_size
    if current < wanted:
        frames += b"\x00" * ((wanted - current) * frame_size)
    elif current > wanted:
        frames = frames[: wanted * frame_size]
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sampwidth)
        handle.setframerate(rate)
        handle.writeframes(frames)
    return target
