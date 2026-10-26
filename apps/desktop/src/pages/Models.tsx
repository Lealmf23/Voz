import { useQuery } from "@tanstack/react-query";
import { api, EngineInfo } from "../api";
import { ErrorNote, Mock, Title } from "../components/ui";
import { useApp } from "../store";

const KIND: Record<string, string> = { transcription: "Transcripción", translation: "Traducción", tts: "TTS / clonación" };

export default function Models() {
  const { engines, setEngine } = useApp();
  const list = useQuery({ queryKey: ["engines"], queryFn: () => api<EngineInfo[]>("/engines") });
  return (
    <div>
      <Title>Modelos de IA</Title>
      <ErrorNote error={list.error} />
      <p className="text-sm text-warn mb-3">Instalar, verificar, actualizar y eliminar modelos aún es <b>pendiente</b>: por ahora se instalan a mano (docs/INSTALLATION.md) y se descargan al primer uso.</p>
      <table className="w-full text-sm card"><thead className="text-mute text-xs text-left"><tr><th>Función</th><th>Modelo</th><th>Idiomas</th><th>Licencia</th><th>Notas</th><th>Predeterminado</th></tr></thead>
        <tbody>{list.data?.map((e) => (
          <tr key={e.kind + e.id} className="border-t border-line align-top">
            <td className="py-2">{KIND[e.kind]}</td><td>{e.name}<Mock on={e.mock} /></td><td>{e.languages.join(", ")}</td><td>{e.license || "—"}</td><td className="text-xs text-mute">{e.notes}</td>
            <td><input type="radio" aria-label={`predeterminado ${e.id}`} name={e.kind} checked={engines[e.kind] === e.id} onChange={() => setEngine(e.kind, e.id)} /></td></tr>))}</tbody></table>
      <p className="text-xs text-mute mt-3">Si un motor real no está instalado, la tarea terminará en error con el motivo; no se sustituye por uno simulado.</p>
    </div>);
}
