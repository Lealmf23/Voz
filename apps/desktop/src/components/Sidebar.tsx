import { Page, useApp } from "../store";

const items: [Page, string][] = [["dashboard", "Dashboard"], ["projects", "Proyectos"], ["studio", "Estudio de doblaje"],
  ["voices", "Mis voces"], ["glossary", "Glosarios"], ["models", "Modelos de IA"], ["settings", "Configuración"]];

export function Sidebar() {
  const { page, go } = useApp();
  return (
    <nav className="w-56 bg-surface border-r border-line p-3 flex flex-col gap-1 shrink-0">
      <div className="px-2 py-3 font-semibold tracking-widest text-primary2 text-sm">LEAL DUB STUDIO</div>
      {items.map(([k, label]) => (
        <button key={k} onClick={() => go(k)} aria-current={page === k}
          className={`text-left px-3 py-2 rounded-md text-sm ${page === k ? "bg-primary/20 text-ink" : "text-mute hover:text-ink"}`}>{label}</button>))}
    </nav>);
}
