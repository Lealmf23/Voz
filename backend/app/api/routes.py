from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..database.db import GlossaryTerm, Project, Segment, VoiceProfile
from ..engines import registry
from ..engines.base import EngineUnavailable
from ..services import media, pipeline
from ..services.media import MediaError

router = APIRouter()
Lang = Literal["en-US", "pt-BR"]


# ---------- helpers ----------
def _db(request: Request):
    """Sesión ligada a la petición; el middleware de main.py la cierra al terminar."""
    session = request.app.state.db()
    request.scope.setdefault("db_sessions", []).append(session)
    return session


def _get(db, model, id_):
    obj = db.get(model, id_)
    if not obj:
        raise HTTPException(404, f"{model.__name__} {id_} no existe")
    return obj


def _seg_dict(s: Segment) -> dict:
    return {"id": s.id, "start": s.start, "end": s.end, "original_text": s.original_text,
            "source_language": s.source_language, "confidence": s.confidence, "translations": s.translations,
            "audio": {k: {kk: vv for kk, vv in v.items() if kk != "path"} for k, v in s.audio.items()},
            "params": s.params, "prosody": s.prosody, "locked": s.locked}


def _proj_dict(p: Project, with_segments: bool = False) -> dict:
    d = {"id": p.id, "name": p.name, "created_at": p.created_at.isoformat(), "source_language": p.source_language,
         "target_languages": p.target_languages, "audio_info": p.audio_info, "has_video": bool(p.video_path),
         "voice_profile_id": p.voice_profile_id, "engine_config": p.engine_config}
    if with_segments:
        d["segments"] = [_seg_dict(s) for s in p.segments]
        d["history"] = [{"at": h.at.isoformat(), "action": h.action} for h in p.history[-50:]]
    return d


def _run_job(request: Request, kind: str, fn):
    """Ejecuta fn(db, job) en la cola con su propia sesión de BD."""
    factory = request.app.state.db

    def wrapped(job):
        db = factory()
        try:
            return fn(db, job)
        finally:
            db.close()
    return request.app.state.jobs.submit(kind, wrapped).to_dict()


# ---------- sistema ----------
@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/hardware")
def hardware():
    info: dict = {"cuda": False, "gpu": None, "vram_total_mb": None, "vram_free_mb": None, "torch": None,
                  "arch_list": [], "blackwell_ok": None}
    try:
        import torch
        info["torch"] = torch.__version__
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            info.update(cuda=True, gpu=torch.cuda.get_device_name(0), vram_total_mb=total // 2**20,
                        vram_free_mb=free // 2**20, arch_list=torch.cuda.get_arch_list())
            info["blackwell_ok"] = "sm_120" in info["arch_list"]
    except ImportError:
        info["note"] = "PyTorch no instalado: sin información de GPU"
    info["ffmpeg"] = bool(shutil.which("ffmpeg"))
    return info


@router.get("/engines")
def engines():
    return [i.__dict__ for i in registry.list_engines()]


# ---------- proyectos ----------
class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_languages: list[Lang] = ["en-US", "pt-BR"]


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, request: Request):
    db = _db(request)
    p = Project(name=body.name, target_languages=list(body.target_languages))
    db.add(p)
    db.commit()
    return _proj_dict(p)


@router.get("/projects")
def list_projects(request: Request):
    return [_proj_dict(p) for p in _db(request).query(Project).order_by(Project.id.desc())]


@router.get("/projects/{pid}")
def get_project(pid: int, request: Request):
    return _proj_dict(_get(_db(request), Project, pid), with_segments=True)


@router.delete("/projects/{pid}", status_code=204)
def delete_project(pid: int, request: Request):
    db = _db(request)
    p = _get(db, Project, pid)
    d = pipeline.project_dir(request.app.state.settings, p)
    db.delete(p)
    db.commit()
    shutil.rmtree(d, ignore_errors=True)


class ImportIn(BaseModel):
    path: str
    kind: Literal["audio", "video"] = "audio"


@router.post("/projects/{pid}/import")
def import_media(pid: int, body: ImportIn, request: Request):
    db = _db(request)
    p = _get(db, Project, pid)
    src = Path(body.path)
    if not src.is_file():
        raise HTTPException(400, "El archivo no existe")
    try:
        pipeline.import_audio(db, request.app.state.settings, p, src, body.kind)
    except MediaError as e:
        raise HTTPException(400, str(e)) from e
    return _proj_dict(p)


