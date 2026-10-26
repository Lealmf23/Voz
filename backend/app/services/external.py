"""Servicios externos opcionales (desactivados por defecto). Hoy: traducción con Claude.

El consentimiento se guarda en un JSON local; la clave API va al almacén de credenciales del sistema
(Windows Credential Manager) y NUNCA a texto plano ni a la base de datos.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from ..core.config import Settings

SERVICE = "LealDubStudio"
KEY_NAME = "anthropic-api-key"
DEFAULT_MODEL = "claude-sonnet-5-5"


class SecretStoreError(Exception):
    pass


def _file(s: Settings) -> Path:
    return s.data_dir / "external_services.json"


def load_claude_config(s: Settings) -> dict:
    try:
        raw = json.loads(_file(s).read_text(encoding="utf-8")).get("claude_translation", {})
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}
    return {"enabled": bool(raw.get("enabled")), "consent_at": raw.get("consent_at"),
            "model": raw.get("model") or DEFAULT_MODEL}


def save_claude_config(s: Settings, cfg: dict) -> None:
    s.data_dir.mkdir(parents=True, exist_ok=True)
    _file(s).write_text(json.dumps({"claude_translation": cfg}, indent=2), encoding="utf-8")


def _keyring():
    try:
        import keyring
        return keyring
    except ImportError as e:
        raise SecretStoreError("Falta el paquete 'keyring' (pip install -r requirements/cloud.txt)") from e


def get_api_key() -> str | None:
    """Clave guardada en el almacén seguro; si no hay, ANTHROPIC_API_KEY del entorno (no se copia a disco)."""
    try:
        key = _keyring().get_password(SERVICE, KEY_NAME)
    except Exception:  # noqa: BLE001 - un almacén roto no debe impedir leer el entorno
        key = None
    return key or os.environ.get("ANTHROPIC_API_KEY") or None


def set_api_key(key: str) -> None:
    kr = _keyring()
    try:
        kr.set_password(SERVICE, KEY_NAME, key)
    except Exception as e:  # noqa: BLE001
        raise SecretStoreError(f"No se pudo guardar la clave en el almacén seguro del sistema: {e}") from e


def delete_api_key() -> None:
    try:
        _keyring().delete_password(SERVICE, KEY_NAME)
    except Exception:  # noqa: BLE001 - ya no estaba
        pass


def claude_status(s: Settings) -> dict:
    cfg = load_claude_config(s)
    return {**cfg, "key_set": get_api_key() is not None}


def update_claude(s: Settings, enabled: bool, consent_confirmed: bool, api_key: str | None,
                  model: str | None) -> dict:
    cfg = load_claude_config(s)
    if enabled and not consent_confirmed and not cfg["consent_at"]:
        raise ValueError("Debes aceptar explícitamente el envío de texto a la API de Anthropic")
    if api_key:
        set_api_key(api_key.strip())
    if enabled and consent_confirmed:
        cfg["consent_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    cfg["enabled"] = enabled
    if model:
        cfg["model"] = model.strip()
    save_claude_config(s, cfg)
    return claude_status(s)
