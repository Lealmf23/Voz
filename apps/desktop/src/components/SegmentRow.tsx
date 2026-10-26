import { useState } from "react";
import { API, Lang, Segment } from "../api";
import { fmtTime } from "./ui";

const LANGS: Lang[] = ["en-US", "pt-BR"];

export interface RowProps {
  seg: Segment;
  onSave: (id: number, patch: Record<string, unknown>) => void;
  onRegenerate: (id: number, lang: Lang) => void;
  busy?: boolean;
}

function play(url: string) { const a = new Audio(url); void a.play(); }

export function SegmentRow({ seg, onSave, onRegenerate, busy }: RowProps) {
  const [text, setText] = useState(seg.original_text);
  const [tr, setTr] = useState<Record<string, string>>({ "en-US": seg.translations["en-US"] ?? "", "pt-BR": seg.translations["pt-BR"] ?? "" });
  const [open, setOpen] = useState(false);
  const dur = seg.end - seg.start;
  const status = (l: Lang) => { const a = seg.audio[l]; return !a ? "sin audio" : a.warnings.length ? "advertencia" : "listo"; };
  return (
    <>
      <tr className="border-t border-line align-top">
        <td className="p-2 text-xs text-mute whitespace-nowrap">{fmtTime(seg.start)}<br />{dur.toFixed(2)} s</td>
        <td className="p-2 w-1/4">
          <textarea aria-label={`original ${seg.id}`} className="input" rows={2} value={text} disabled={seg.locked}
            onChange={(e) => setText(e.target.value)} onBlur={() => text !== seg.original_text && onSave(seg.id, { original_text: text })} />
          {seg.confidence != null && <div className="text-xs text-mute">confianza {(seg.confidence * 100).toFixed(0)}%</div>}
        </td>
        {LANGS.map((l) => (
          <td key={l} className="p-2 w-1/4">
            <textarea aria-label={`${l} ${seg.id}`} className="input" rows={2} value={tr[l]} disabled={seg.locked}
              onChange={(e) => setTr({ ...tr, [l]: e.target.value })}
              onBlur={() => tr[l] !== (seg.translations[l] ?? "") && onSave(seg.id, { translations: { [l]: tr[l] } })} />
            <div className="flex gap-1 mt-1 items-center">
              <button className="btn-ghost" disabled={!seg.audio[l]} onClick={() => play(`${API}/segments/${seg.id}/audio/${l}?t=${seg.audio[l]?.duration}`)}>▶ {l}</button>
              <button className="btn-ghost" disabled={busy || seg.locked || !tr[l]} onClick={() => onRegenerate(seg.id, l)}>Regenerar</button>
              <span className={`text-xs ${status(l) === "advertencia" ? "text-warn" : "text-mute"}`}>{status(l)}</span>
            </div>
            {seg.audio[l]?.warnings.map((w) => <div key={w} className="text-xs text-warn">{w}</div>)}
          </td>))}
        <td className="p-2 whitespace-nowrap">
          <button className="btn-ghost" onClick={() => setOpen(!open)} aria-expanded={open}>Ajustes</button>{" "}
          <label className="text-xs text-mute"><input type="checkbox" checked={seg.locked} onChange={(e) => onSave(seg.id, { locked: e.target.checked })} /> aprobado</label>
        </td>
      </tr>
      {open && (
        <tr className="bg-surface/50"><td colSpan={5} className="p-3 text-sm">
          <div className="flex gap-6 flex-wrap items-end">
            {(["speed", "intensity", "emotion"] as const).map((k) => (
              <label key={k} className="text-xs text-mute">{{ speed: "Velocidad", intensity: "Intensidad", emotion: "Exageración/emoción" }[k]}
                <input type="number" step="0.1" min="0" max="2" className="input w-24" defaultValue={seg.params[k] ?? (k === "emotion" ? 0.5 : 1)}
                  onBlur={(e) => onSave(seg.id, { params: { ...seg.params, [k]: Number(e.target.value) } })} /></label>))}
            <label className="text-xs text-mute">Semilla<input type="number" className="input w-28" defaultValue={seg.params.seed ?? ""}
              onBlur={(e) => onSave(seg.id, { params: { ...seg.params, seed: e.target.value === "" ? undefined : Number(e.target.value) } })} /></label>
            {seg.prosody && <div className="text-xs text-mute">Medido: F0 {Number(seg.prosody.f0_median_hz)?.toFixed?.(0) ?? "—"} Hz · RMS {Number(seg.prosody.rms_mean_db)?.toFixed?.(1) ?? "—"} dB · pausas {seg.prosody.pause_ratio != null ? (Number(seg.prosody.pause_ratio) * 100).toFixed(0) + "%" : "—"}</div>}
          </div>
          <p className="text-xs text-mute mt-2">Estos controles solo tienen efecto si el motor TTS los admite (Chatterbox usa la exageración y la semilla; la velocidad se aplica con time-stretch).</p>
        </td></tr>)}
    </>);
}
