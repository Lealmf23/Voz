"""Compara Chatterbox y Qwen3-TTS con TU voz y las mismas frases. Escucha los WAV resultantes.
    python scripts/compare_tts.py mi_voz.wav salida [--ref-text "lo que dices en mi_voz.wav"]
Descarga pesos (varios GB) la primera vez. Los motores se cargan de uno en uno para no pasar de 12 GB de VRAM."""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import Settings  # noqa: E402
from app.engines import registry  # noqa: E402
from app.engines.base import TTSRequest  # noqa: E402
from app.engines.setup import register_all  # noqa: E402

TEXTS = {"en-US": ["Look at that traffic! This is insane, the whole city is blocked.",
                   "Now, let's build a roundabout here and see if it fixes the problem."],
         "pt-BR": ["Olha esse trânsito! Isso é uma loucura, a cidade inteira está parada.",
                   "Agora, vamos construir uma rotatória aqui e ver se resolve o problema."]}

ap = argparse.ArgumentParser()
ap.add_argument("voice")
ap.add_argument("out")
ap.add_argument("--ref-text")
a = ap.parse_args()
register_all(Settings(allow_mock_engines=False))
import torch  # noqa: E402

for eng_id in ("chatterbox", "qwen3-tts-base"):
    try:
        eng = registry.get("tts", eng_id)
    except Exception as e:  # noqa: BLE001
        print(f"[{eng_id}] NO DISPONIBLE: {e}")
        continue
    torch.cuda.reset_peak_memory_stats()
    for lang, texts in TEXTS.items():
        for i, text in enumerate(texts):
            t0 = time.time()
            out = Path(a.out) / f"{eng_id}_{lang}_{i}.wav"
            dur = eng.synthesize(TTSRequest(text, lang, [Path(a.voice)], out, seed=1, reference_text=a.ref_text))
            print(f"[{eng_id}] {lang}#{i}: {dur:.1f}s de audio en {time.time() - t0:.1f}s")
    print(f"[{eng_id}] VRAM pico: {torch.cuda.max_memory_allocated() // 2**20} MB")
    registry.unload_all()
