"""Audio helpers for splitting multi-channel streams into per-speaker PCM."""

import numpy as np


def _build_ulaw_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = (((mantissa << 3) + 0x84) << exponent) - 0x84
        table[i] = -sample if sign else sample
    return table


_ULAW_TABLE = _build_ulaw_table()


def deinterleave(data: bytes, channels: int, sample_width: int) -> list[bytes]:
    """Split interleaved frames into one byte string per channel.

    A trailing partial frame is dropped.
    """
    if channels < 1 or sample_width < 1:
        raise ValueError("channels and sample_width must be >= 1")
    if channels == 1:
        return [data]
    frame = channels * sample_width
    usable = len(data) - (len(data) % frame)
    arr = np.frombuffer(data[:usable], dtype=np.uint8).reshape(-1, channels, sample_width)
    return [arr[:, c, :].tobytes() for c in range(channels)]


def pcmu_to_pcm16(data: bytes) -> bytes:
    """Decode G.711 mu-law bytes to little-endian 16-bit PCM."""
    return _ULAW_TABLE[np.frombuffer(data, dtype=np.uint8)].astype("<i2").tobytes()
