import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import { api, ApiError, Segment } from "../api";
import { SegmentRow } from "../components/SegmentRow";
import Glossary from "../pages/Glossary";
import Voices from "../pages/Voices";

const seg: Segment = { id: 7, start: 1, end: 3.5, original_text: "Miren el tráfico", confidence: null, translations: { "en-US": "Look at the traffic" },
  audio: { "en-US": { duration: 2.6, ratio: 1.04, engine: "mock-tts", mock: true, warnings: ["Aceleración notable x1.20"] } }, params: {}, prosody: null, locked: false };

const wrap = (ui: React.ReactElement) => render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{ui}</QueryClientProvider>);

beforeEach(() => { vi.stubGlobal("fetch", vi.fn(async () => new Response("[]", { status: 200 }))); });
afterEach(() => vi.unstubAllGlobals());

test("api: error de conexión con mensaje claro", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fail"); }));
  await expect(api("/x")).rejects.toMatchObject({ status: 0, message: expect.stringContaining("backend local") });
});

test("api: propaga el detalle del backend", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "El archivo no existe" }), { status: 400 })));
  await expect(api("/x")).rejects.toEqual(expect.any(ApiError));
  await expect(api("/x")).rejects.toThrow("El archivo no existe");
});

test("SegmentRow: guarda la traducción editada al perder el foco", () => {
  const onSave = vi.fn();
  render(<table><tbody><SegmentRow seg={seg} onSave={onSave} onRegenerate={vi.fn()} /></tbody></table>);
  const box = screen.getByLabelText("en-US 7");
  fireEvent.change(box, { target: { value: "Check out this traffic" } });
  fireEvent.blur(box);
  expect(onSave).toHaveBeenCalledWith(7, { translations: { "en-US": "Check out this traffic" } });
});

test("SegmentRow: muestra advertencias y deshabilita al bloquear", () => {
  const onRegen = vi.fn();
  const { rerender } = render(<table><tbody><SegmentRow seg={seg} onSave={vi.fn()} onRegenerate={onRegen} /></tbody></table>);
  expect(screen.getByText(/Aceleración notable/)).toBeInTheDocument();
  fireEvent.click(screen.getAllByText("Regenerar")[0]);
  expect(onRegen).toHaveBeenCalledWith(7, "en-US");
  rerender(<table><tbody><SegmentRow seg={{ ...seg, locked: true }} onSave={vi.fn()} onRegenerate={onRegen} /></tbody></table>);
  expect(screen.getByLabelText("en-US 7")).toBeDisabled();
});

test("Voices: exige consentimiento antes de crear el perfil", () => {
  wrap(<Voices />);
  fireEvent.change(screen.getByLabelText("Nombre del perfil"), { target: { value: "Yo" } });
  fireEvent.change(screen.getByLabelText("Muestras"), { target: { value: "C:\\voz.wav" } });
  const btn = screen.getByText("Crear perfil");
  expect(btn).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(btn).toBeEnabled();
});

test("Glossary: 'No traducir' desactiva los campos de traducción", async () => {
  wrap(<Glossary />);
  fireEvent.click(screen.getByLabelText(/No traducir/));
  await waitFor(() => expect(screen.getByLabelText("Inglés")).toBeDisabled());
});

import { ClaudeSection } from "../pages/Settings";

test("Claude: no se puede activar sin consentimiento y la clave se envía una sola vez", async () => {
  const calls: { url: string; body?: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined });
    return new Response(JSON.stringify({ enabled: false, consent_at: null, model: "claude-sonnet-5-5", key_set: false }), { status: 200 });
  }));
  wrap(<ClaudeSection />);
  const btn = await screen.findByText("Activar");
  fireEvent.change(screen.getByLabelText("Clave API de Anthropic"), { target: { value: "sk-ant-x" } });
  expect(btn).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  expect(btn).toBeEnabled();
  fireEvent.click(btn);
  await waitFor(() => expect(calls.some((c) => c.body?.includes("consent_confirmed\":true"))).toBe(true));
  expect(screen.getByText(/EN LÍNEA/)).toBeInTheDocument();
});
