import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, post, Project } from "../api";
import { ErrorNote, Title } from "../components/ui";
import { useApp } from "../store";

export default function Projects() {
  const qc = useQueryClient();
  const open = useApp((s) => s.open);
  const [name, setName] = useState("");
  const list = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/projects") });
  const create = useMutation({ mutationFn: () => post<Project>("/projects", { name }), onSuccess: (p) => { setName(""); void qc.invalidateQueries({ queryKey: ["projects"] }); open(p.id); } });
  const remove = useMutation({ mutationFn: (id: number) => del(`/projects/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }) });
  return (
    <div>
      <Title>Proyectos</Title>
      <form className="flex gap-2 mb-4 max-w-lg" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <input className="input" placeholder="Nombre del proyecto" aria-label="Nombre del proyecto" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" disabled={!name.trim() || create.isPending}>Crear</button>
      </form>
      <ErrorNote error={create.error ?? remove.error ?? list.error} />
      <ul className="space-y-2">
        {list.data?.map((p) => (
          <li key={p.id} className="card flex justify-between items-center">
            <div><div className="font-medium">{p.name}</div>
              <div className="text-xs text-mute">{new Date(p.created_at).toLocaleString()} · {p.audio_info ? `${p.audio_info.duration.toFixed(1)} s` : "sin audio"}</div></div>
            <div className="flex gap-2"><button className="btn" onClick={() => open(p.id)}>Abrir</button>
              <button className="btn-ghost" onClick={() => confirm(`¿Eliminar "${p.name}" y sus archivos generados?`) && remove.mutate(p.id)}>Eliminar</button></div>
          </li>))}
      </ul>
    </div>);
}
