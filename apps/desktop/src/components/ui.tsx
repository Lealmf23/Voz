import { ApiError, Job, post } from "../api";
import { useApp } from "../store";

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <div role="alert" className="text-err text-sm my-2">{error instanceof ApiError || error instanceof Error ? error.message : String(error)}</div>;
}

export function Mock({ on }: { on: boolean }) {
  return on ? <span className="text-warn text-xs border border-warn rounded px-1 ml-1" title="Motor simulado: no es IA real">SIMULADO</span> : null;
}

export function Title({ children }: { children: React.ReactNode }) {
  return <h1 className="text-xl font-semibold mb-4">{children}</h1>;
}

/** Lanza una tarea en la cola y la registra para mostrar su progreso. */
export function useRunJob() {
  const setJob = useApp((s) => s.setJob);
  return async (path: string, body?: unknown) => { const j = await post<Job>(path, body); setJob(j); return j; };
}

export const fmtTime = (t: number) => `${Math.floor(t / 60)}:${(t % 60).toFixed(2).padStart(5, "0")}`;
