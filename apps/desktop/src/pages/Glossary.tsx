import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, del, post, Term } from "../api";
import { ErrorNote, Title } from "../components/ui";

export default function Glossary() {
  const qc = useQueryClient();
  const empty = { term: "", en: "", pt: "", do_not_translate: false, priority: 0 };
  const [f, setF] = useState(empty);
  const list = useQuery({ queryKey: ["glossary"], queryFn: () => api<Term[]>("/glossary") });
  const add = useMutation({ mutationFn: () => post("/glossary", { ...f, en: f.en || null, pt: f.pt || null }), onSuccess: () => { setF(empty); void qc.invalidateQueries({ queryKey: ["glossary"] }); } });
  const remove = useMutation({ mutationFn: (id: number) => del(`/glossary/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["glossary"] }) });
  return (
    <div className="max-w-3xl">
      <Title>Glosarios</Title>
      <form className="card grid grid-cols-6 gap-2 items-end mb-4" onSubmit={(e) => { e.preventDefault(); if (f.term.trim()) add.mutate(); }}>
        <input className="input col-span-2" aria-label="Término" placeholder="Término original" value={f.term} onChange={(e) => setF({ ...f, term: e.target.value })} />
        <input className="input" aria-label="Inglés" placeholder="Inglés" value={f.en} disabled={f.do_not_translate} onChange={(e) => setF({ ...f, en: e.target.value })} />
        <input className="input" aria-label="Portugués" placeholder="Portugués" value={f.pt} disabled={f.do_not_translate} onChange={(e) => setF({ ...f, pt: e.target.value })} />
        <label className="text-xs"><input type="checkbox" checked={f.do_not_translate} onChange={(e) => setF({ ...f, do_not_translate: e.target.checked })} /> No traducir</label>
        <button className="btn" disabled={!f.term.trim()}>Añadir</button>
      </form>
      <ErrorNote error={add.error ?? remove.error} />
      <table className="w-full text-sm card"><thead className="text-mute text-xs text-left"><tr><th>Término</th><th>EN</th><th>PT</th><th /><th /></tr></thead>
        <tbody>{list.data?.map((t) => <tr key={t.id} className="border-t border-line"><td className="py-1">{t.term}</td><td>{t.do_not_translate ? "—" : t.en}</td><td>{t.do_not_translate ? "—" : t.pt}</td><td className="text-xs text-mute">{t.do_not_translate && "no traducir"}</td>
          <td><button className="btn-ghost" onClick={() => remove.mutate(t.id)}>Quitar</button></td></tr>)}</tbody></table>
    </div>);
}
