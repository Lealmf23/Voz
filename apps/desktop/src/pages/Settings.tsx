import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, API, del, Hardware } from "../api";
import { ErrorNote, Title } from "../components/ui";

interface ClaudeStatus { enabled: boolean; consent_at: string | null; model: string; key_set: boolean }

export function ClaudeSection() {
  const qc = useQueryClient();
  const st = useQuery({ queryKey: ["claude"], queryFn: () => api<ClaudeStatus>("/external/claude") });
  const [consent, setConsent] = useState(false);
  const [key, setKey] = useState("");
  const save = useMutation({
    mutationFn: (enabled: boolean) => api<ClaudeStatus>("/external/claude", { method: "PUT", body: JSON.stringify({ enabled, consent_confirmed: consent, api_key: key || undefined }) }),
    onSuccess: () => { setKey(""); setConsent(false); void qc.invalidateQueries({ queryKey: ["claude"] }); } });
  const rmKey = useMutation({ mutationFn: () => del("/external/claude/key"), onSuccess: () => qc.invalidateQueries({ queryKey: ["claude"] }) });
  const on = st.data?.enabled ?? false;
  return (
    <section className="card text-sm space-y-3">
      <h2 className="font-medium">Servicios externos · Traducción con Claude <span className="text-warn text-xs border border-warn rounded px-1">EN LÍNEA</span></h2>
      <p className="text-mute">Opcional y desactivado por defecto. Si lo activas, el <b>texto</b> de los segmentos (y el de sus vecinos como contexto) se envía a la API de Anthropic para traducirlo. No se envía audio. Tiene coste por uso en tu cuenta de Anthropic.</p>
      <ErrorNote error={st.error ?? save.error ?? rmKey.error} />
      <p>Estado: <b className={on ? "text-ok" : ""}>{on ? "activado" : "desactivado"}</b> · modelo {st.data?.model} · clave {st.data?.key_set ? "guardada en el almacén seguro del sistema" : "no configurada"}</p>
      {!on && (
        <>
          <label className="flex gap-2"><input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
            Entiendo y acepto que el texto de mis narraciones se envíe a Anthropic para traducirlo.</label>
          <input className="input" type="password" autoComplete="off" aria-label="Clave API de Anthropic" placeholder={st.data?.key_set ? "Clave ya guardada (déjalo vacío para conservarla)" : "sk-ant-…"} value={key} onChange={(e) => setKey(e.target.value)} />
          <button className="btn" disabled={!consent || (!key && !st.data?.key_set) || save.isPending} onClick={() => save.mutate(true)}>Activar</button>
        </>)}
      {on && <button className="btn-ghost" onClick={() => save.mutate(false)}>Desactivar</button>}
      {st.data?.key_set && <button className="btn-ghost ml-2" onClick={() => rmKey.mutate()}>Borrar clave</button>}
    </section>);
}

export default function Settings() {
  const hw = useQuery({ queryKey: ["hw"], queryFn: () => api<Hardware>("/hardware") });
  return (
    <div className="max-w-xl space-y-4">
      <Title>Configuración</Title>
      <section className="card text-sm space-y-1"><h2 className="font-medium">Privacidad</h2>
        <p>Por defecto todo es 100 % local. Backend en <code>{API}</code> (solo localhost). La única excepción es el servicio externo de abajo, desactivado de fábrica.</p></section>
      <ClaudeSection />
      <section className="card text-sm space-y-1"><h2 className="font-medium">Hardware</h2>
        <p>Dispositivo detectado: {hw.data?.gpu ?? "CPU"}.</p>
        <p className="text-warn">Pendiente: selección manual CPU/GPU, límite de memoria, lotes, directorios y formato de audio configurables. Hoy se controlan con variables de entorno (<code>LEAL_DATA_DIR</code>).</p></section>
    </div>);
}
