import * as T from "three";
const $ = id => document.getElementById(id);
const el = (tag, attrs = {}, ...kids) => { const e = document.createElement(tag); for (const k in attrs) { if (k === "class") e.className = attrs[k]; else if (k.startsWith("on")) e[k] = attrs[k]; else if (k === "html") e.innerHTML = attrs[k]; else if (attrs[k] !== false && attrs[k] != null) e.setAttribute(k, attrs[k]); } kids.flat().forEach(c => e.append(c)); return e; };

// ================= estado =================
const KINDS = { win: "Ventana", door: "Puerta", bal: "Balcón", shop: "Vidriera" };
const CATKIND = { win: "window", door: "door", bal: "balcony" };
const KCOL = { win: 0x4e9bff, door: 0xc08040, bal: 0x4cc38a, shop: 0x38d0e0 };
const DEFC = { facade_color: [.62, .60, .56], mat_b_color: [.30, .073, .046], mat_c_color: [.034, .034, .038], mat_d_color: [.45, .133, .027], trim_color: [.81, .77, .68], roof_color: [.15, .115, .097], door_color: [.066, .039, .021] };
const l2s = c => c <= .0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - .055, s2l = c => c <= .04045 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4);
const toHex = lin => "#" + lin.map(c => Math.round(Math.max(0, Math.min(1, l2s(c))) * 255).toString(16).padStart(2, "0")).join("");
const fromHex = h => [1, 3, 5].map(i => s2l(parseInt(h.slice(i, i + 2), 16) / 255));
const colorOf = k => S.props[k] || DEFC[k];
const matHex = m => toHex(colorOf(["facade_color", "facade_color", "mat_b_color", "mat_c_color", "mat_d_color"][m]));
const newEdge = () => ({ bulge: 0, mat: 0, rot: 0, ws: "", ds: "", bs: "", ov: {}, add: [] });
const NOSEL = () => ({ edge: null, sec: 0, slot: null, vert: null });
const ES = (i, sec = 0) => sec ? ((E()[i].sx = E()[i].sx || {})[sec] = E()[i].sx[sec] || { ov: {}, add: [] }) : E()[i];
const Wk = (i, sec = 0) => R && R.walls.find(w => w.edge === i && w.sec === sec);
let S = { verts: [], edges: [], setbacks: [], floors: 4, floor_h: 3, roof: "FLAT_SLAB", props: { balconies: "alternate" } };
let closed = false, R = null, CAT = null, SCH = null, online = false;
let sel = NOSEL();
let tool = "select", brush = { win: null, door: null, bal: null };
let snapG = 1, showRoof = true, viewMode = "both";
const hist = { u: [], r: [] };
const snap = () => JSON.stringify({ S, closed });
const restore = s => { const o = JSON.parse(s); S = o.S; S.setbacks = S.setbacks || []; closed = o.closed; };
function commit(rebuild = true) { hist.u.push(snap()); if (hist.u.length > 100) hist.u.shift(); hist.r = []; changed(rebuild); }
function undo() { if (hist.u.length < 2) return; hist.r.push(hist.u.pop()); restore(hist.u[hist.u.length - 1]); sel = NOSEL(); changed(true, true); }
function redo() { if (!hist.r.length) return; const s = hist.r.pop(); hist.u.push(s); restore(s); sel = NOSEL(); changed(true, true); }
function toast(msg, ms = 3500) { const t = $("toast"); t.textContent = msg; t.style.display = "block"; clearTimeout(toast.h); toast.h = setTimeout(() => t.style.display = "none", ms); }
const H = () => S.floors * S.floor_h;

// ================= geometría de paredes =================
function area(v) { let a = 0; for (let i = 0; i < v.length; i++) { const j = (i + 1) % v.length; a += v[i][0] * v[j][1] - v[j][0] * v[i][1]; } return a / 2; }
function ensureCCW() { if (S.verts.length >= 3 && area(S.verts) < 0) { S.verts.reverse(); const e = S.edges.reverse(); S.edges = e.slice(1).concat(e.slice(0, 1)); S.edges.forEach(x => x.bulge = -x.bulge); (S.setbacks || []).forEach(sb => { const r = sb.ins.slice().reverse(); sb.ins = r.slice(1).concat(r.slice(0, 1)); }); } }
function facetsOf(i, sec = 0) { const w = Wk(i, sec); if (w && w.facets) return w.facets; const n = S.verts.length; return [[S.verts[i], S.verts[(i + 1) % n]]]; }
function wallPath(i, sec = 0) { const w0 = Wk(i, sec); let c = w0 ? (w0.amin || 0) : 0; return facetsOf(i, sec).map(f => { const dx = f[1][0] - f[0][0], dy = f[1][1] - f[0][1], L = Math.hypot(dx, dy) || 1e-9; const o = { p: f[0], dx: dx / L, dy: dy / L, L, c0: c }; c += L; return o; }); }
function atS(i, s, path = wallPath(i), sec = 0) { let f = path.find(q => s <= q.c0 + q.L + 1e-9) || path[path.length - 1]; const t = Math.max(0, Math.min(f.L, s - f.c0)); return { x: f.p[0] + f.dx * t, y: f.p[1] + f.dy * t, nx: f.dy, ny: -f.dx, f }; }
function wallLen(i) { return wallPath(i).reduce((a, f) => a + f.L, 0); }
const E = () => S.edges;
function slotsOf(i, sec = 0) { const w = Wk(i, sec); return w ? w.slots : []; }

// ================= motor =================
let building = false, pending = false, buildT = 0, builds = 0;
function fixSetbacks() { const n = S.verts.length; S.setbacks = (S.setbacks || []).filter(sb => sb.from >= 1 && sb.from < S.floors); S.setbacks.forEach(sb => { sb.ins = (sb.ins || []).slice(0, n); while (sb.ins.length < n) sb.ins.push(0); }); }
function recipe() { const props = { ...S.props }; return { verts: S.verts, edges: S.edges, setbacks: S.setbacks, floors: S.floors, floor_h: S.floor_h, roof: S.roof, props }; }
function requestBuild() { clearTimeout(buildT); buildT = setTimeout(doBuild, 120); }
async function doBuild() {
  if (!closed || S.verts.length < 3) return;
  if (building) { pending = true; return; }
  building = true; $("spin").classList.add("on");
  try {
    const r = await fetch("/build", { method: "POST", body: JSON.stringify(recipe()) });
    const j = await r.json(); if (j.error) throw new Error(j.error);
    R = j; builds++; setOnline(true); showReal(); drawAll(); sidebar(); stats();
  } catch (e) { if (e instanceof TypeError) setOnline(false); else toast("El motor devolvió un error: " + e.message, 6000); }
  building = false; $("spin").classList.remove("on");
  if (pending) { pending = false; doBuild(); }
}
function setOnline(v) { online = v; $("dot").className = "dot " + (v ? "ok" : ""); $("engtxt").textContent = v ? "Motor Blender conectado" : "Motor sin conexión"; }
async function loadCatalog() { try { CAT = await (await fetch("/catalog")).json(); SCH = await (await fetch("/schema")).json(); setOnline(true); } catch { CAT = { window: [], door: [], balcony: [], roofs: [], mats: [] }; setOnline(false); } sidebar(); }

// ================= cambios =================
function changed(rebuild = true, redraw = true) {
  if (!closed || S.verts.length < 3 || (R && !R.walls.every(w => w.edge < S.verts.length) )) R = null;
  fixSetbacks();
  if (rebuild) { if (closed) requestBuild(); else { R = null; showReal(); } }
  try { localStorage.setItem("cs2ed", snap()); } catch { }
  drawAll(); sidebar(); stats(); hintText(); welcome(); guide();
}
function stats() {
  const b = $("bot"); b.innerHTML = "";
  let w = 0, d = 0;
  if (S.verts.length) { const xs = S.verts.map(p => p[0]), ys = S.verts.map(p => p[1]); w = Math.max(...xs) - Math.min(...xs); d = Math.max(...ys) - Math.min(...ys); }
  const cells = (Math.ceil(w / 8 - 1e-6) || 0) + "×" + (Math.ceil(d / 8 - 1e-6) || 0);
  b.append(el("span", { html: `Tamaño <b>${w.toFixed(1)} × ${d.toFixed(1)} m</b> · Altura <b>${H().toFixed(1)} m</b>` }));
  b.append(el("span", { html: `Lote CS2 <b>${cells}</b> celdas` }));
  if (R) { const pct = Math.min(100, R.tris / 8000 * 100); b.append(el("span", { html: `Triángulos (alto detalle) <b>${R.tris}</b><span class="bar"><i style="width:${pct}%;background:${pct > 85 ? "var(--warn)" : "var(--ok)"}"></i></span>` })); }
  b.append(el("span", { style: "flex:1" })); b.append(el("span", { html: `<span class="kbd">V</span> elegir · <span class="kbd">D</span> planta · <span class="kbd">W</span> ventana · <span class="kbd">P</span> puerta · <span class="kbd">B</span> balcón · <span class="kbd">Supr</span> borrar` }));
}

