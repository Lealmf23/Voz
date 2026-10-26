from __future__ import annotations

from pathlib import Path

from ...services.media import detect_speech_regions, load_mono
from ..base import EngineInfo, EngineUnavailable, TranscribedSegment


class MockTranscription:
    """SIMULADO: segmenta por silencios (real) pero NO reconoce voz. El texto queda vacío."""
    info = EngineInfo("mock-silence", "transcription", "Segmentación por silencios (SIMULADO)", mock=True,
                      languages=["es"], notes="No transcribe: solo propone segmentos. Rellena el texto a mano.")

    def transcribe(self, audio: Path, language: str | None) -> list[TranscribedSegment]:
        y, sr = load_mono(audio)
        return [TranscribedSegment(s, e, "", None) for s, e in detect_speech_regions(y, sr)]

    def unload(self) -> None:
        pass


class FasterWhisperEngine:
    """Real. Requiere `faster-whisper`. NO verificado en este entorno (sin GPU)."""
    info = EngineInfo("faster-whisper", "transcription", "Faster-Whisper", languages=["es", "en", "pt"],
                      license="MIT (código); pesos Whisper MIT")

    def __init__(self, model: str = "large-v3", device: str = "auto", compute_type: str = "float16"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise EngineUnavailable("faster-whisper no está instalado (pip install -r requirements/ai.txt)") from e
        if device == "cpu":
            compute_type = "int8"
        self._model = WhisperModel(model, device=device, compute_type=compute_type)

    def transcribe(self, audio: Path, language: str | None) -> list[TranscribedSegment]:
        segs, _ = self._model.transcribe(str(audio), language=language, vad_filter=True)
        out = []
        for s in segs:
            # avg_logprob es la única medida real del motor; se convierte a probabilidad media por token.
            import math
            out.append(TranscribedSegment(s.start, s.end, s.text.strip(), math.exp(s.avg_logprob)))
        return out

    def unload(self) -> None:
        self._model = None
