"""Prueba de viabilidad: clona tu voz en en-US y pt-BR con Chatterbox y mide la VRAM.
Descarga los pesos de Chatterbox la primera vez (varios GB). Uso:
    python scripts/voice_clone_test.py MI_VOZ.wav salida_dir
Escucha los WAV resultantes: la semejanza y la pronunciación pt-BR solo se juzgan de oído."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.engines.base import TTSRequest  # noqa: E402
from app.engines.tts.engines import ChatterboxTTS  # noqa: E402

TEXTS = {"en-US": "Look at that traffic! This is insane, the whole city is blocked.",
         "pt-BR": "Olha esse trânsito! Isso é uma loucura, a cidade inteira está parada."}

ref, out = Path(sys.argv[1]), Path(sys.argv[2])
eng = ChatterboxTTS()
import torch  # noqa: E402
for lang, text in TEXTS.items():
    t0 = time.time()
    dur = eng.synthesize(TTSRequest(text, lang, [ref], out / f"test_{lang}.wav", seed=1))
    print(f"{lang}: {dur:.2f}s de audio en {time.time() - t0:.1f}s | "
          f"VRAM pico {torch.cuda.max_memory_allocated() // 2**20} MB")
