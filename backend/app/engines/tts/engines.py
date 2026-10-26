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


class Qwen3TTS:
    """Real. Qwen3-TTS Base (clonación zero-shot). NO ejecutado: sin GPU en el entorno de desarrollo.

    API tomada del README oficial (`Qwen3TTSModel.from_pretrained`, `generate_voice_clone`); verifícala con la
    versión instalada. El modelo Base NO documenta control de estilo/emoción: `speed`, `intensity` y `emotion`
    se ignoran aquí (la velocidad se aplica después con time-stretch). Pesos Apache-2.0.
    Usa la transcripción de la muestra (`reference_text`) si existe; sin ella recurre a `x_vector_only_mode`,
    que según el README puede bajar la calidad.
    """
    info = EngineInfo("qwen3-tts-base", "tts", "Qwen3-TTS 1.7B Base", languages=["en-US", "pt-BR", "es"],
                      license="Apache-2.0", notes="Clona con pocos segundos de audio. Sin control de estilo en el modelo Base. "
                      "No confirmado si 'Portuguese' es pt-BR: comprobar de oído.")
    supports_prosody_transfer = False
    _LANG = {"en-US": "English", "pt-BR": "Portuguese", "es": "Spanish"}

    def __init__(self, device: str = "cuda:0", model=None, model_id: str = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"):
        if model is not None:  # inyección para pruebas
            self._model = model
            return
        try:
            import torch
            from qwen_tts import Qwen3TTSModel
        except ImportError as e:
            raise EngineUnavailable("qwen-tts/torch no instalados (requirements/qwen.txt)") from e
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise EngineUnavailable("CUDA no disponible; revisa el wheel de PyTorch (cu128)")
        # sdpa evita depender de flash-attn, difícil de instalar en Windows.
        self._model = Qwen3TTSModel.from_pretrained(model_id, device_map=device, dtype=torch.bfloat16,
                                                    attn_implementation="sdpa")

    def synthesize(self, req: TTSRequest) -> float:
        if not req.reference_audio:
            raise ValueError("Se requiere al menos una muestra de referencia del perfil vocal")
        if req.seed is not None:
            try:
                import torch
                torch.manual_seed(req.seed)
            except ImportError:
                pass
        kw = {"ref_text": req.reference_text} if req.reference_text else {"x_vector_only_mode": True}
        wavs, sr = self._model.generate_voice_clone(text=req.text, language=self._LANG[req.language],
                                                    ref_audio=str(req.reference_audio[0]), **kw)
        y = np.asarray(wavs[0], dtype="float32").squeeze()
        req.out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(req.out_path), y, sr)
        return len(y) / sr

    def unload(self) -> None:
        self._model = None
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
