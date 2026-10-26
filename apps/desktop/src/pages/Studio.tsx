import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, API, EngineInfo, Lang, patch, post, Project } from "../api";
import { ABCompare } from "../components/ABCompare";
import { SegmentRow } from "../components/SegmentRow";
import { ErrorNote, Mock, Title, useRunJob } from "../components/ui";
import { Waveform } from "../components/Waveform";
import { useApp } from "../store";

export default function Studio() {
  const { projectId, engines, go } = useApp();
  const qc = useQueryClient();
  const run = useRunJob();
  const [path, setPath] = useState("");
  const [outDir, setOutDir] = useState("");
  const [sel, setSel] = useState<number | null>(null);
  const [report, setReport] = useState<string | null>(null);
  const key = ["project", projectId];
  const proj = useQuery({ queryKey: key, enabled: projectId != null, queryFn: () => api<Project>(`/projects/${projectId}`), refetchInterval: 2000 });
  const eng = useQuery({ queryKey: ["engines"], queryFn: () => api<EngineInfo[]>("/engines") });
  const voices = useQuery({ queryKey: ["voices"], queryFn: () => api<{ id: number; name: string }[]>("/voices") });
  const refresh = () => qc.invalidateQueries({ queryKey: key });
  const act = useMutation({ mutationFn: (fn: () => Promise<unknown>) => fn(), onSuccess: refresh });

  if (projectId == null) return <div><Title>Estudio de doblaje</Title><p className="text-mute">Abre un proyecto desde <button className="text-primary2 underline" onClick={() => go("projects")}>Proyectos</button>.</p></div>;
  const p = proj.data;
  const segs = p?.segments ?? [];
  const isMock = (kind: string) => eng.data?.find((e) => e.id === engines[kind])?.mock ?? false;
  const selected = segs.find((s) => s.id === sel) ?? segs[0];

  return (
    <div className="space-y-4">
      <Title>{p?.name ?? "…"}</Title>
      <ErrorNote error={proj.error ?? act.error} />
      <section className="card flex gap-2 items-end flex-wrap">
        <label className="flex-1 min-w-64 text-xs text-mute">Ruta de la narración (WAV/MP3/FLAC/M4A) — el selector nativo llegará con Tauri
          <input className="input" aria-label="Ruta de la narración" value={path} onChange={(e) => setPath(e.target.value)} placeholder="C:\Proyectos\narracion.wav" /></label>
        <button className="btn" disabled={!path} onClick={() => act.mutate(() => post(`/projects/${projectId}/import`, { path }))}>Importar</button>
        <label className="text-xs text-mute">Perfil vocal
          <select className="input" value={p?.voice_profile_id ?? ""} onChange={(e) => act.mutate(() => patch(`/projects/${projectId}`, { voice_profile_id: e.target.value ? Number(e.target.value) : null }))}>
            <option value="">— ninguno —</option>{voices.data?.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></label>
      </section>
      {p?.audio_info && (
        <section className="card text-sm">
          <div className="text-mute mb-2">{p.audio_info.duration.toFixed(1)} s · {p.audio_info.sample_rate} Hz · {p.audio_info.channels} canal(es) · pico {p.audio_info.peak_db.toFixed(1)} dB · RMS {p.audio_info.rms_db.toFixed(1)} dB · {p.audio_info.speech_regions} regiones de habla{p.audio_info.clipping && <span className="text-warn"> · saturación</span>}</div>
          <Waveform url={`${API}/projects/${projectId}/audio`} />
        </section>)}
      <section className="card flex gap-2 flex-wrap items-center">
        <button className="btn" disabled={!p?.audio_info} onClick={() => act.mutate(() => run(`/projects/${projectId}/transcribe`, { engine: engines.transcription }))}>1 · Transcribir</button><Mock on={isMock("transcription")} />
        <button className="btn" disabled={!segs.length} onClick={() => act.mutate(async () => { for (const l of ["en-US", "pt-BR"] as Lang[]) await run(`/projects/${projectId}/translate`, { engine: engines.translation, language: l }); })}>2 · Traducir EN + PT</button><Mock on={isMock("translation")} />
        <button className="btn" disabled={!segs.length} onClick={() => act.mutate(async () => { for (const l of ["en-US", "pt-BR"] as Lang[]) await run(`/projects/${projectId}/synthesize`, { engine: engines.tts, language: l }); })}>3 · Generar voz</button><Mock on={isMock("tts")} />
        <button className="btn-ghost" disabled={!segs.length} onClick={() => act.mutate(() => run(`/projects/${projectId}/analyze-prosody`))}>Analizar prosodia</button>
        <span className="text-xs text-mute">Motores elegidos en Modelos de IA.</span>
      </section>
      <section className="card overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-mute text-xs"><tr><th className="p-2">Tiempo</th><th>Original</th><th>Inglés (en-US)</th><th>Portugués (pt-BR)</th><th>Acciones</th></tr></thead>
          <tbody>{segs.map((s) => (
            <SegmentRow key={`${s.id}-${s.original_text}-${JSON.stringify(s.translations)}`} seg={s} busy={act.isPending}
              onSave={(id, body) => act.mutate(() => patch(`/segments/${id}`, body))}
              onRegenerate={(id, language) => { setSel(id); act.mutate(() => post(`/segments/${id}/synthesize`, { language, engine: engines.tts })); }} />))}</tbody>
        </table>
        {!segs.length && <p className="text-mute text-sm p-2">Sin segmentos. Importa audio y pulsa Transcribir.</p>}
      </section>
      {selected && <ABCompare key={selected.id} projectId={projectId} seg={selected} />}
      {segs.length > 0 && <div className="text-xs text-mute">Segmento para A/B: <select className="input w-auto inline" value={selected?.id} onChange={(e) => setSel(Number(e.target.value))}>{segs.map((s) => <option key={s.id} value={s.id}>#{s.id} · {s.start.toFixed(1)}s</option>)}</select></div>}
      <section className="card flex gap-2 items-end">
        <label className="flex-1 text-xs text-mute">Carpeta de exportación
          <input className="input" aria-label="Carpeta de exportación" value={outDir} onChange={(e) => setOutDir(e.target.value)} placeholder="C:\Exports\Proyecto" /></label>
        <button className="btn" disabled={!outDir || !segs.length} onClick={() => act.mutate(async () => setReport(JSON.stringify(await post(`/projects/${projectId}/export`, { output_dir: outDir }), null, 2)))}>Exportar WAV + SRT</button>
      </section>
      {report && <pre className="card text-xs overflow-auto max-h-64">{report}</pre>}
    </div>);
}
