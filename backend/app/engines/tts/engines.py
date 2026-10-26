from __future__ import annotations

import numpy as np
import soundfile as sf

from ..base import EngineInfo, EngineUnavailable, TTSRequest


class MockTTS:
    """SIMULADO: genera un tono con duración proporcional al texto. NO clona ninguna voz."""
    info = EngineInfo("mock-tts", "tts", "TTS (SIMULADO, sin voz)", mock=True, languages=["en-US", "pt-BR"])
    supports_prosody_transfer = False

    def synthesize(self, req: TTSRequest) -> float:
        dur = max(0.3, len(req.text) * 0.055 / max(req.speed, 0.1))
        sr = 24000
        t = np.arange(int(dur * sr)) / sr
        f = 180 + (req.seed or 0) % 60
        y = 0.2 * req.intensity * np.sin(2 * np.pi * f * t) * np.minimum(1, np.minimum(t, dur - t) * 20)
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(req.out_path), y.astype("float32"), sr)
        return dur

    def unload(self) -> None:
        pass


class ChatterboxTTS:
    """Real. Adaptador de Chatterbox Multilingual. NO ejecutado aún: requiere GPU/PC del usuario.

    Verifica en la PC de destino la API exacta de la versión instalada (`from_pretrained`, `generate`).
    Chatterbox no ofrece transferencia de prosodia por frase; solo exaggeration/cfg_weight.
    """
    info = EngineInfo("chatterbox", "tts", "Chatterbox Multilingual", languages=["en-US", "pt-BR", "es"],
                      license="MIT", notes="Clonación zero-shot. Sin transferencia directa de prosodia.")
    supports_prosody_transfer = False
    _LANG = {"en-US": "en", "pt-BR": "pt", "es": "es"}

    def __init__(self, device: str = "cuda"):
        try:
            import torch
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as e:
            raise EngineUnavailable("chatterbox-tts/torch no instalados (ver docs/INSTALLATION.md)") from e
        if device == "cuda" and not torch.cuda.is_available():
            raise EngineUnavailable("CUDA no disponible; usa device='cpu' o revisa el wheel de PyTorch (cu128)")
        self._torch = torch
        self._model = ChatterboxMultilingualTTS.from_pretrained(device=device)

    def synthesize(self, req: TTSRequest) -> float:
        if not req.reference_audio:
            raise ValueError("Se requiere al menos una muestra de referencia del perfil vocal")
        if req.seed is not None:
            self._torch.manual_seed(req.seed)
        wav = self._model.generate(req.text, language_id=self._LANG[req.language],
                                   audio_prompt_path=str(req.reference_audio[0]),
                                   exaggeration=req.emotion)
        y = wav.squeeze().cpu().numpy()
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(req.out_path), y, self._model.sr)
        return len(y) / self._model.sr

    def unload(self) -> None:
        self._model = None
        if self._torch.cuda.is_available():
            self._torch.cuda.empty_cache()