@router.get("/projects/{pid}/audio")
def original_audio(pid: int, request: Request):
    p = _get(_db(request), Project, pid)
    path = pipeline.project_dir(request.app.state.settings, p) / "work" / "narration.wav"
    if not path.exists():
        raise HTTPException(404, "Sin audio importado")
    return FileResponse(path, media_type="audio/wav")


class ProjectPatch(BaseModel):
    name: str | None = None
    voice_profile_id: int | None = None
    engine_config: dict | None = None


@router.patch("/projects/{pid}")
def patch_project(pid: int, body: ProjectPatch, request: Request):
    db = _db(request)
    p = _get(db, Project, pid)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(p, k, v)
    db.commit()
    return _proj_dict(p)


# ---------- segmentos ----------
class SegmentPatch(BaseModel):
    start: float | None = None
    end: float | None = None
    original_text: str | None = None
    translations: dict[str, str] | None = None
    params: dict | None = None
    locked: bool | None = None


@router.patch("/segments/{sid}")
def patch_segment(sid: int, body: SegmentPatch, request: Request):
    db = _db(request)
    seg = _get(db, Segment, sid)
    data = body.model_dump(exclude_unset=True)
    start, end = data.get("start", seg.start), data.get("end", seg.end)
    if end <= start:
        raise HTTPException(422, "end debe ser mayor que start")
    if "translations" in data:
        data["translations"] = {**seg.translations, **data["translations"]}
    for k, v in data.items():
        setattr(seg, k, v)
    pipeline.log_history(db, seg.project, f"Segmento {sid} editado")
    db.commit()
    return _seg_dict(seg)


@router.get("/segments/{sid}/audio/{lang}")
def segment_audio(sid: int, lang: str, request: Request):
    seg = _get(_db(request), Segment, sid)
    a = seg.audio.get(lang)
    if not a or not Path(a["path"]).exists():
        raise HTTPException(404, "Sin audio generado")
    return FileResponse(a["path"], media_type="audio/wav")


class SynthIn(BaseModel):
    language: Lang
    engine: str


@router.post("/segments/{sid}/synthesize")
def synth_one(sid: int, body: SynthIn, request: Request):
    """Regenera una sola frase (síncrono)."""
    db = _db(request)
    seg = _get(db, Segment, sid)
    try:
        return pipeline.synthesize_segment(db, request.app.state.settings, seg.project, seg, body.language,
                                           body.engine)
    except EngineUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ---------- tareas ----------
class TranscribeIn(BaseModel):
    engine: str
    language: str | None = "es"


class TranslateIn(BaseModel):
    engine: str
    language: Lang
    only_missing: bool = False


class SynthAllIn(BaseModel):
    engine: str
    language: Lang


class ExportIn(BaseModel):
    output_dir: str
    languages: list[Lang] = ["en-US", "pt-BR"]
    normalize: bool = False
    bit_depth: Literal[16, 24] = 24


@router.post("/projects/{pid}/transcribe", status_code=202)
def transcribe(pid: int, body: TranscribeIn, request: Request):
    _get(_db(request), Project, pid)
    s = request.app.state.settings
    return _run_job(request, "transcribe", lambda db, job: pipeline.transcribe(
        db, s, _get(db, Project, pid), body.engine, job, body.language))


@router.post("/projects/{pid}/translate", status_code=202)
def translate(pid: int, body: TranslateIn, request: Request):
    _get(_db(request), Project, pid)
    return _run_job(request, "translate", lambda db, job: pipeline.translate(
        db, _get(db, Project, pid), body.language, body.engine, job, body.only_missing))


@router.post("/projects/{pid}/synthesize", status_code=202)
def synthesize(pid: int, body: SynthAllIn, request: Request):
    _get(_db(request), Project, pid)
    s = request.app.state.settings
    return _run_job(request, "synthesize", lambda db, job: pipeline.synthesize_all(
        db, s, _get(db, Project, pid), body.language, body.engine, job))


