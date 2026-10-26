from __future__ import annotations

from typing import Callable

from .base import EngineInfo, EngineUnavailable

_factories: dict[tuple[str, str], tuple[EngineInfo, Callable[[], object]]] = {}
_loaded: dict[tuple[str, str], object] = {}


def register(info: EngineInfo, factory: Callable[[], object]) -> None:
    _factories[(info.kind, info.id)] = (info, factory)


def list_engines(kind: str | None = None) -> list[EngineInfo]:
    return [i for (k, _), (i, _) in _factories.items() if kind in (None, k)]


def get(kind: str, engine_id: str):
    """Carga el motor. Solo se mantiene un motor TTS en memoria a la vez (límite de VRAM)."""
    key = (kind, engine_id)
    if key not in _factories:
        raise EngineUnavailable(f"Motor desconocido: {kind}/{engine_id}")
    if key not in _loaded:
        if kind == "tts":
            for k in [k for k in _loaded if k[0] == "tts"]:
                _loaded.pop(k).unload()
        _loaded[key] = _factories[key][1]()
    return _loaded[key]


def unload_all() -> None:
    for eng in _loaded.values():
        eng.unload()
    _loaded.clear()
