import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, post, Voice } from "../api";
import { ErrorNote, Title } from "../components/ui";

export default function Voices() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [paths, setPaths] = useState("");
  const [consent, setConsent] = useState(false);
  const [refText, setRefText] = useState("");
  const list = useQuery({ queryKey: ["voices"], queryFn: () => api<Voice[]>("/voices") });
  const create = useMutation({
    mutationFn: () => post("/voices", { name, sample_paths: paths.split("\n").map((s) => s.trim()).filter(Boolean), consent_confirmed: consent, reference_text: refText || undefined }),
    onSuccess: () => { setName(""); setPaths(""); setRefText(""); setConsent(false); void qc.invalidateQueries({ queryKey: ["voices"] }); } });
  const remove = useMutation({ mutationFn: (id: number) => del(`/voices/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["voices"] }) });
  const valid = name.trim() && paths.trim() && consent;
  return (
    <div className="max-w-2xl">
      <Title>Mis voces</Title>
      <form className="card space-y-3 mb-6" onSubmit={(e) => { e.preventDefault(); if (valid) create.mutate(); }}>
        <input className="input" aria-label="Nombre del perfil" placeholder="Nombre del perfil" value={name} onChange={(e) => setName(e.target.value)} />
        <textarea className="input" rows={3} aria-label="Muestras" placeholder="Una ruta de audio por línea (WAV/MP3/FLAC/M4A, ≥ 5 s, sin ruido de fondo)" value={paths} onChange={(e) => setPaths(e.target.value)} />
        <textarea className="input" rows={2} aria-label="Texto de la muestra" placeholder="Opcional: lo que se dice en la primera muestra, palabra por palabra (mejora la clonación con Qwen3-TTS)" value={refText} onChange={(e) => setRefText(e.target.value)} />
        <label className="text-sm flex gap-2"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />Confirmo que soy el titular de esta voz o tengo su autorización expresa.</label>
        <button className="btn" disabled={!valid || create.isPending}>Crear perfil</button>
        <ErrorNote error={create.error ?? remove.error} />
      </form>
      <ul className="space-y-2">
        {list.data?.map((v) => (
          <li key={v.id} className="card">
            <div className="flex justify-between"><span className="font-medium">{v.name} <span className="text-mute text-xs">{v.samples} muestra(s)</span></span>
              <button className="btn-ghost" onClick={() => confirm("¿Eliminar este perfil y sus muestras procesadas?") && remove.mutate(v.id)}>Eliminar</button></div>
            {Object.entries(v.quality).map(([f, q]) => <div key={f} className="text-xs text-mute">{f}: {q.duration.toFixed(1)} s {q.warnings.map((w) => <span key={w} className="text-warn"> · {w}</span>)}</div>)}
          </li>))}
      </ul>
      <p className="text-xs text-mute mt-4">Las muestras se convierten a WAV y se guardan localmente. El borrado desde la app no garantiza eliminación física irrecuperable.</p>
    </div>);
}
