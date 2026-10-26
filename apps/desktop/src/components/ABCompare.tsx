import { useState } from "react";
import { API, Segment } from "../api";

const CRITERIA = ["Semejanza vocal", "Naturalidad", "Pronunciación", "Expresividad", "Sincronización"] as const;
type Ratings = Record<string, number>;

function load(key: string): Ratings {
  try { return JSON.parse(localStorage.getItem(key) ?? "{}"); } catch { return {}; }
}

/** Compara original (A), inglés (B) y portugués (C) de un segmento y guarda notas manuales (sin métricas automáticas). */
export function ABCompare({ projectId, seg }: { projectId: number; seg: Segment }) {
  const [playing, setPlaying] = useState<string | null>(null);
  const [ratings, setRatings] = useState<Ratings>(() => load(`ab-${projectId}-${seg.id}`));
  const key = `ab-${projectId}-${seg.id}`;
  const stopAll = () => document.querySelectorAll("audio").forEach((a) => a.pause());

  const playOriginal = () => {
    stopAll();
    const a = new Audio(`${API}/projects/${projectId}/audio`);
    a.currentTime = seg.start; setPlaying("A");
    void a.play();
    const t = setInterval(() => { if (a.currentTime >= seg.end) { a.pause(); clearInterval(t); setPlaying(null); } }, 50);
  };
  const playDub = (lang: "en-US" | "pt-BR", id: string) => {
    stopAll(); setPlaying(id);
    const a = new Audio(`${API}/segments/${seg.id}/audio/${lang}?t=${seg.audio[lang]?.duration}`);
    a.onended = () => setPlaying(null); void a.play();
  };
  const rate = (c: string, v: number) => {
    const next = { ...ratings, [c]: v }; setRatings(next);
    try { localStorage.setItem(key, JSON.stringify(next)); } catch { /* almacenamiento no disponible */ }
  };
  return (
    <div className="card">
      <h3 className="font-medium mb-2">Comparación A/B/C — segmento {seg.id}</h3>
      <div className="flex gap-2 mb-3">
        <button className={playing === "A" ? "btn" : "btn-ghost"} onClick={playOriginal}>A · Original (es)</button>
        <button className={playing === "B" ? "btn" : "btn-ghost"} disabled={!seg.audio["en-US"]} onClick={() => playDub("en-US", "B")}>B · Inglés</button>
        <button className={playing === "C" ? "btn" : "btn-ghost"} disabled={!seg.audio["pt-BR"]} onClick={() => playDub("pt-BR", "C")}>C · Portugués</button>
      </div>
      <div className="space-y-1">
        {CRITERIA.map((c) => (
          <div key={c} className="flex items-center gap-2 text-sm"><span className="w-36 text-mute">{c}</span>
            {[1, 2, 3, 4, 5].map((v) => <button key={v} aria-label={`${c} ${v}`} onClick={() => rate(c, v)}
              className={`w-7 h-7 rounded ${ratings[c] === v ? "bg-primary" : "bg-bg border border-line"}`}>{v}</button>)}</div>))}
      </div>
      <p className="text-xs text-mute mt-2">Valoración manual (1–5). Se guarda solo en este equipo; no hay métricas automáticas.</p>
    </div>);
}
