from __future__ import annotations

import numpy as np


def _f0_autocorr(frame: np.ndarray, sr: int, fmin=70.0, fmax=400.0) -> float | None:
    frame = frame - frame.mean()
    if np.max(np.abs(frame)) < 1e-3:
        return None
    ac = np.correlate(frame, frame, mode="full")[frame.size - 1:]
    lo, hi = int(sr / fmax), int(sr / fmin)
    if hi >= ac.size or ac[0] <= 0:
        return None
    k = lo + int(np.argmax(ac[lo:hi]))
    return sr / k if ac[k] / ac[0] > 0.3 else None  # umbral de voz


def analyze(y: np.ndarray, sr: int, text: str = "") -> dict:
    """Rasgos medidos (RMS, F0, pausas, ritmo). NO infiere emociones: eso exigiría un modelo."""
    dur = y.size / sr if sr else 0.0
    n = int(sr * 0.03)
    frames = y[: y.size // n * n].reshape(-1, n) if n and y.size >= n else np.zeros((0, 1))
    rms = np.sqrt((frames**2).mean(axis=1)) if frames.size else np.array([])
    f0s = [f for f in (_f0_autocorr(fr, sr) for fr in frames[:: 2]) if f]
    voiced = rms > 10 ** (-40 / 20) if rms.size else np.array([], bool)
    words = len(text.split())
    return {
        "duration": dur,
        "rms_mean_db": float(20 * np.log10(max(rms[voiced].mean(), 1e-9))) if voiced.any() else None,
        "rms_peak_db": float(20 * np.log10(max(rms.max(), 1e-9))) if rms.size else None,
        "rms_dynamic_range_db": float(20 * np.log10(max(rms[voiced].max(), 1e-9) / max(rms[voiced].mean(), 1e-9)))
        if voiced.any() else None,
        "f0_median_hz": float(np.median(f0s)) if f0s else None,
        "f0_range_hz": [float(np.percentile(f0s, 10)), float(np.percentile(f0s, 90))] if len(f0s) > 3 else None,
        "pause_ratio": float(1 - voiced.mean()) if voiced.size else None,
        "words_per_second": words / dur if dur and words else None,
    }