// ================= herramientas =================
const ICON = {
  select: '<path d="M5 3l12 7-5 1.5L9.5 17z"/>', draw: '<path d="M4 20V6l8-3 8 3v14z"/><path d="M4 12h16" stroke-dasharray="2 2"/>',
  win: '<rect x="4" y="3" width="16" height="18" rx="1"/><path d="M12 3v18M4 12h16"/>', door: '<rect x="6" y="3" width="12" height="18" rx="1"/><circle cx="15" cy="12.5" r="1"/>',
  bal: '<path d="M3 11h18M5 11v8h14v-8M9 11v8M15 11v8M12 11v8"/><rect x="8" y="3" width="8" height="8"/>', shop: '<path d="M3 9l2-5h14l2 5M4 9v11h16V9"/><rect x="7" y="12" width="10" height="8"/>'
};
const TOOLS = [["select", "Elegir", "V"], ["draw", "Planta", "D"], null, ["win", "Ventana", "W"], ["door", "Puerta", "P"], ["bal", "Balcón", "B"], ["shop", "Vidriera", "T"]];
function drawTools() {
  const t = $("tools"); t.innerHTML = "";
  for (const it of TOOLS) {
    if (!it) { t.append(el("div", { class: "sep" })); continue; }
    t.append(el("button", { class: "tool" + (tool === it[0] ? " on" : ""), title: `${it[1]} (${it[2]})`, html: `<svg viewBox="0 0 24 24">${ICON[it[0]]}</svg>${it[1]}`, onclick: () => setTool(it[0]) }));
  }
}
function setTool(t) {
  tool = t;
  ghost.visible = false; $("tip").style.display = "none"; drawTools(); drawAll(); hintText(); sidebar(); showCat(); guide(); welcome();
}
function hintText() {
  const h = $("hint"); const m = {
    select: closed ? "<b>Clic</b> en una pared o abertura para elegirla · arrastrá una abertura para moverla · <b>Supr</b> la borra" : "",
    draw: closed ? "Modo <b>planta</b>: arrastrá las esquinas · <b>clic en un lado</b> agrega una esquina · <b>clic derecho</b> en una esquina la borra" : (S.verts.length ? "<b>Clic</b> para poner otra esquina · clic en el punto amarillo (o <b>Enter</b>) para cerrar · <b>Esc</b> cancela" : "<b>Clic</b> en la grilla para poner la primera esquina"),
    win: "Pasá el mouse por una pared y hacé <b>clic</b> para poner una ventana. Sobre una existente, la reemplaza.", door: "<b>Clic</b> en una pared para poner una puerta.",
    bal: "<b>Clic</b> en una pared para poner un balcón.", shop: "<b>Clic</b> para poner una vidriera comercial."
  }; h.innerHTML = m[tool] || "";
}
function tip(e, text) { const t = $("tip"); if (!text) { t.style.display = "none"; return; } const r = $("main").getBoundingClientRect(); t.textContent = text; t.style.display = "block"; t.style.left = (e.clientX - r.left + 14) + "px"; t.style.top = (e.clientY - r.top + 16) + "px"; }
const GUIDE = [["1", "Planta", () => { tab = "bld"; setTool("draw"); }, () => tool === "draw"], ["2", "Paredes", () => { tab = "sel"; setTool("select"); }, () => tool === "select" && tab === "sel"], ["3", "Aberturas", () => setTool("win"), () => ["win", "door", "bal", "shop"].includes(tool)], ["4", "Techo y color", () => { tab = "sty"; setTool("select"); }, () => tool === "select" && (tab === "sty" || tab === "bld")], ["5", "Exportar", () => $("bExport").click(), () => false]];
function guide() { const g = $("guide"); g.innerHTML = ""; GUIDE.forEach(([n, t, fn, on]) => g.append(el("button", { class: on() ? "on" : "", onclick: fn, html: `<b>${n}</b>${t}` }))); }
let tab = "sel";

