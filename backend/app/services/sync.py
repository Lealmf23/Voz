from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

from .media import MediaError, ffmpeg_bin

MAX_STRETCH = 1.25  # por encima se advierte en vez de deformar


def fit_ratio(generated: float, slot: float) -> float:
    """Factor de velocidad necesario para que `generated` quepa en `slot` (>1 = acelerar)."""
    return generated / slot if slot > 0 else 1.0


def time_stretch(src: Path, dst: Path, ratio: float) -> Path:
    """Time-stretch con atempo de FFmpeg (conserva el tono). ratio>1 acelera."""
    ratio = float(np.clip(ratio, 0.5, 2.0))
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(src), "-filter:a", f"atempo={ratio:.5f}",
                        str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"Time-stretch fallido: {r.stderr.strip()[:200]}")
    return dst


def fit_segment(src: Path, dst: Path, slot: float) -> dict:
    """Ajusta el audio al hueco. Devuelve duración final, factor aplicado y advertencias."""
    info = sf.info(str(src))
    gen = info.frames / info.samplerate
    ratio = fit_ratio(gen, slot)
    warnings: list[str] = []
    if ratio <= 1.0:  # cabe: se conserva sin tocar (la pausa restante queda como silencio)
        if src != dst:
            dst.write_bytes(src.read_bytes())
        return {"duration": gen, "ratio": 1.0, "warnings": warnings}
    applied = min(ratio, MAX_STRETCH)
    time_stretch(src, dst, applied)
    new = sf.info(str(dst))
    final = new.frames / new.samplerate
    if ratio > MAX_STRETCH:
        warnings.append(f"La frase excede el hueco ({final:.2f}s > {slot:.2f}s) incluso acelerada x{applied:.2f}; "
                        "acorta la traducción")
    elif ratio > 1.15:
        warnings.append(f"Aceleración notable x{applied:.2f}")
    return {"duration": final, "ratio": applied, "warnings": warnings}


def find_overlaps(placements: list[tuple[int, float, float]]) -> list[tuple[int, int]]:
    """placements: (segment_id, start, end). Devuelve pares solapados."""
    ordered = sorted(placements, key=lambda p: p[1])
    return [(a[0], b[0]) for a, b in zip(ordered, ordered[1:]) if b[1] < a[2] - 1e-6]
