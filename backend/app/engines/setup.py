from ..core.config import Settings
from . import registry
from .transcription.engines import FasterWhisperEngine, MockTranscription
from .translation.claude import ClaudeTranslation
from .translation.engines import MockTranslation
from .translation.nllb import NLLBTranslation
from .tts.engines import ChatterboxTTS, MockTTS


def register_all(settings: Settings) -> None:
    registry.register(FasterWhisperEngine.info, FasterWhisperEngine)
    registry.register(NLLBTranslation.info, NLLBTranslation)
    registry.register(ClaudeTranslation.info, lambda: ClaudeTranslation(settings))
    registry.register(ChatterboxTTS.info, ChatterboxTTS)
    if settings.allow_mock_engines:
        registry.register(MockTranscription.info, MockTranscription)
        registry.register(MockTranslation.info, MockTranslation)
        registry.register(MockTTS.info, MockTTS)
