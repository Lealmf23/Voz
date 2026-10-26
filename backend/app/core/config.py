from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel


class Settings(BaseModel):
    host: str = "127.0.0.1"  # solo localhost por defecto
    port: int = 8765
    data_dir: Path = Path(os.environ.get("LEAL_DATA_DIR", Path.home() / "LealDubStudio"))
    sample_rate: int = 48000
    allowed_audio: tuple[str, ...] = (".wav", ".mp3", ".flac", ".m4a")
    allowed_video: tuple[str, ...] = (".mp4", ".mov", ".mkv")
    # Si es True, los motores simulados están permitidos (desarrollo/pruebas).
    allow_mock_engines: bool = os.environ.get("LEAL_ALLOW_MOCK", "1") == "1"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "leal.db"

    def ensure_dirs(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    return Settings()
