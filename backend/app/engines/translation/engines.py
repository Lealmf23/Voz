from __future__ import annotations

from ..base import EngineInfo


class MockTranslation:
    """SIMULADO: no traduce, antepone una etiqueta para que sea evidente en la UI."""
    info = EngineInfo("mock-translate", "translation", "Traducción (SIMULADA)", mock=True,
                      languages=["es", "en-US", "pt-BR"])

    def translate(self, text, source, target, context_before, context_after) -> str:
        return f"[{target} SIMULADO] {text}" if text else ""

    def unload(self) -> None:
        pass
