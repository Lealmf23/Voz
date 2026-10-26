from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 48000


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def build_srt(entries: list[tuple[float, float, str]]) -> str:
    out = []
    for i, (s, e, text) in enumerate(entries, 1):
        out.append(f"{i}\n{srt_time(s)} --> {srt_time(e)}\n{text.strip()}\n")
    return "\n".join(out)


def render_timeline(placements: list[tuple[Path, float]], total_duration: float, dst: Path,
                    sample_rate: int = SR, subtype: str = "PCM_24", normalize: bool = False) -> dict:
    """Mezcla clips en una línea de tiempo que empieza en t=0 y dura `total_duration`."""
    total = int(round(total_duration * sample_rate))
    buf = np.zeros(total, dtype=np.float64)
    for path, start in placements:
        y, sr = sf.read(str(path), dtype="float64", always_2d=True)
        y = y.mean(axis=1)
        if sr != sample_rate:
            from scipy.signal import resample_poly
            from math import gcd
            g = gcd(sr, sample_rate)
            y = resample_poly(y, sample_rate // g, sr // g)
        i = int(round(start * sample_rate))
        j = min(i + y.size, total)
        if i < total:
            buf[i:j] += y[: j - i]
    peak = float(np.max(np.abs(buf))) if buf.size else 0.0
    if normalize and peak > 0:
        buf *= 0.891 / peak  # -1 dBFS
    clipped = peak > 1.0 and not normalize
    buf = np.clip(buf, -1.0, 1.0)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), buf, sample_rate, subtype=subtype)
    return {"samples": total, "duration": total / sample_rate, "peak": peak, "clipped": clipped}


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