// ================= planta 2D =================
const pc = $("plan"), pg = pc.getContext("2d"); const view = { cx: 0, cy: 0, s: 14 };
const w2s = (x, y) => [pc.width / 2 + (x - view.cx) * view.s, pc.height / 2 - (y - view.cy) * view.s];
const s2w = (px, py) => [(px - pc.width / 2) / view.s + view.cx, (pc.height / 2 - py) / view.s + view.cy];
const sn = v => Math.round(v / snapG) * snapG;
let hover = null, dragV = null, dragB = null, pan = null;
function drawPlan() {
  const r = pc.getBoundingClientRect(); const W = r.width | 0, Hh = r.height | 0; if (!W) return; if (pc.width !== W || pc.height !== Hh) { pc.width = W; pc.height = Hh; }
  pg.clearRect(0, 0, W, Hh);
  const [x0, y0] = s2w(0, Hh), [x1, y1] = s2w(W, 0);
  for (const [st, col, lw] of [[1, "#1c2026", 1], [8, "#33404f", 1.3]]) {
    if (st * view.s < 5) continue; pg.strokeStyle = col; pg.lineWidth = lw; pg.beginPath();
    for (let x = Math.floor(x0 / st) * st; x <= x1; x += st) { const [a] = w2s(x, 0); pg.moveTo(a, 0); pg.lineTo(a, Hh); }
    for (let y = Math.floor(y0 / st) * st; y <= y1; y += st) { const [, b] = w2s(0, y); pg.moveTo(0, b); pg.lineTo(W, b); }
    pg.stroke();
  }
  const [ox, oy] = w2s(0, 0); pg.strokeStyle = "#4a5a70"; pg.beginPath(); pg.moveTo(ox - 6, oy); pg.lineTo(ox + 6, oy); pg.moveTo(ox, oy - 6); pg.lineTo(ox, oy + 6); pg.stroke();
  const V = S.verts; if (!V.length) return;
  // contorno con curvas (de las facetas del motor si están)
  pg.beginPath(); let first = true;
  const edgesN = closed ? V.length : V.length - 1;
  for (let i = 0; i < edgesN; i++) for (const f of facetsOf(i)) { const [a, b] = w2s(...f[0]), [c, d] = w2s(...f[1]); if (first) { pg.moveTo(a, b); first = false; } pg.lineTo(c, d); }
  if (!closed && hover && tool === "draw") { const [a, b] = w2s(...hover); pg.lineTo(a, b); }
  if (closed) { pg.closePath(); pg.fillStyle = "rgba(78,161,255,.14)"; pg.fill(); }
  pg.strokeStyle = "#7fb7ff"; pg.lineWidth = 2; pg.stroke();
  // retranqueos: contorno de cada tramo superior
  if (closed && R && R.levels) { pg.setLineDash([6, 4]); pg.strokeStyle = "#9fd0ff"; pg.lineWidth = 1.5; for (const lv of R.levels.slice(1)) { pg.beginPath(); lv.poly.forEach((q, k) => { const [a, b] = w2s(...q); k ? pg.lineTo(a, b) : pg.moveTo(a, b); }); pg.closePath(); pg.stroke(); } pg.setLineDash([]); }
  // aberturas sobre cada pared
  if (closed && R) for (let i = 0; i < V.length; i++) { const path = wallPath(i); for (const s of slotsOf(i)) { if (s.deleted || s.f !== 0) continue; const m = atS(i, (s.a0 + s.a1) / 2, path), a = atS(i, s.a0, path), b = atS(i, s.a1, path); const A = w2s(a.x, a.y), B = w2s(b.x, b.y); pg.strokeStyle = "#" + KCOL[s.kind].toString(16).padStart(6, "0"); pg.lineWidth = 5; pg.lineCap = "butt"; pg.beginPath(); pg.moveTo(...A); pg.lineTo(...B); pg.stroke(); } }
  // pared elegida
  if (sel.edge != null && closed) { pg.strokeStyle = "#ffd24e"; pg.lineWidth = 4; pg.beginPath(); let f1 = true; for (const f of facetsOf(sel.edge)) { const [a, b] = w2s(...f[0]), [c, d] = w2s(...f[1]); if (f1) { pg.moveTo(a, b); f1 = false; } pg.lineTo(c, d); } pg.stroke(); }
  // vértices y manijas
  V.forEach((p, i) => { const [a, b] = w2s(...p); pg.fillStyle = (i === 0 && !closed && tool === "draw") ? "#ffd24e" : (sel.vert === i ? "#ffd24e" : "#fff"); pg.beginPath(); pg.arc(a, b, sel.vert === i ? 6 : 4.5, 0, 7); pg.fill(); pg.strokeStyle = "#0008"; pg.lineWidth = 1; pg.stroke(); });
  if (closed && sel.edge != null) { const h = bulgeHandle(sel.edge); const [a, b] = w2s(...h); pg.fillStyle = "#ffd24e"; pg.beginPath(); pg.rect(a - 5, b - 5, 10, 10); pg.fill(); pg.fillStyle = "#fff"; pg.font = "11px system-ui"; pg.fillText("curvar", a + 9, b + 4); }
  // medidas
  pg.fillStyle = "#c8d0dc"; pg.font = "11px system-ui"; pg.textAlign = "center";
  for (let i = 0; i < edgesN; i++) { const p = V[i], q = V[(i + 1) % V.length]; const L = wallLen(i); const m = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]; const nx = (q[1] - p[1]), ny = -(q[0] - p[0]), nl = Math.hypot(nx, ny) || 1; const bg = E()[i] ? E()[i].bulge : 0; const [a, b] = w2s(m[0] + nx / nl * (bg > 0 ? bg + 0.9 : 0.9) * (closed ? 1 : -0.001), m[1] + ny / nl * (bg > 0 ? bg + 0.9 : 0.9) * (closed ? 1 : -0.001)); pg.fillText(L.toFixed(1) + " m", a, b); }
  if (!closed && V.length && hover && tool === "draw") { const q = V[V.length - 1]; const L = Math.hypot(hover[0] - q[0], hover[1] - q[1]); const [a, b] = w2s((hover[0] + q[0]) / 2, (hover[1] + q[1]) / 2); pg.fillStyle = "#ffd24e"; pg.fillText(L.toFixed(2) + " m", a, b - 8); }
  pg.textAlign = "start";
}
function bulgeHandle(i) { const n = S.verts.length, p = S.verts[i], q = S.verts[(i + 1) % n]; const c = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1; const bg = E()[i].bulge || 0; return [(p[0] + q[0]) / 2 + (q[1] - p[1]) / c * bg, (p[1] + q[1]) / 2 - (q[0] - p[0]) / c * bg]; }
function planPos(e) { const r = pc.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
function nearVert(px, py, tol = 10) { let best = null; S.verts.forEach((p, i) => { const [a, b] = w2s(...p); const d = Math.hypot(a - px, b - py); if (d < tol && (!best || d < best[0])) best = [d, i]; }); return best && best[1]; }
function nearEdge(px, py, tol = 8) { if (!closed) return null; let best = null; for (let i = 0; i < S.verts.length; i++) for (const f of facetsOf(i)) { const [a, b] = w2s(...f[0]), [c, d] = w2s(...f[1]); const dx = c - a, dy = d - b, L2 = dx * dx + dy * dy || 1e-9; const t = Math.max(0, Math.min(1, ((px - a) * dx + (py - b) * dy) / L2)); const dist = Math.hypot(px - (a + t * dx), py - (b + t * dy)); if (dist < tol && (!best || dist < best[0])) best = [dist, i]; } return best && best[1]; }
pc.addEventListener("pointerdown", e => {
  const [px, py] = planPos(e); pc.setPointerCapture(e.pointerId);
  if (e.button === 1 || e.button === 2 || e.shiftKey) { pan = { x: e.clientX, y: e.clientY, cx: view.cx, cy: view.cy }; return; }
  if (tool === "draw" && !closed) {
    const v = hover || [sn(s2w(px, py)[0]), sn(s2w(px, py)[1])];
    if (S.verts.length >= 3 && Math.hypot(...w2s(...S.verts[0]).map((c, k) => c - [px, py][k])) < 10) { closePoly(); return; }
    if (S.verts.length && v[0] === S.verts[S.verts.length - 1][0] && v[1] === S.verts[S.verts.length - 1][1]) return;
    S.verts.push(v); S.edges.push(newEdge()); changed(false); return;
  }
  if (closed) {
    if (sel.edge != null) { const h = bulgeHandle(sel.edge), [a, b] = w2s(...h); if (Math.hypot(a - px, b - py) < 11) { dragB = { i: sel.edge, pre: snap() }; return; } }
    const v = nearVert(px, py); if (v != null) { tab = "sel"; sel = { edge: null, slot: null, vert: v }; dragV = { i: v, pre: snap(), moved: false }; drawAll(); sidebar(); return; }
    const ed = nearEdge(px, py); if (ed != null && tool === "draw") { insertVert(ed, s2w(px, py)); return; }
    if (ed != null) { sel = { edge: ed, sec: 0, slot: null, vert: null }; tab = "sel"; if (e.detail === 2) insertVert(ed, s2w(px, py)); drawAll(); sidebar(); guide(); return; }
    sel = NOSEL(); drawAll(); sidebar();
  }
});
pc.addEventListener("pointermove", e => {
  const [px, py] = planPos(e);
  if (pan) { view.cx = pan.cx - (e.clientX - pan.x) / view.s; view.cy = pan.cy + (e.clientY - pan.y) / view.s; drawPlan(); return; }
  const w = s2w(px, py); hover = [sn(w[0]), sn(w[1])];
  if (dragV) { S.verts[dragV.i] = hover.slice(); dragV.moved = true; R = null; drawAll(); return; }
  if (dragB) { const n = S.verts.length, p = S.verts[dragB.i], q = S.verts[(dragB.i + 1) % n]; const c = Math.hypot(q[0] - p[0], q[1] - p[1]) || 1; const m = [(p[0] + q[0]) / 2, (p[1] + q[1]) / 2]; let bg = ((w[0] - m[0]) * (q[1] - p[1]) - (w[1] - m[1]) * (q[0] - p[0])) / c; bg = Math.round(bg * 4) / 4; if (Math.abs(bg) < 0.3) bg = 0; E()[dragB.i].bulge = Math.max(-c * .5, Math.min(c * .5, bg)); drawAll(); return; }
  drawPlan();
});
pc.addEventListener("pointerup", () => {
  pan = null;
  if (dragV) { const d = dragV; dragV = null; if (d.moved) { if (area(S.verts) < 0) { ensureCCW(); } hist.u.push(snap()); changed(true); } }
  if (dragB) { dragB = null; commit(true); }
});
pc.addEventListener("contextmenu", e => { e.preventDefault(); const [px, py] = planPos(e); const v = nearVert(px, py); if (closed && v != null && S.verts.length > 3) removeVert(v); });
pc.addEventListener("wheel", e => { e.preventDefault(); const [px, py] = planPos(e); const before = s2w(px, py); view.s = Math.min(80, Math.max(3, view.s * (e.deltaY < 0 ? 1.12 : .89))); const after = s2w(px, py); view.cx += before[0] - after[0]; view.cy += before[1] - after[1]; drawPlan(); }, { passive: false });
function closePoly() { if (S.verts.length < 3) return; closed = true; ensureCCW(); tool = "select"; sel = NOSEL(); fitView(); drawTools(); commit(true); }
function insertVert(i, w) { R = null; const n = S.verts.length, p = S.verts[i], q = S.verts[(i + 1) % n]; const dx = q[0] - p[0], dy = q[1] - p[1], L2 = dx * dx + dy * dy; const t = Math.max(.1, Math.min(.9, ((w[0] - p[0]) * dx + (w[1] - p[1]) * dy) / L2)); const v = [sn(p[0] + dx * t), sn(p[1] + dy * t)]; S.verts.splice(i + 1, 0, v); S.setbacks.forEach(sb => sb.ins.splice(i + 1, 0, sb.ins[i])); const e = newEdge(); Object.assign(e, { mat: E()[i].mat, rot: E()[i].rot, ws: E()[i].ws, ds: E()[i].ds, bs: E()[i].bs }); E()[i] = { ...E()[i], ov: {}, add: [], bulge: 0 }; S.edges.splice(i + 1, 0, e); sel = { edge: null, slot: null, vert: i + 1 }; commit(true); }
function removeVert(i) { R = null; S.verts.splice(i, 1); S.setbacks.forEach(sb => sb.ins.splice(i, 1)); const e = S.edges; e.splice(i, 1); const pe = (i + e.length - 1) % e.length; e[pe] = { ...e[pe], ov: {}, add: [], bulge: 0 }; sel = NOSEL(); commit(true); }
function fitView() { if (!S.verts.length) return; const xs = S.verts.map(p => p[0]), ys = S.verts.map(p => p[1]); view.cx = (Math.min(...xs) + Math.max(...xs)) / 2; view.cy = (Math.min(...ys) + Math.max(...ys)) / 2; const sz = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), 10); view.s = Math.min(40, Math.min(pc.width, pc.height) / (sz * 1.8)); tgt.set(view.cx, view.cy, H() / 2); dist = Math.max(30, sz * 2.2 + H()); }

