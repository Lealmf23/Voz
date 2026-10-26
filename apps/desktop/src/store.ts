import { create } from "zustand";
import type { Job } from "./api";

export type Page = "dashboard" | "projects" | "studio" | "voices" | "glossary" | "models" | "settings";

interface State {
  page: Page; projectId: number | null; jobs: Record<string, Job>; engines: Record<string, string>;
  go: (p: Page) => void; open: (id: number) => void; setJob: (j: Job) => void; setEngine: (kind: string, id: string) => void;
}

export const useApp = create<State>((set) => ({
  page: "dashboard", projectId: null, jobs: {}, engines: { transcription: "faster-whisper", translation: "nllb-200-600m", tts: "chatterbox" },
  go: (page) => set({ page }),
  open: (projectId) => set({ projectId, page: "studio" }),
  setJob: (j) => set((s) => ({ jobs: { ...s.jobs, [j.id]: j } })),
  setEngine: (kind, id) => set((s) => ({ engines: { ...s.engines, [kind]: id } })),
}));
