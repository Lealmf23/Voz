from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


class MediaError(Exception):
    pass


def ffmpeg_bin() -> str:
    p = shutil.which("ffmpeg")
    if not p:
        raise MediaError("FFmpeg no encontrado en PATH")
    return p


def probe(path: Path) -> dict:
    """Metadatos vía ffprobe. Lanza MediaError si el archivo no es multimedia válido."""
    exe = shutil.which("ffprobe")
    if not exe:
        raise MediaError("FFprobe no encontrado en PATH")
    r = subprocess.run([exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"Archivo no válido: {r.stderr.strip()[:200]}")
    info = json.loads(r.stdout)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise MediaError("El archivo no contiene pista de audio")
    return {
        "duration": float(info["format"].get("duration") or audio.get("duration") or 0),
        "sample_rate": int(audio["sample_rate"]),
        "channels": int(audio["channels"]),
        "has_video": any(s.get("codec_type") == "video" for s in info["streams"]),
    }


def validate_extension(path: Path, allowed: tuple[str, ...]) -> None:
    if path.suffix.lower() not in allowed:
        raise MediaError(f"Formato no admitido: {path.suffix}. Permitidos: {', '.join(allowed)}")


def to_working_wav(src: Path, dst: Path, sample_rate: int = 48000) -> Path:
    """Convierte a WAV mono float para análisis. El original no se toca."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([ffmpeg_bin(), "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "1",
                        "-ar", str(sample_rate), "-c:a", "pcm_s16le", str(dst)], capture_output=True, text=True)
    if r.returncode != 0:
        raise MediaError(f"Conversión fallida: {r.stderr.strip()[:200]}")
    return dst


def load_mono(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def analyze_levels(y: np.ndarray, sr: int) -> dict:
    peak = float(np.max(np.abs(y))) if y.size else 0.0
    rms = float(np.sqrt(np.mean(y**2))) if y.size else 0.0
    db = lambda v: float(20 * np.log10(max(v, 1e-9)))
    return {"peak_db": db(peak), "rms_db": db(rms), "clipping": peak >= 0.999}


def detect_speech_regions(y: np.ndarray, sr: int, threshold_db: float = -40.0, min_silence: float = 0.35,
                          min_speech: float = 0.15, frame: float = 0.02) -> list[tuple[float, float]]:
    """Regiones de habla por umbral de energía. Los silencios más cortos que min_silence no cortan."""
    n = int(sr * frame)
    if n == 0 or y.size < n:
        return []
    frames = y[: y.size // n * n].reshape(-1, n)
    db = 20 * np.log10(np.maximum(np.sqrt((frames**2).mean(axis=1)), 1e-9))
    active = db > threshold_db
    regions: list[list[float]] = []
    for i, a in enumerate(active):
        t = i * frame
        if a:
            if regions and t - regions[-1][1] <= min_silence:
                regions[-1][1] = t + frame
            else:
                regions.append([t, t + frame])
    return [(s, e) for s, e in regions if e - s >= min_speech]