// ================= vista 3D =================
const rc = $("v3"), Rn = new T.WebGLRenderer({ canvas: rc, antialias: true }); Rn.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
const sc = new T.Scene(); sc.background = new T.Color(0x1d2127);
const cam = new T.PerspectiveCamera(42, 1, .1, 3000); let az = 1.0, el_ = .55, dist = 60, tgt = new T.Vector3(0, 0, 6);
sc.add(new T.HemisphereLight(0xffffff, 0x4a5566, 1.15)); const sun = new T.DirectionalLight(0xffffff, 1.5); sun.position.set(40, -30, 60); sc.add(sun);
const grid = new T.GridHelper(400, 50, 0x46566e, 0x2a313b); grid.rotation.x = Math.PI / 2; sc.add(grid);
const gridS = new T.GridHelper(400, 400, 0x232830, 0x232830); gridS.rotation.x = Math.PI / 2; gridS.position.z = -.01; sc.add(gridS);
const grpReal = new T.Group(), grpOv = new T.Group(), grpPick = new T.Group(); sc.add(grpReal, grpOv, grpPick);
const PAL = { facade: 0xd9d2c3, glass: 0x5b8fbf, trim: 0xcfc4ad, roof: 0x4a3a32, door: 0x5a3a20, gls: 0x5b8fbf, facb: 0x8a3a28, facc: 0x333338, facd: 0xa0602a, gar: 0x8c8c85, rail: 0xcfc4ad };
const lin2srgb = c => new T.Color().setRGB(c[0], c[1], c[2], T.LinearSRGBColorSpace);
function setCam() { cam.up.set(0, 0, 1); cam.position.set(tgt.x + dist * Math.cos(el_) * Math.cos(az), tgt.y + dist * Math.cos(el_) * Math.sin(az), tgt.z + dist * Math.sin(el_)); cam.lookAt(tgt); }
function clearGroup(g) { while (g.children.length) { const o = g.children.pop(); o.geometry && o.geometry.dispose(); if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose()); } }
let roofMat = null;
function showReal() {
  clearGroup(grpReal); roofMat = null;
  if (!closed || S.verts.length < 3) return;
  if (!R) { // vista previa instantánea
    const sh = new T.Shape(S.verts.map(p => new T.Vector2(p[0], p[1])));
    grpReal.add(new T.Mesh(new T.ExtrudeGeometry(sh, { depth: H(), bevelEnabled: false }), new T.MeshStandardMaterial({ color: 0xb9c4d4, transparent: true, opacity: .8 })));
    return;
  }
  const g = new T.BufferGeometry(); g.setAttribute("position", new T.Float32BufferAttribute(R.v, 3));
  const order = [...R.m.keys()].sort((a, b) => R.m[a] - R.m[b]); const idx = []; order.forEach(t => idx.push(R.f[t * 3], R.f[t * 3 + 1], R.f[t * 3 + 2])); g.setIndex(idx);
  let st = 0; const mats = []; const used = [...new Set(R.m)].sort((a, b) => a - b);
  for (const mi of used) {
    const c = order.filter(t => R.m[t] === mi).length; g.addGroup(st * 3, c * 3, mats.length); st += c;
    const nm = R.mats[mi] || "facade"; const col = (R.cols && R.cols[nm]) ? lin2srgb(R.cols[nm]) : new T.Color(PAL[nm] ?? 0xcccccc);
    const m = new T.MeshStandardMaterial({ color: col, side: T.DoubleSide, roughness: nm.startsWith("gl") ? .15 : .85, metalness: nm.startsWith("gl") ? .2 : 0 });
    if (nm === "roof") roofMat = m; mats.push(m);
  }
  g.computeVertexNormals(); const mesh = new T.Mesh(g, mats); mesh.position.set(R.center[0], R.center[1], 0); grpReal.add(mesh); if (roofMat) roofMat.visible = showRoof;
}
// ---- superposiciones: paredes elegidas, aberturas y piezas de selección
function quadMesh(P, color, op, order = 1) { const g = new T.BufferGeometry(); g.setAttribute("position", new T.Float32BufferAttribute(P.flat(), 3)); g.setIndex([0, 1, 2, 0, 2, 3]); const m = new T.Mesh(g, new T.MeshBasicMaterial({ color, transparent: true, opacity: op, side: T.DoubleSide, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -order, polygonOffsetUnits: -order })); return m; }
function outline(P, color) { const g = new T.BufferGeometry().setFromPoints([...P, P[0]].map(p => new T.Vector3(...p))); return new T.Line(g, new T.LineBasicMaterial({ color })); }
function slotQuad(i, s, off = .05, path = wallPath(i), sec = 0) { const A = atS(i, s.a0, path), B = atS(i, s.a1, path); const pt = (q, z) => [q.x + q.nx * off, q.y + q.ny * off, z]; return [pt(A, s.z0), pt(B, s.z0), pt(B, s.z1), pt(A, s.z1)]; }
function wallQuads(i, sec = 0) { const out = [], w = Wk(i, sec), z0 = w ? w.zmin : 0, z1 = w ? w.zmax : H(); for (const f of wallPath(i, sec)) { const n = [f.dy, -f.dx], o = .03; const a = [f.p[0] + n[0] * o, f.p[1] + n[1] * o], b = [f.p[0] + f.dx * f.L + n[0] * o, f.p[1] + f.dy * f.L + n[1] * o]; out.push([[a[0], a[1], z0], [b[0], b[1], z0], [b[0], b[1], z1], [a[0], a[1], z1]]); } return out; }
function overlay() {
  clearGroup(grpOv); clearGroup(grpPick);
  if (!closed || !R) return;
  for (const w of R.walls) {
    const i = w.edge, sec = w.sec, path = wallPath(i, sec);
    // piezas para elegir con el mouse
    const wq = wallQuads(i, sec); path.forEach((f, k) => { const m = quadMesh(wq[k], 0xffffff, 0); m.userData = { edge: i, sec, f }; m.material.visible = true; grpPick.add(m); });
    if (sel.edge === i && sel.sec === sec) wq.forEach(q => grpOv.add(quadMesh(q, 0xffd24e, .16)));
    for (const s of w.slots) {
      if (s.deleted) continue; const P = slotQuad(i, s, .06, path, sec); const on = sel.edge === i && sel.sec === sec && sel.slot === s.id;
      grpOv.add(quadMesh(P, on ? 0xffd24e : KCOL[s.kind], on ? .45 : (tool === "select" ? .12 : .22), 2)); grpOv.add(outline(P, on ? 0xffd24e : KCOL[s.kind]));
    }
  }
}
// ---- ghost (vista previa de lo que se va a colocar)
const ghost = new T.Group(); ghost.visible = false; sc.add(ghost); let ghostFill = null, ghostLine = null;
function setGhost(P, color, op = .5) { if (ghostFill) { ghost.remove(ghostFill, ghostLine); ghostFill.geometry.dispose(); ghostLine.geometry.dispose(); } ghostFill = quadMesh(P, color, op, 3); ghostLine = outline(P, color); ghost.add(ghostFill, ghostLine); ghost.visible = true; }
function drawAll() { drawPlan(); overlay(); }

// ---- ratón en 3D
const ray = new T.Raycaster(); const mouse = new T.Vector2(); let drag3 = null, slotDrag = null;
function pick3(e) {
  const r = rc.getBoundingClientRect(); mouse.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1); ray.setFromCamera(mouse, cam);
  const hit = ray.intersectObjects(grpPick.children, false)[0]; if (!hit) return null;
  const { edge, sec, f } = hit.object.userData; const p = hit.point; const t = (p.x - f.p[0]) * f.dx + (p.y - f.p[1]) * f.dy; return { edge, sec, s: f.c0 + Math.max(0, Math.min(f.L, t)), z: p.z };
}
function slotAt(h) { if (!h) return null; return slotsOf(h.edge, h.sec).find(s => !s.deleted && h.s >= s.a0 && h.s <= s.a1 && h.z >= s.z0 - .05 && h.z <= s.z1 + .05) || null; }
function defaultsFor(i, kind, sec = 0) { const w = Wk(i, sec), d = w.defaults, fh = w.fh; if (kind === "door") return { w: d.dw, h: d.dh, sill: 0 }; if (kind === "bal") return { w: d.ww, h: 2.1, sill: 0 }; if (kind === "shop") return { w: 2.0, h: Math.max(1, fh - .95), sill: .3 }; return { w: d.ww, h: d.wh, sill: d.sill }; }
function ghostFor(h) {
  if (!h || !R) return null; const w = Wk(h.edge, h.sec); if (!w) return null; const kind = tool; const df = defaultsFor(h.edge, kind, h.sec);
  const ex = slotAt(h); if (ex) return { replace: ex, edge: h.edge, sec: h.sec, P: slotQuad(h.edge, ex, .08, wallPath(h.edge, h.sec), h.sec), ok: true };
  const f = Math.max(0, Math.min(w.nfl - 1, Math.floor((h.z - w.zmin) / w.fh)));
  const c = Math.round(h.s / .25) * .25; const s = { a0: c - df.w / 2, a1: c + df.w / 2, z0: w.zmin + f * w.fh + df.sill, z1: w.zmin + f * w.fh + df.sill + df.h };
  const why = s.a0 < w.amin + .12 || s.a1 > w.amax - .12 ? "Muy cerca del borde de la pared" : s.z1 > w.zmax - .15 ? "No entra en altura" : w.slots.some(o => !o.deleted && Math.min(o.a1, s.a1) - Math.max(o.a0, s.a0) > .05 && Math.min(o.z1, s.z1) - Math.max(o.z0, s.z0) > .05) ? "Se pisa con otra abertura" : "", bad = !!why;
  return { add: { kind, f, a: c, ...(brush[kind] ? { style: brush[kind] } : {}) }, edge: h.edge, sec: h.sec, P: slotQuad(h.edge, s, .08, wallPath(h.edge, h.sec), h.sec), ok: !bad, why };
}
rc.addEventListener("pointerdown", e => {
  rc.setPointerCapture(e.pointerId); drag3 = { x: e.clientX, y: e.clientY, b: e.button, moved: false, sx: e.clientX, sy: e.clientY };
  if (e.button === 0 && tool === "select" && sel.slot) { const h = pick3(e), s = slotAt(h); if (s && s.id === sel.slot && h.edge === sel.edge && h.sec === sel.sec) slotDrag = { edge: h.edge, sec: h.sec, id: s.id, s0: h.s, da: 0, slot: s }; }
});
rc.addEventListener("pointermove", e => {
  if (drag3) { const dx = e.clientX - drag3.x, dy = e.clientY - drag3.y; if (Math.abs(e.clientX - drag3.sx) + Math.abs(e.clientY - drag3.sy) > 3) drag3.moved = true;
    if (slotDrag) { const h = pick3(e); if (h && h.edge === slotDrag.edge && h.sec === slotDrag.sec) { slotDrag.da = Math.round((h.s - slotDrag.s0) / .25) * .25; const s = { ...slotDrag.slot, a0: slotDrag.slot.a0 + slotDrag.da, a1: slotDrag.slot.a1 + slotDrag.da }; setGhost(slotQuad(slotDrag.edge, s, .09, wallPath(slotDrag.edge, slotDrag.sec), slotDrag.sec), 0xffd24e, .35); } return; }
    if (drag3.moved && (drag3.b === 2 || e.shiftKey)) { const k = dist * .0018; tgt.x += (Math.sin(az) * dx - Math.cos(az) * Math.sin(el_) * dy * -1) * k; tgt.y += (-Math.cos(az) * dx - Math.sin(az) * Math.sin(el_) * dy * -1) * k; } else if (drag3.moved && (drag3.b === 0 || drag3.b === 1)) { az -= dx * .008; el_ = Math.min(1.5, Math.max(.03, el_ + dy * .008)); }
    drag3.x = e.clientX; drag3.y = e.clientY; return; }
  if (["win", "door", "bal", "shop"].includes(tool)) { const g = ghostFor(pick3(e)); if (g) { setGhost(g.P, g.replace ? 0x4ea1ff : (g.ok ? 0x4cc38a : 0xef6a5e), g.replace ? .3 : .5); tip(e, g.replace ? "Reemplazar por: " + KINDS[tool].toLowerCase() : (g.ok ? KINDS[tool] + " · clic para poner" : g.why)); } else { ghost.visible = false; tip(e, ""); } return; }
  if (tool === "select") { const h = pick3(e), s = slotAt(h); rc.style.cursor = h ? "pointer" : "default"; tip(e, s ? KINDS[s.kind] + " · clic para elegir" : (h ? "Pared " + (h.edge + 1) + (h.sec ? " (tramo superior)" : "") + " · clic para elegir" : "")); }
});
rc.addEventListener("pointerleave", () => { $("tip").style.display = "none"; ghost.visible = false; });
rc.addEventListener("pointerup", e => {
  const d = drag3; drag3 = null; ghost.visible = tool !== "select" ? ghost.visible : false;
  if (slotDrag) { const sd = slotDrag; slotDrag = null; ghost.visible = false; if (sd.da) moveSlot(sd.edge, sd.id, sd.da, sd.sec); return; }
  if (!d || d.moved || d.b !== 0) return;
  const h = pick3(e);
  if (tool === "select") { if (!h) { sel = NOSEL(); } else { const s = slotAt(h); sel = { edge: h.edge, sec: h.sec, slot: s ? s.id : null, vert: null }; tab = "sel"; } drawAll(); guide(); sidebar(); return; }
  if (["win", "door", "bal", "shop"].includes(tool)) { const g = ghostFor(h); if (!g || !g.ok) { if (g) toast("No entra ahí: se pisa con otra abertura o queda muy cerca del borde"); return; } placeFrom(g); }
});
rc.addEventListener("contextmenu", e => e.preventDefault());
rc.addEventListener("wheel", e => { e.preventDefault(); dist = Math.min(600, Math.max(4, dist * (e.deltaY < 0 ? .9 : 1.1))); }, { passive: false });
rc.addEventListener("dblclick", e => { const h = pick3(e); if (!h) return; const p = atS(h.edge, h.s, wallPath(h.edge, h.sec)); tgt.set(p.x, p.y, h.z); });

