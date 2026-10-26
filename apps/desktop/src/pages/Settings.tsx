import { useQuery } from "@tanstack/react-query";
import { api, API, Hardware } from "../api";
import { Title } from "../components/ui";

export default function Settings() {
  const hw = useQuery({ queryKey: ["hw"], queryFn: () => api<Hardware>("/hardware") });
  return (
    <div className="max-w-xl space-y-4">
      <Title>Configuración</Title>
      <section className="card text-sm space-y-1"><h2 className="font-medium">Privacidad</h2>
        <p>Procesamiento 100 % local. Backend en <code>{API}</code> (solo localhost). Los servicios externos no existen todavía y, cuando se añadan, estarán desactivados por defecto.</p></section>
      <section className="card text-sm space-y-1"><h2 className="font-medium">Hardware</h2>
        <p>Dispositivo detectado: {hw.data?.gpu ?? "CPU"}.</p>
        <p className="text-warn">Pendiente: selección manual CPU/GPU, límite de memoria, lotes, directorios y formato de audio configurables. Hoy se controlan con variables de entorno (<code>LEAL_DATA_DIR</code>).</p></section>
    </div>);
}
