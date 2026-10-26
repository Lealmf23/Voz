import { useQuery } from "@tanstack/react-query";
import { api, Hardware, Project } from "../api";
import { ErrorNote, Title } from "../components/ui";
import { useApp } from "../store";

export default function Dashboard() {
  const { go, open } = useApp();
  const hw = useQuery({ queryKey: ["hw"], queryFn: () => api<Hardware>("/hardware") });
  const pr = useQuery({ queryKey: ["projects"], queryFn: () => api<Project[]>("/projects") });
  return (
    <div>
      <Title>Dashboard</Title>
      <ErrorNote error={hw.error} />
      <div className="grid grid-cols-2 gap-4">
        <section className="card"><h2 className="font-medium mb-2">Hardware</h2>
          {hw.isLoading ? <p className="text-mute">Cargando…</p> : hw.data && (
            <ul className="text-sm space-y-1">
              <li>GPU: {hw.data.gpu ?? "no detectada"}</li>
              <li>CUDA: {hw.data.cuda ? "sí" : "no"} · PyTorch: {hw.data.torch ?? "no instalado"}</li>
              <li>VRAM: {hw.data.vram_total_mb ? `${hw.data.vram_free_mb} / ${hw.data.vram_total_mb} MB libres` : "—"}</li>
              <li>sm_120 (Blackwell): {hw.data.blackwell_ok == null ? "sin comprobar" : hw.data.blackwell_ok ? "OK" : "falta (usa wheel cu128)"}</li>
              <li className={hw.data.ffmpeg ? "" : "text-err"}>FFmpeg: {hw.data.ffmpeg ? "encontrado" : "NO encontrado"}</li>
              {hw.data.note && <li className="text-warn">{hw.data.note}</li>}
            </ul>)}
        </section>
        <section className="card"><h2 className="font-medium mb-2">Proyectos recientes</h2>
          {pr.data?.length === 0 && <p className="text-mute text-sm">Aún no hay proyectos.</p>}
          {pr.data?.slice(0, 5).map((p) => <button key={p.id} className="block text-left text-sm text-primary2 hover:underline" onClick={() => open(p.id)}>{p.name}</button>)}
          <button className="btn mt-3" onClick={() => go("projects")}>Nuevo proyecto</button>
        </section>
      </div>
    </div>);
}