// ================= ediciones de aberturas =================
function placeFrom(g) {
  const ed = ES(g.edge, g.sec);
  if (g.add) { ed.add.push(g.add); sel = { edge: g.edge, sec: g.sec, slot: "add," + (ed.add.length - 1), vert: null }; }
  else { const s = g.replace, kind = tool; const st = brush[kind] || undefined; if (s.id.startsWith("add,")) { const a = ed.add[+s.id.slice(4)]; if (a) { if (a.kind !== kind) { delete a.w; delete a.h; delete a.sill; } a.kind = kind; if (st) a.style = st; else delete a.style; } } else { const o = ed.ov[s.id] = ed.ov[s.id] || {}; delete o.del; if (o.kind && o.kind !== kind || s.kind !== kind) { delete o.w; delete o.h; delete o.sill; } o.kind = kind; if (st) o.style = st; else delete o.style; } sel = { edge: g.edge, sec: g.sec, slot: s.id, vert: null }; }
  toast((g.add ? KINDS[tool] + " agregada" : KINDS[tool] + ": reemplazada") + " · Ctrl+Z para deshacer", 1800); commit(true);
}
function moveSlot(i, id, da, sec = sel.sec || 0) { const ed = ES(i, sec); if (id.startsWith("add,")) { const a = ed.add[+id.slice(4)]; a.a = (a.a ?? 0) + da; } else { const o = ed.ov[id] = ed.ov[id] || {}; o.da = (o.da || 0) + da; } commit(true); }
function setSlotField(i, id, k, v, sec = sel.sec || 0) { const ed = ES(i, sec); const tgt = id.startsWith("add,") ? ed.add[+id.slice(4)] : (ed.ov[id] = ed.ov[id] || {}); if (v === "" || v == null) delete tgt[k]; else tgt[k] = v; commit(true); }
function deleteSlot(i, id, sec = sel.sec || 0) { const ed = ES(i, sec); if (id.startsWith("add,")) ed.add.splice(+id.slice(4), 1); else ed.ov[id] = { del: true }; sel.slot = null; toast("Abertura borrada · Ctrl+Z para deshacer", 1800); commit(true); }
function resetSlot(i, id, sec = sel.sec || 0) { const ed = ES(i, sec); if (!id.startsWith("add,")) delete ed.ov[id]; commit(true); }
function clearWall(i, sec = sel.sec || 0) { const ed = ES(i, sec); ed.add = []; ed.ov = {}; slotsOf(i, sec).forEach(s => { if (!s.id.startsWith("add,")) ed.ov[s.id] = { del: true }; }); sel.slot = null; commit(true); }
function resetWall(i, sec = sel.sec || 0) { if (sec) Object.assign(ES(i, sec), { ov: {}, add: [] }); else Object.assign(E()[i], { ov: {}, add: [], sx: {}, mat: 0, rot: 0, ws: "", ds: "", bs: "", bulge: 0 }); sel.slot = null; commit(true); }

// ================= catálogo con miniaturas =================
let catFor = null; // {kind, onPick, cur}
function openCat(kind, cur, onPick, title) { catFor = { kind, cur, onPick }; $("catTitle").textContent = title || "Catálogo"; $("catQ").value = ""; $("cat").classList.add("show"); $("main").classList.add("catopen"); fillCat(); }
function closeCat() { catFor = null; $("cat").classList.remove("show"); $("main").classList.remove("catopen"); }
function showCat() { if (["win", "door", "bal"].includes(tool)) openCat(tool, brush[tool], n => { brush[tool] = n; sidebar(); }, "Estilo de " + KINDS[tool].toLowerCase()); else closeCat(); }
function fillCat() {
  if (!catFor || !CAT) return; const g = $("catGrid"); g.innerHTML = ""; const q = $("catQ").value.toLowerCase();
  const list = CAT[CATKIND[catFor.kind]] || [];
  g.append(el("div", { class: "card" + (!catFor.cur ? " on" : ""), onclick: () => pickCat(null), html: `<div style="display:flex;align-items:center;justify-content:center;flex:1">Estilo general</div>` }));
  list.filter(x => x.name.toLowerCase().includes(q)).forEach(x => g.append(el("div", { class: "card" + (catFor.cur === x.name ? " on" : ""), title: x.name, onclick: () => pickCat(x.name), html: `<img loading="lazy" src="${x.thumb}"><div>${x.name}</div>` })));
}
function pickCat(n) { const f = catFor; if (!f) return; f.cur = n; f.onPick(n); fillCat(); if (tool === "select" || !["win", "door", "bal"].includes(tool)) closeCat(); }
$("catQ").oninput = fillCat; $("catX").onclick = () => { if (["win", "door", "bal"].includes(tool)) setTool("select"); else closeCat(); };

