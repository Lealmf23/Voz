from . import registry
from .transcription.engines import FasterWhisperEngine, MockTranscription
from .translation.engines import MockTranslation
from .translation.nllb import NLLBTranslation
from .tts.engines import ChatterboxTTS, MockTTS


def register_all(allow_mock: bool) -> None:
    registry.register(FasterWhisperEngine.info, FasterWhisperEngine)
    registry.register(NLLBTranslation.info, NLLBTranslation)
    registry.register(ChatterboxTTS.info, ChatterboxTTS)
    if allow_mock:
        registry.register(MockTranscription.info, MockTranscription)
        registry.register(MockTranslation.info, MockTranslation)
        registry.register(MockTTS.info, MockTTS)
