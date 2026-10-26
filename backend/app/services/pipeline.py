from __future__ import annotations

import shutil
from pathlib import Path

import soundfile as sf
from sqlalchemy.orm import Session

from ..core.config import Settings
from ..database.db import GlossaryTerm, HistoryEntry, Project, Segment, VoiceProfile
from ..engines import registry
from ..engines.base import TTSRequest
from ..engines.prosody.analyzer import analyze
from ..workers.jobs import Job
from . import export as exp
from . import glossary as gl
from . import media, sync

LANGS = {"en-US": ("english", "Leal_EN"), "pt-BR": ("portuguese", "Leal_PT_BR")}


def log_history(db: Session, project: Project, action: str) -> None:
    db.add(HistoryEntry(project_id=project.id, action=action))


def project_dir(s: Settings, p: Project) -> Path:
    d = s.projects_dir / f"{p.id:04d}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def import_audio(db: Session, s: Settings, p: Project, src: Path, kind: str = "audio") -> Project:
    media.validate_extension(src, s.allowed_audio + s.allowed_video)
    info = media.probe(src)
    dest = project_dir(s, p) / "source" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)  # el original del usuario no se modifica
    work = media.to_working_wav(dest, project_dir(s, p) / "work" / "narration.wav", 48000)
    y, sr = media.load_mono(work)
    info.update(media.analyze_levels(y, sr))
    info["speech_regions"] = len(media.detect_speech_regions(y, sr))
    if kind == "video":
        p.video_path = str(dest)
    p.audio_path = str(dest) if kind == "audio" else p.audio_path or str(dest)
    p.audio_info = info
    log_history(db, p, f"Importado {src.name}")
    db.commit()
    return p


def transcribe(db: Session, s: Settings, p: Project, engine_id: str, job: Job, language: str | None = "es") -> dict:
    eng = registry.get("transcription", engine_id)
    job.step("Transcribiendo", 0, 1)
    segs = eng.transcribe(project_dir(s, p) / "work" / "narration.wav", language)
    for old in list(p.segments):
        db.delete(old)
    db.flush()
    for i, t in enumerate(segs):
        job.step("Guardando segmentos", i, len(segs))
        db.add(Segment(project_id=p.id, start=t.start, end=t.end, original_text=t.text,
                       source_language=language or "es", confidence=t.confidence))
    log_history(db, p, f"Transcripción con {eng.info.id}")
    db.commit()
    return {"segments": len(segs), "mock": eng.info.mock}


def translate(db: Session, p: Project, lang: str, engine_id: str, job: Job, only_missing: bool = False) -> dict:
    eng = registry.get("translation", engine_id)
    terms = [gl.Term(t.term, t.en, t.pt, t.do_not_translate, t.priority) for t in db.query(GlossaryTerm)]
    segs = list(p.segments)
    for i, seg in enumerate(segs):
        job.step(f"Traduciendo a {lang}", i, len(segs))
        if seg.locked or (only_missing and seg.translations.get(lang)):
            continue
        before = [x.original_text for x in segs[max(0, i - 2):i]]
        after = [x.original_text for x in segs[i + 1:i + 3]]
        text, mapping = gl.protect(seg.original_text, terms, lang)
        out = eng.translate(text, p.source_language, lang, before, after)
        seg.translations = {**seg.translations, lang: gl.restore(out, mapping)}
    log_history(db, p, f"Traducción {lang} con {eng.info.id}")
    db.commit()
    return {"mock": eng.info.mock}


def _voice_refs(db: Session, p: Project) -> tuple[list[Path], str | None]:
    if not p.voice_profile_id:
        return [], None
    vp = db.get(VoiceProfile, p.voice_profile_id)
    if not vp:
        return [], None
    return [Path(x) for x in vp.samples], (vp.engine_settings or {}).get("reference_text")


def synthesize_segment(db: Session, s: Settings, p: Project, seg: Segment, lang: str, engine_id: str) -> dict:
    text = seg.translations.get(lang, "").strip()
    if not text:
        raise ValueError("El segmento no tiene traducción")
    eng = registry.get("tts", engine_id)
    refs, ref_text = _voice_refs(db, p)
    if not refs and not eng.info.mock:
        raise ValueError("Selecciona un perfil vocal con muestras antes de generar")
    out_dir = project_dir(s, p) / "generated" / lang
    raw, fitted = out_dir / f"seg_{seg.id:05d}_raw.wav", out_dir / f"seg_{seg.id:05d}.wav"
    pr = seg.params or {}
    eng.synthesize(TTSRequest(text=text, language=lang, reference_audio=refs, out_path=raw,
                              seed=pr.get("seed"), speed=pr.get("speed", 1.0),
                              intensity=pr.get("intensity", 1.0), emotion=pr.get("emotion", 0.5),
                              reference_text=ref_text))
    fit = sync.fit_segment(raw, fitted, seg.end - seg.start)
    seg.audio = {**seg.audio, lang: {"path": str(fitted), "duration": fit["duration"], "ratio": fit["ratio"],
                                     "engine": eng.info.id, "mock": eng.info.mock, "warnings": fit["warnings"]}}
    db.commit()
    return seg.audio[lang]


def synthesize_all(db: Session, s: Settings, p: Project, lang: str, engine_id: str, job: Job) -> dict:
    segs = [x for x in p.segments if x.translations.get(lang)]
    for i, seg in enumerate(segs):
        job.step(f"Generando {lang}", i, len(segs))
        if seg.locked and seg.audio.get(lang):
            continue
        synthesize_segment(db, s, p, seg, lang, engine_id)
    log_history(db, p, f"Audio {lang} generado")
    db.commit()
    return {"segments": len(segs)}


def analyze_prosody(s: Settings, db: Session, p: Project) -> None:
    y, sr = media.load_mono(project_dir(s, p) / "work" / "narration.wav")
    for seg in p.segments:
        seg.prosody = analyze(y[int(seg.start * sr):int(seg.end * sr)], sr, seg.original_text)
    db.commit()


def export_project(db: Session, s: Settings, p: Project, out_root: Path, langs: list[str],
                   normalize: bool = False, subtype: str = "PCM_24") -> dict:
    total = (p.audio_info or {}).get("duration") or max((x.end for x in p.segments), default=0)
    report: dict = {"project": p.name, "duration": total, "languages": {}, "warnings": []}
    for lang in langs:
        folder, base = LANGS[lang]
        placed, srt, spans, missing = [], [], [], []
        for seg in p.segments:
            a = seg.audio.get(lang)
            if not a or not Path(a["path"]).exists():
                missing.append(seg.id)
                continue
            placed.append((Path(a["path"]), seg.start))
            end = seg.start + a["duration"]
            spans.append((seg.id, seg.start, end))
            srt.append((seg.start, end, seg.translations[lang]))
            report["warnings"] += [f"seg {seg.id} ({lang}): {w}" for w in a.get("warnings", [])]
            if a.get("mock"):
                report["warnings"].append(f"seg {seg.id} ({lang}): audio SIMULADO, no es voz real")
        info = exp.render_timeline(placed, total, out_root / folder / f"{base}.wav", subtype=subtype,
                                   normalize=normalize)
        (out_root / folder / f"{base}.srt").write_text(exp.build_srt(srt), encoding="utf-8")
        overlaps = sync.find_overlaps(spans)
        report["languages"][lang] = {**info, "segments_exported": len(placed), "missing_audio": missing,
                                     "overlaps": overlaps}
    exp.write_report(out_root / "reports" / "export_report.json", report)
    return report