// ================= panel lateral =================
const numRow = (label, val, step, min, max, fn, unit = "") => { const i = el("input", { type: "number", value: (+val).toFixed(2).replace(/\.?0+$/, ""), step, min, max }); i.onchange = () => { let v = parseFloat(i.value); if (isNaN(v)) return; v = Math.max(min, Math.min(max, v)); fn(v); }; return el("div", { class: "row" }, el("label", {}, label), i, unit ? el("span", { class: "hintline" }, unit) : ""); };
const sec = (title, ...kids) => el("div", { class: "sec" }, el("h3", {}, title), ...kids);
function setbackSec() {
  const kids = [el("div", { class: "hintline" }, "Cada retranqueo achica la planta de un piso hacia arriba. Una pared con 0 m no se retira; el retiro no puede volver a achicarse más arriba.")];
  S.setbacks.forEach((sb, k) => {
    const from = el("input", { type: "number", value: sb.from + 1, min: 2, max: S.floors, step: 1 }); from.onchange = () => { sb.from = Math.max(1, Math.min(S.floors - 1, Math.round(+from.value) - 1)); S.setbacks.sort((a, b) => a.from - b.from); commit(true); };
    kids.push(el("div", { class: "row", style: "margin-top:12px" }, el("label", {}, "Desde el piso"), from, el("button", { class: "ghost", title: "Quitar este retranqueo", onclick: () => { S.setbacks.splice(k, 1); commit(true); } }, "✕")));
    kids.push(numRow("Todas las paredes", Math.max(...sb.ins), .5, 0, 20, v => { sb.ins = sb.ins.map(() => v); commit(true); }, "m"));
    S.verts.forEach((_, i) => kids.push(numRow(`Pared ${i + 1}`, sb.ins[i] || 0, .5, 0, 20, v => { sb.ins[i] = v; commit(true); }, "m")));
  });
  kids.push(el("div", { class: "row" }, el("button", { disabled: S.floors < 2, onclick: () => { const last = S.setbacks.length ? S.setbacks[S.setbacks.length - 1] : null; const from = Math.min(S.floors - 1, last ? last.from + 1 : Math.max(1, Math.round(S.floors / 2))); S.setbacks.push({ from, ins: S.verts.map((_, i) => last ? last.ins[i] + 1.5 : 2) }); commit(true); } }, "+ Agregar retranqueo")));
  return sec("Retranqueos", ...kids);
}
function stylePicker(kind, cur, onPick, labelGeneral = "Estilo general") {
  const it = CAT && (CAT[CATKIND[kind]] || []).find(x => x.name === cur);
  return el("button", { class: "pick", onclick: () => openCat(kind, cur, n => { onPick(n); sidebar(); }, "Estilo de " + KINDS[kind].toLowerCase()), html: `${it ? `<img src="${it.thumb}">` : `<img style="opacity:.25">`}<span>${cur || labelGeneral}</span><span style="flex:0">▾</span>` });
}
function selTab(sd) {
  if (sel.edge != null && E()[sel.edge]) {
    const i = sel.edge, wsec = sel.sec || 0, ed = E()[i], w = Wk(i, wsec);
    const s = sel.slot && w ? w.slots.find(x => x.id === sel.slot) : null;
    if (s) sd.append(sec("Abertura elegida",
      el("div", { class: "row" }, el("label", {}, "Tipo"), el("select", { onchange: ev => { const k = ev.target.value; if (s.id.startsWith("add,")) { const a = ed.add[+s.id.slice(4)]; a.kind = k; delete a.w; delete a.h; delete a.sill; } else { const o = ed.ov[s.id] = ed.ov[s.id] || {}; o.kind = k; delete o.w; delete o.h; delete o.sill; } commit(true); } }, ...Object.entries(KINDS).map(([k, n]) => el("option", { value: k, selected: k === s.kind }, n)))),
      CATKIND[s.kind] ? stylePicker(s.kind, s.style === "__GLOBAL__" ? null : s.style, n => setSlotField(i, s.id, "style", n)) : "",
      numRow("Ancho", s.a1 - s.a0, .1, .4, 12, v => setSlotField(i, s.id, "w", v), "m"), numRow("Alto", s.z1 - s.z0, .1, .3, 12, v => setSlotField(i, s.id, "h", v), "m"),
      s.kind === "win" || s.kind === "shop" ? numRow("Altura del antepecho", s.z0 - (w.zmin + s.f * w.fh), .05, 0, 4, v => setSlotField(i, s.id, "sill", v), "m") : "",
      el("div", { class: "row" }, el("button", { onclick: () => deleteSlot(i, s.id) }, "Borrar"), s.edited && !s.id.startsWith("add,") ? el("button", { onclick: () => resetSlot(i, s.id) }, "Restablecer") : "", el("button", { onclick: () => { sel.slot = null; drawAll(); sidebar(); } }, "Ver la pared")),
      el("div", { class: "hintline" }, "Arrastrala en la vista 3D para moverla.")));
    sd.append(sec(`Pared ${i + 1}${wsec ? " · tramo superior" : ""}`,
      el("div", { class: "hintline" }, `${wallLen(i).toFixed(1)} m de largo · ${w ? w.slots.filter(x => !x.deleted).length : 0} aberturas`),
      el("div", { class: "row wrap" }, ...[1, 2, 3, 4].map(m => el("button", { class: "matbtn" + (ed.mat === m ? " on" : ""), title: R && R.mats ? "" : "", onclick: () => { ed.mat = ed.mat === m ? 0 : m; commit(true); }, html: `<span class="sw" style="background:${matHex(m)}"></span>${"ABCD"[m - 1]}` })), el("button", { onclick: () => { ed.mat = 0; commit(true); } }, "Igual al resto")),
      el("div", { class: "row" }, el("label", {}, "Girar textura"), el("div", { class: "seg" }, ...[0, 1, 2, 3].map(r => el("button", { class: ed.rot === r ? "on" : "", onclick: () => { ed.rot = r; commit(true); } }, r * 90 + "°")))),
      numRow("Curvatura", ed.bulge, .5, -12, 12, v => { ed.bulge = v; commit(true); }, "m (0 = recta)"),
      el("div", { class: "row" }, el("label", {}, "Ventanas de esta pared"), ""), stylePicker("win", ed.ws, n => { ed.ws = n || ""; commit(true); }),
      el("div", { class: "row" }, el("label", {}, "Puertas de esta pared"), ""), stylePicker("door", ed.ds, n => { ed.ds = n || ""; commit(true); }),
      el("div", { class: "row" }, el("label", {}, "Balcones de esta pared"), ""), stylePicker("bal", ed.bs, n => { ed.bs = n || ""; commit(true); }),
      el("div", { class: "row" }, el("button", { onclick: () => clearWall(i) }, "Vaciar pared"), el("button", { onclick: () => resetWall(i) }, "Restablecer pared")),
      el("div", { class: "row" }, el("button", { onclick: () => { E().forEach((x, k) => { if (k !== i) { x.mat = ed.mat; x.rot = ed.rot; x.ws = ed.ws; x.ds = ed.ds; x.bs = ed.bs; } }); commit(true); toast("Material y estilos copiados a todas las paredes"); } }, "Copiar material y estilos a todas"))));
    const wbox = el("div", { class: "sec" }, el("h3", {}, "Solo esta pared"), el("div", { class: "hintline", style: "margin-bottom:6px" }, "Cambian solo esta pared; lo demás sigue las opciones generales.")); compBlocks(wbox, "w", ed); sd.append(wbox);
  } else if (sel.vert != null) {
    sd.append(sec("Esquina", el("div", { class: "hintline" }, "Arrastrala en la planta para moverla. Clic derecho (o Supr) la borra."), el("div", { class: "row" }, el("button", { disabled: S.verts.length <= 3, onclick: () => removeVert(sel.vert) }, "Borrar esquina"))));
  } else {
    sd.append(sec("Paredes", el("div", { class: "hintline" }, "Elegí una pared (acá, en la planta o en la vista 3D) para cambiar su material, curvarla o poner aberturas."),
      el("div", { class: "wlist", style: "margin-top:8px" }, ...S.verts.map((_, i) => el("button", { onclick: () => { sel = { edge: i, sec: 0, slot: null, vert: null }; sidebar(); drawAll(); } }, el("span", {}, "Pared " + (i + 1)), el("span", { class: "hintline" }, wallLen(i).toFixed(1) + " m"))))));
  }
}
function bldTab(sd) {
  sd.append(sec("Edificio",
    numRow("Pisos", S.floors, 1, 1, 40, v => { S.floors = Math.round(v); commit(true); }), numRow("Alto de piso", S.floor_h, .1, 2.2, 6, v => { S.floor_h = v; commit(true); }, "m"),
    el("div", { class: "row" }, el("label", {}, "Techo"), el("select", { style: "max-width:170px", onchange: ev => { S.roof = ev.target.value; commit(true); } }, ...(CAT ? CAT.roofs : []).map(r => el("option", { value: r.id, selected: r.id === S.roof }, r.label)))),
    el("div", { class: "row" }, el("label", {}, "Balcones"), el("select", { onchange: ev => { S.props.balconies = ev.target.value; commit(true); } }, ...[["none", "Sin balcones"], ["alternate", "Alternados"], ["all", "En todas"], ["random", "Al azar"]].map(([k, n]) => el("option", { value: k, selected: S.props.balconies === k }, n)))),
    el("div", { class: "row" }, el("label", {}, "Cornisas entre pisos"), el("input", { type: "checkbox", checked: !!S.props.string_courses, onchange: ev => { S.props.string_courses = ev.target.checked; commit(true); } }))));
  sd.append(sec("Planta", el("div", { class: "row" }, el("label", {}, "Imán de la grilla"), el("select", { onchange: ev => { snapG = +ev.target.value; } }, ...[[.25, "25 cm"], [.5, "50 cm"], [1, "1 m"], [2, "2 m"], [8, "8 m (celda)"]].map(([v, n]) => el("option", { value: v, selected: v === snapG }, n)))),
    el("div", { class: "hintline" }, "Doble clic sobre una pared en la planta agrega una esquina. Clic derecho sobre una esquina la borra.")));
}
function styTab(sd) {
  sd.append(sec("Estilos generales", el("div", { class: "hintline" }, "Valen para todas las paredes que no tengan uno propio."),
    el("div", { class: "row" }, el("label", {}, "Ventanas")), stylePicker("win", S.props.window_style, n => { S.props.window_style = n || "NONE"; if (!n) delete S.props.window_style; commit(true); }, "Predeterminado"),
    el("div", { class: "row" }, el("label", {}, "Puertas")), stylePicker("door", S.props.door_style, n => { S.props.door_style = n || "NONE"; if (!n) delete S.props.door_style; commit(true); }, "Predeterminado"),
    el("div", { class: "row" }, el("label", {}, "Balcones")), stylePicker("bal", S.props.balcony_style, n => { S.props.balcony_style = n || "NONE"; if (!n) delete S.props.balcony_style; commit(true); }, "Predeterminado")));
  const colRow = (label, k) => { const i = el("input", { type: "color", value: toHex(colorOf(k)), style: "width:44px;height:26px;padding:0;border:1px solid var(--ln);border-radius:5px;background:none" }); i.onchange = () => { S.props[k] = fromHex(i.value); commit(true); }; return el("div", { class: "row" }, el("label", {}, label), i, el("button", { class: "ghost", title: "Volver al color original", onclick: () => { delete S.props[k]; commit(true); } }, "↺")); };
  sd.append(sec("Colores", colRow("Fachada (A)", "facade_color"), colRow("Material B", "mat_b_color"), colRow("Material C", "mat_c_color"), colRow("Material D", "mat_d_color"), colRow("Molduras", "trim_color"), colRow("Techo", "roof_color"), colRow("Puertas", "door_color")));
}
// ---- opciones del add-on (generadas desde sus propios cuadros de componentes)
const openComp = new Set(["gwindow", "wwindow"]); let optQ = "";
function fctl(f, val, set) {
  if (f.type === "BOOLEAN") { const c = el("input", { type: "checkbox", checked: !!val }); c.onchange = () => set(c.checked); return c; }
  if (f.type === "ENUM") { const s = el("select", { style: "max-width:170px" }, ...(f.items || []).map(([id, n]) => el("option", { value: id, selected: String(id) === String(val) }, n))); s.onchange = () => set(s.value); return s; }
  if (f.type === "COLOR") { const c = el("input", { type: "color", value: toHex(val || [1, 1, 1]), style: "width:44px;height:26px;padding:0;border:1px solid var(--ln);border-radius:5px;background:none" }); c.onchange = () => set(fromHex(c.value)); return c; }
  const rng = (f.smax ?? 10) - (f.smin ?? 0), step = f.type === "INT" ? 1 : rng <= 2 ? .05 : rng <= 20 ? .1 : .5;
  const i = el("input", { type: "number", value: +(+val).toFixed(3), step, min: f.min, max: f.max, style: "width:76px" });
  i.onchange = () => { let v = parseFloat(i.value); if (isNaN(v)) return; v = Math.max(f.min ?? -1e9, Math.min(f.max ?? 1e9, v)); set(f.type === "INT" ? Math.round(v) : v); };
  return i;
}
function fieldRow(f, mode, ed) {
  const isW = mode === "w";
  const cur = isW ? (ed.wp && ed.wp[f.w] !== undefined ? ed.wp[f.w] : (f.g && S.props[f.g] !== undefined ? S.props[f.g] : f.default)) : (f.g === "r_preset" ? S.roof : (S.props[f.g] !== undefined ? S.props[f.g] : f.default));
  const set = v => { if (isW) { (ed.wp = ed.wp || {})[f.w] = v; } else if (f.g === "r_preset") { S.roof = v; Object.keys(S.props).filter(k => k.startsWith("r_")).forEach(k => delete S.props[k]); } else S.props[f.g] = v; commit(true); };
  const changedFlag = isW ? (ed.wp && ed.wp[f.w] !== undefined) : (f.g === "r_preset" ? false : S.props[f.g] !== undefined);
  const lab = el("label", { title: f.desc || f.label, style: changedFlag ? "color:var(--tx)" : "" }, f.label + (f.unit === "LENGTH" ? " (m)" : ""));
  const row = el("div", { class: "row" }, lab, fctl(f, cur, set));
  if (changedFlag) row.append(el("button", { class: "ghost", title: "Volver al valor general", onclick: () => { if (isW) { delete ed.wp[f.w]; if (!Object.keys(ed.wp).length) delete ed.wp; } else delete S.props[f.g]; commit(true); } }, "↺"));
  return row;
}
function compBlocks(sd, mode, ed) {
  if (!SCH) return;
  const q = optQ.trim().toLowerCase(); const hidden = new Set((R && R.hidden) || []);
  for (const c of SCH.comps) {
    if (mode === "w" && !c.wall) continue; if (mode === "g" && c.only_all === undefined) { }
    const groups = c.groups.map(g => ({ title: g.title, fields: g.fields.filter(f => (mode === "w" ? f.w : f.g) && (mode === "w" || !hidden.has(f.g)) && (!q || (f.label + " " + g.title + " " + c.label).toLowerCase().includes(q))) })).filter(g => g.fields.length);
    if (!groups.length) continue;
    const key = mode + c.key; const det = el("details", { class: "comp" }, el("summary", { title: c.desc }, c.label));
    if (q || openComp.has(key)) det.open = true; det.ontoggle = () => { if (det.open) openComp.add(key); else openComp.delete(key); };
    groups.forEach(g => { det.append(el("div", { class: "gtitle" }, g.title)); g.fields.forEach(f => det.append(fieldRow(f, mode, ed))); });
    if (mode === "w" && ed.wp) { const mine = c.groups.flatMap(g => g.fields).filter(f => f.w && ed.wp[f.w] !== undefined); if (mine.length) det.append(el("div", { class: "row" }, el("button", { onclick: () => { mine.forEach(f => delete ed.wp[f.w]); if (!Object.keys(ed.wp).length) delete ed.wp; commit(true); } }, "Volver a lo general"))); }
    sd.append(det);
  }
}
function optTab(sd) {
  const q = el("input", { type: "text", placeholder: "Buscar una opción…", value: optQ, style: "width:100%" }); q.oninput = () => { optQ = q.value; const pos = q.selectionStart; sidebar(); const n = $("side").querySelector("input[type=text]"); if (n) { n.focus(); n.setSelectionRange(pos, pos); } };
  sd.append(el("div", { class: "sec" }, q, el("div", { class: "hintline", style: "margin-top:6px" }, "Son las mismas opciones del add-on de Blender. Valen para todo el edificio; para una pared sola, elegila y mirá «Solo esta pared».")));
  const box = el("div", { class: "sec" }); sd.append(box); compBlocks(box, "g");
  if (!SCH) box.append(el("div", { class: "hintline" }, "Esperando al motor…"));
}
function sbTab(sd) {
  sd.append(setbackSec());
}
function sidebar() {
  const sd = $("side"); const keep = sd.scrollTop; sd.innerHTML = "";
  if (!closed) { sd.append(el("div", { class: "empty", html: "<b>Todavía no hay edificio</b><br>Elegí una forma en el centro de la pantalla o dibujá tu planta." })); return; }
  const hasSel = sel.edge != null || sel.vert != null;
  const tabs = [["sel", hasSel ? "Selección" : "Paredes"], ["bld", "Edificio"], ["sty", "Estilo"], ["opt", "Opciones"], ["sb", "Retranqueo"]];
  sd.append(el("div", { class: "tabs" }, ...tabs.map(([k, n]) => el("button", { class: tab === k ? "on" : "", onclick: () => { tab = k; sidebar(); guide(); } }, n))));
  const body = el("div"); sd.append(body);
  ({ sel: selTab, bld: bldTab, sty: styTab, opt: optTab, sb: sbTab })[tab](body);
  sd.scrollTop = keep;
}

