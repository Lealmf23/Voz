from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    source_language: Mapped[str] = mapped_column(String(10), default="es")
    target_languages: Mapped[list] = mapped_column(JSON, default=lambda: ["en-US", "pt-BR"])
    audio_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    video_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    audio_info: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    voice_profile_id: Mapped[int | None] = mapped_column(ForeignKey("voice_profiles.id"), nullable=True)
    engine_config: Mapped[dict] = mapped_column(JSON, default=dict)
    segments: Mapped[list["Segment"]] = relationship(back_populates="project", cascade="all, delete-orphan",
                                                      order_by="Segment.start")
    history: Mapped[list["HistoryEntry"]] = relationship(cascade="all, delete-orphan")


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    original_text: Mapped[str] = mapped_column(Text, default="")
    source_language: Mapped[str] = mapped_column(String(10), default="es")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)  # solo si el motor lo da
    translations: Mapped[dict] = mapped_column(JSON, default=dict)  # {"en-US": "..."}
    # {"en-US": {"path": "...", "duration": 1.2, "engine": "mock-tts", "mock": true, "warnings": []}}
    audio: Mapped[dict] = mapped_column(JSON, default=dict)
    params: Mapped[dict] = mapped_column(JSON, default=dict)  # speed, intensity, emotion, seed
    prosody: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    project: Mapped[Project] = relationship(back_populates="segments")


class HistoryEntry(Base):
    __tablename__ = "history"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    action: Mapped[str] = mapped_column(String(200))


class VoiceProfile(Base):
    __tablename__ = "voice_profiles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    samples: Mapped[list] = mapped_column(JSON, default=list)  # rutas a muestras procesadas
    quality: Mapped[dict] = mapped_column(JSON, default=dict)
    consent_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    engine_settings: Mapped[dict] = mapped_column(JSON, default=dict)


class GlossaryTerm(Base):
    __tablename__ = "glossary"
    id: Mapped[int] = mapped_column(primary_key=True)
    term: Mapped[str] = mapped_column(String(200))
    en: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pt: Mapped[str | None] = mapped_column(String(200), nullable=True)
    do_not_translate: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)


def make_session_factory(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
