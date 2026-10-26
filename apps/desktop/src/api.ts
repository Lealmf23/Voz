export const API = (import.meta.env?.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8765/api";

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API + path, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  } catch {
    throw new ApiError(0, "No se puede conectar con el backend local (¿está en ejecución?)");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { const j = await res.json(); detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail); } catch { /* sin cuerpo */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? (undefined as T) : res.json();
}

export const post = <T,>(p: string, body?: unknown) => api<T>(p, { method: "POST", body: body ? JSON.stringify(body) : undefined });
export const patch = <T,>(p: string, body: unknown) => api<T>(p, { method: "PATCH", body: JSON.stringify(body) });
export const del = (p: string) => api<void>(p, { method: "DELETE" });

export type Lang = "en-US" | "pt-BR";
export interface AudioInfo { duration: number; sample_rate: number; channels: number; peak_db: number; rms_db: number; clipping: boolean; speech_regions: number }
export interface Project { id: number; name: string; created_at: string; audio_info: AudioInfo | null; voice_profile_id: number | null; target_languages: Lang[]; segments?: Segment[]; history?: { at: string; action: string }[] }
export interface GenAudio { duration: number; ratio: number; engine: string; mock: boolean; warnings: string[] }
export interface Segment { id: number; start: number; end: number; original_text: string; confidence: number | null; translations: Partial<Record<Lang, string>>; audio: Partial<Record<Lang, GenAudio>>; params: { speed?: number; intensity?: number; emotion?: number; seed?: number }; prosody: Record<string, number | number[] | null> | null; locked: boolean }
export interface Job { id: string; kind: string; status: string; stage: string; progress: number; done: number; total: number; error: string | null; elapsed: number; eta: number | null }
export interface Voice { id: number; name: string; samples: number; quality: Record<string, { duration: number; warnings: string[] }> }
export interface EngineInfo { id: string; kind: string; name: string; mock: boolean; languages: string[]; license: string; notes: string }
export interface Hardware { cuda: boolean; gpu: string | null; vram_total_mb: number | null; vram_free_mb: number | null; torch: string | null; blackwell_ok: boolean | null; ffmpeg: boolean; note?: string }
export interface Term { id: number; term: string; en: string | null; pt: string | null; do_not_translate: boolean; priority: number }