// ================= plantas rápidas, archivos, exportación =================
const PRE = { box: [[0, 0], [20, 0], [20, 12], [0, 12]], L: [[0, 0], [24, 0], [24, 10], [10, 10], [10, 18], [0, 18]], U: [[0, 0], [28, 0], [28, 16], [20, 16], [20, 8], [8, 8], [8, 16], [0, 16]], T: [[0, 8], [10, 8], [10, 0], [22, 0], [22, 8], [32, 8], [32, 18], [0, 18]] };
function loadPreset(k) {
  let v;
  if (k === "round") { v = []; const n = 24; for (let i = 0; i < n; i++) v.push([Math.round(Math.cos(i / n * 2 * Math.PI) * 8 * 4) / 4, Math.round(Math.sin(i / n * 2 * Math.PI) * 8 * 4) / 4]); S.verts = v; S.edges = v.map(newEdge); }
  else if (k === "curve") { S.verts = [[0, 0], [24, 0], [24, 12], [0, 12]]; S.edges = S.verts.map(newEdge); S.edges[1].bulge = 4; }
  else { const b = PRE[k]; const cx = b.reduce((a, p) => a + p[0], 0) / b.length, cy = b.reduce((a, p) => a + p[1], 0) / b.length; S.verts = b.map(p => [Math.round(p[0] - cx), Math.round(p[1] - cy)]); S.edges = S.verts.map(newEdge); }
  S.setbacks = []; closed = true; sel = NOSEL(); tool = "select"; R = null; ensureCCW(); fitView(); drawTools(); commit(true);
}
// ---- pantalla de inicio, «Nuevo», ayuda y vistas
const SHAPEICON = { box: PRE.box, L: PRE.L, U: PRE.U, T: PRE.T };
function shapeSvg(b) { const xs = b.map(p => p[0]), ys = b.map(p => p[1]); const x0 = Math.min(...xs), y0 = Math.min(...ys), w = Math.max(...xs) - x0, h = Math.max(...ys) - y0, s = Math.min(100 / w, 60 / h); return `<svg viewBox="0 0 100 70"><polygon points="${b.map(p => ((p[0] - x0) * s + (100 - w * s) / 2).toFixed(1) + "," + (65 - (p[1] - y0) * s - (60 - h * s) / 2 + 0).toFixed(1)).join(" ")}"/></svg>`; }
function welcome() {
  const w = $("welcome"); const show = !closed && S.verts.length === 0 && tool !== "draw"; w.classList.toggle("show", show); if (!show) return; w.innerHTML = "";
  const card = (svg, name, fn) => el("div", { class: "shape", onclick: fn, html: svg + `<div>${name}</div>` });
  w.append(el("div", { class: "card2" }, el("h1", {}, "¿Cómo querés empezar?"), el("p", {}, "Elegí una planta de partida o dibujá la tuya. Después podés mover esquinas, curvar paredes y agregar ventanas y puertas con el mouse."),
    el("div", { class: "shapes" }, card(shapeSvg(PRE.box), "Rectángulo", () => loadPreset("box")), card(shapeSvg(PRE.L), "En L", () => loadPreset("L")), card(shapeSvg(PRE.U), "En U", () => loadPreset("U")), card(shapeSvg(PRE.T), "En T", () => loadPreset("T")),
      card('<svg viewBox="0 0 100 70"><circle cx="50" cy="35" r="28"/></svg>', "Torre redonda", () => loadPreset("round")), card('<svg viewBox="0 0 100 70"><path d="M12 8h50a24 27 0 0 1 0 54H12z"/></svg>', "Frente curvo", () => loadPreset("curve")),
      card('<svg viewBox="0 0 100 70"><path d="M15 55l15-40 40 5 15 35z" stroke-dasharray="6 5"/></svg>', "Dibujar la mía", () => { setTool("draw"); w.classList.remove("show"); }), card('<svg viewBox="0 0 100 70"><path d="M50 12v34M34 32l16 16 16-16M18 58h64"/></svg>', "Abrir archivo", () => $("file").click())),
    el("div", { class: "steps", html: "<span><b>1</b> Planta</span><span><b>2</b> Paredes</span><span><b>3</b> Ventanas y puertas</span><span><b>4</b> Techo y color</span><span><b>5</b> Exportar</span>" })));
}
$("bNew").onclick = () => { if (!closed && !S.verts.length) return; const m = $("mbox"); m.innerHTML = ""; m.append(el("h2", {}, "¿Empezar un edificio nuevo?"), el("div", { class: "hintline" }, "Se borra lo que hay en pantalla. Podés volver con Ctrl+Z, o guardalo antes con «Guardar»."), el("div", { class: "row", style: "justify-content:flex-end;margin-top:14px" }, el("button", { onclick: () => $("modal").classList.remove("show") }, "Cancelar"), el("button", { class: "pri", onclick: () => { $("modal").classList.remove("show"); S.verts = []; S.edges = []; S.setbacks = []; closed = false; R = null; sel = NOSEL(); tool = "select"; tab = "sel"; showReal(); commit(false); drawTools(); guide(); } }, "Sí, nuevo"))); $("modal").classList.add("show"); };
$("bHelp").onclick = helpModal;
function helpModal() { const m = $("mbox"); m.innerHTML = ""; const rows = [["V", "Elegir paredes y aberturas"], ["D", "Editar / dibujar la planta"], ["W · P · B · T", "Colocar ventana · puerta · balcón · vidriera"], ["Supr", "Borrar la abertura o esquina elegida"], ["Ctrl+Z · Ctrl+Y", "Deshacer · rehacer"], ["Esc", "Cancelar / volver a Elegir"], ["Arrastrar (3D)", "Girar la vista"], ["Rueda", "Acercar o alejar"], ["Clic derecho + arrastrar", "Mover la vista"], ["Doble clic (3D)", "Centrar la vista en ese punto"], ["Clic derecho (planta)", "Borrar una esquina"]]; m.append(el("h2", {}, "Atajos y ayuda"), el("table", { class: "kt" }, ...rows.map(r => el("tr", {}, el("td", {}, el("span", { class: "kbd" }, r[0])), el("td", {}, r[1])))), el("div", { class: "row", style: "justify-content:flex-end;margin-top:14px" }, el("button", { class: "pri", onclick: () => $("modal").classList.remove("show") }, "Entendido"))); $("modal").classList.add("show"); }
document.querySelectorAll("#vnav button").forEach(b => b.onclick = () => { if (b.dataset.az === "top") { el_ = 1.5; } else { az = +b.dataset.az; el_ = .35; } });
$("bUndo").onclick = undo; $("bRedo").onclick = redo;
$("bSave").onclick = () => { const a = el("a", { href: URL.createObjectURL(new Blob([snap()], { type: "application/json" })), download: "edificio.cs2ed.json" }); a.click(); toast("Guardado en tus descargas"); };
$("bOpen").onclick = () => $("file").click();
$("file").onchange = async e => { const f = e.target.files[0]; if (!f) return; try { restore(await f.text()); sel = NOSEL(); R = null; hist.u.push(snap()); fitView(); changed(true); toast("Archivo abierto"); } catch { toast("No pude leer ese archivo"); } e.target.value = ""; };
$("bRoof").onclick = e => { showRoof = !showRoof; e.target.classList.toggle("on", showRoof); if (roofMat) roofMat.visible = showRoof; };
$("bFit").onclick = fitView;
document.querySelectorAll(".seg [data-v]").forEach(b => b.onclick = () => { viewMode = b.dataset.v; document.querySelectorAll(".seg [data-v]").forEach(x => x.classList.toggle("on", x === b)); $("main").className = (viewMode === "both" ? "" : "v-" + viewMode) + ($("cat").classList.contains("show") ? " catopen" : ""); setTimeout(() => { drawPlan(); }, 30); });
$("bExport").onclick = () => {
  if (!closed) return toast("Primero dibujá la planta del edificio");
  const m = $("mbox"); m.innerHTML = ""; const out = el("input", { type: "text", value: localStorage.getItem("cs2out") || "", placeholder: "Carpeta en tu PC, por ejemplo C:\\cs2\\mi_edificio", style: "width:100%" }); const name = el("input", { type: "text", value: localStorage.getItem("cs2name") || "Edificio01", style: "width:100%" });
  const msg = el("div", { class: "hintline", style: "margin-top:10px;min-height:2.6em" }); const go = el("button", { class: "pri" }, "Exportar");
  go.onclick = async () => { go.disabled = true; msg.textContent = "Blender está armando los LODs, las UV y los archivos… (puede tardar un poco)"; localStorage.setItem("cs2out", out.value); localStorage.setItem("cs2name", name.value);
    try { const r = await (await fetch("/export", { method: "POST", body: JSON.stringify({ ...recipe(), out: out.value, name: name.value }) })).json(); if (r.error) throw new Error(r.error); msg.innerHTML = `Listo. Triángulos: <b>${r.tris.join(" / ")}</b> (alto / medio / bajo)${r.warn && r.warn.length ? `<br><span style="color:var(--warn)">${r.warn.join("<br>")}</span>` : ""}<br>Archivos en: <b>${r.path}</b>`; } catch (e) { msg.innerHTML = `<span style="color:var(--bad)">No se pudo exportar: ${e.message}</span>`; } go.disabled = false; };
  m.append(el("h2", {}, "Exportar a Cities: Skylines II"), el("div", { class: "hintline" }, "Blender arma los tres niveles de detalle, las UV y los archivos del juego."), el("div", { class: "row" }, el("label", {}, "Nombre")), name, el("div", { class: "row" }, el("label", {}, "Carpeta de salida")), out, msg, el("div", { class: "row", style: "justify-content:flex-end;margin-top:12px" }, el("button", { onclick: () => $("modal").classList.remove("show") }, "Cerrar"), go));
  $("modal").classList.add("show");
};
$("modal").onclick = e => { if (e.target.id === "modal") $("modal").classList.remove("show"); };

