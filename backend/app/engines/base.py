from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class EngineInfo:
    id: str
    kind: str  # transcription | translation | tts
    name: str
    mock: bool = False
    languages: list[str] = field(default_factory=list)
    license: str = ""
    notes: str = ""


@dataclass
class TranscribedSegment:
    start: float
    end: float
    text: str
    confidence: float | None = None  # None si el motor no lo proporciona


class TranscriptionEngine(Protocol):
    info: EngineInfo
    def transcribe(self, audio: Path, language: str | None) -> list[TranscribedSegment]: ...
    def unload(self) -> None: ...


class TranslationEngine(Protocol):
    info: EngineInfo
    def translate(self, text: str, source: str, target: str, context_before: list[str],
                  context_after: list[str]) -> str: ...
    def unload(self) -> None: ...


@dataclass
class TTSRequest:
    text: str
    language: str
    reference_audio: list[Path]
    out_path: Path
    seed: int | None = None
    speed: float = 1.0
    intensity: float = 1.0
    emotion: float = 0.5
    prosody_reference: Path | None = None  # solo lo usan motores que lo admiten


class TTSEngine(Protocol):
    info: EngineInfo
    supports_prosody_transfer: bool
    def synthesize(self, req: TTSRequest) -> float: ...  # devuelve duración en segundos
    def unload(self) -> None: ...


class EngineUnavailable(Exception):
    """El motor no está instalado o no es compatible con el hardware."""