@router.post("/projects/{pid}/analyze-prosody", status_code=202)
def prosody(pid: int, request: Request):
    _get(_db(request), Project, pid)
    s = request.app.state.settings
    return _run_job(request, "prosody", lambda db, job: pipeline.analyze_prosody(s, db, _get(db, Project, pid)) or {})


@router.post("/projects/{pid}/export")
def export(pid: int, body: ExportIn, request: Request):
    db = _db(request)
    p = _get(db, Project, pid)
    return pipeline.export_project(db, request.app.state.settings, p, Path(body.output_dir), list(body.languages),
                                   body.normalize, "PCM_24" if body.bit_depth == 24 else "PCM_16")


@router.get("/jobs/{jid}")
def job(jid: str, request: Request):
    j = request.app.state.jobs.jobs.get(jid)
    if not j:
        raise HTTPException(404, "Tarea desconocida")
    return j.to_dict()


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str, request: Request):
    return {"cancelled": request.app.state.jobs.cancel(jid)}


# ---------- voces ----------
class VoiceIn(BaseModel):
    name: str = Field(min_length=1)
    sample_paths: list[str] = Field(min_length=1)
    consent_confirmed: bool


@router.post("/voices", status_code=201)
def create_voice(body: VoiceIn, request: Request):
    if not body.consent_confirmed:
        raise HTTPException(400, "Debes confirmar que tienes autorización del titular de la voz")
    s = request.app.state.settings
    db = _db(request)
    vp = VoiceProfile(name=body.name, consent_confirmed=True)
    db.add(vp)
    db.flush()
    vdir = s.data_dir / "voices" / f"{vp.id:04d}"
    vdir.mkdir(parents=True, exist_ok=True)
    samples, quality = [], {}
    for i, sp in enumerate(body.sample_paths):
        src = Path(sp)
        try:
            media.validate_extension(src, s.allowed_audio)
            if not src.is_file():
                raise MediaError("El archivo no existe")
            info = media.probe(src)
            dst = media.to_working_wav(src, vdir / f"sample_{i:02d}.wav", 24000)
            y, sr = media.load_mono(dst)
        except MediaError as e:
            db.rollback()
            raise HTTPException(400, f"{src.name}: {e}") from e
        q = {**info, **media.analyze_levels(y, sr), "warnings": []}
        if info["duration"] < 5:
            q["warnings"].append("Muestra muy corta (<5 s)")
        if q["clipping"]:
            q["warnings"].append("Saturación detectada")
        samples.append(str(dst))
        quality[dst.name] = q
    vp.samples, vp.quality = samples, quality
    db.commit()
    return {"id": vp.id, "name": vp.name, "quality": quality}


@router.get("/voices")
def list_voices(request: Request):
    return [{"id": v.id, "name": v.name, "samples": len(v.samples), "quality": v.quality}
            for v in _db(request).query(VoiceProfile)]


@router.delete("/voices/{vid}", status_code=204)
def delete_voice(vid: int, request: Request):
    db = _db(request)
    v = _get(db, VoiceProfile, vid)
    for sp in v.samples:
        shutil.rmtree(Path(sp).parent, ignore_errors=True)
    for p in db.query(Project).filter(Project.voice_profile_id == vid):
        p.voice_profile_id = None
    db.delete(v)
    db.commit()


# ---------- glosario ----------
class TermIn(BaseModel):
    term: str = Field(min_length=1)
    en: str | None = None
    pt: str | None = None
    do_not_translate: bool = False
    priority: int = 0


@router.get("/glossary")
def list_terms(request: Request):
    return [{"id": t.id, "term": t.term, "en": t.en, "pt": t.pt, "do_not_translate": t.do_not_translate,
             "priority": t.priority} for t in _db(request).query(GlossaryTerm).order_by(GlossaryTerm.priority.desc())]


@router.post("/glossary", status_code=201)
def add_term(body: TermIn, request: Request):
    db = _db(request)
    t = GlossaryTerm(**body.model_dump())
    db.add(t)
    db.commit()
    return {"id": t.id, **body.model_dump()}


@router.delete("/glossary/{tid}", status_code=204)
def delete_term(tid: int, request: Request):
    db = _db(request)
    db.delete(_get(db, GlossaryTerm, tid))
    db.commit()