// ================= teclado =================
addEventListener("keydown", e => {
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName) && document.activeElement.type !== "checkbox") return;
  const k = e.key.toLowerCase();
  if ((e.ctrlKey || e.metaKey) && k === "z") { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
  if ((e.ctrlKey || e.metaKey) && k === "y") { e.preventDefault(); redo(); return; }
  if (k === "escape") { if (!closed && S.verts.length) { S.verts = []; S.edges = []; drawAll(); hintText(); } else if (tool !== "select") setTool("select"); else { sel = NOSEL(); drawAll(); sidebar(); } return; }
  if (k === "enter" && tool === "draw" && !closed) { closePoly(); return; }
  if (k === "delete" || k === "backspace") { if (sel.edge != null && sel.slot) deleteSlot(sel.edge, sel.slot); else if (sel.vert != null && S.verts.length > 3) removeVert(sel.vert); return; }
  if (e.key === "?") { helpModal(); return; }
  const m = { v: "select", d: "draw", w: "win", p: "door", b: "bal", t: "shop" }[k]; if (m && !e.ctrlKey && !e.metaKey) setTool(m);
});

// ================= arranque =================
function loop() {
  const r = rc.getBoundingClientRect(); if (r.width && (rc.width !== (r.width * Rn.getPixelRatio() | 0) || rc.height !== (r.height * Rn.getPixelRatio() | 0))) { Rn.setSize(r.width | 0, r.height | 0, false); cam.aspect = r.width / r.height; cam.updateProjectionMatrix(); }
  setCam(); Rn.render(sc, cam); requestAnimationFrame(loop);
}
addEventListener("resize", drawPlan);
window.__ed = { get builds() { return builds; }, w2s, s2w, get view() { return view; }, project: (x, y, z) => { const v = new T.Vector3(x, y, z).project(cam), r = rc.getBoundingClientRect(); return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height]; }, atS, wallPath, get S() { return S; }, get R() { return R; }, get closed() { return closed; }, get sel() { return sel; }, get tool() { return tool; }, loadPreset, setTool, doBuild, cam, tgt, get ghostVisible() { return ghost.visible; }, setView: (a, e, d) => { az = a; el_ = e; dist = d; }, setSel: s => { sel = s; drawAll(); sidebar(); }, pick3, slotAt, ghostFor };
(async () => {
  try { const s = localStorage.getItem("cs2ed"); if (s) { restore(s); } } catch { }
  hist.u.push(snap()); drawTools(); await loadCatalog(); hintText(); stats(); sidebar(); welcome(); guide();
  if (closed) { fitView(); showReal(); requestBuild(); }
  setTimeout(drawPlan, 50); requestAnimationFrame(loop);
})();
