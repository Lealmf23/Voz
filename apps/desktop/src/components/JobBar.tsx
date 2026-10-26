import { useQuery } from "@tanstack/react-query";
import { api, Job, post } from "../api";
import { useApp } from "../store";

const label: Record<string, string> = { pending: "Pendiente", preparing: "Preparando", processing: "Procesando", completed: "Completado", cancelled: "Cancelado", error: "Error" };
const fmt = (s: number | null) => (s == null ? "—" : `${Math.round(s)} s`);

function Row({ job }: { job: Job }) {
  const setJob = useApp((s) => s.setJob);
  const active = job.status === "pending" || job.status === "processing";
  useQuery({ queryKey: ["job", job.id], enabled: active, refetchInterval: 700,
    queryFn: async () => { const j = await api<Job>(`/jobs/${job.id}`); setJob(j); return j; } });
  return (
    <div className="text-xs border-t border-line pt-2 mt-2">
      <div className="flex justify-between"><span>{job.kind} · {job.stage || "en cola"}</span>
        <span className={job.status === "error" ? "text-err" : job.status === "completed" ? "text-ok" : "text-mute"}>{label[job.status]}</span></div>
      <div className="h-1.5 bg-bg rounded mt-1"><div className="h-full bg-primary rounded" style={{ width: `${job.progress * 100}%` }} /></div>
      <div className="text-mute mt-1">{job.done}/{job.total} · {fmt(job.elapsed)} · ETA {fmt(job.eta)}</div>
      {job.error && <div className="text-err mt-1">{job.error}</div>}
      {active && <button className="btn-ghost mt-1" onClick={() => post(`/jobs/${job.id}/cancel`)}>Cancelar</button>}
    </div>);
}

export function JobBar() {
  const jobs = Object.values(useApp((s) => s.jobs)).slice(-4).reverse();
  if (!jobs.length) return null;
  return <aside className="w-64 bg-surface border-l border-line p-3 shrink-0 overflow-auto"><div className="text-sm font-medium">Tareas</div>{jobs.map((j) => <Row key={j.id} job={j} />)}</aside>;
}
