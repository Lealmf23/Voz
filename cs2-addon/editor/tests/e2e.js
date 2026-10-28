// Prueba de punta a punta del editor: dibujar, mover, curvar, colocar/borrar aberturas, deshacer y exportar.
// Uso: levantar el motor (python3 engine.py) y correr:  NODE_PATH=<carpeta con playwright> node tests/e2e.js [carpeta_capturas]
const { chromium } = require('playwright');
const URL = process.env.EDITOR_URL || 'http://127.0.0.1:8765/';
const out = process.argv[2] || '/tmp';
let fails = 0;
const ok = (c, m) => { console.log((c ? 'OK   ' : 'FALLA') + ' ' + m); if (!c) fails++; };
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
  const p = await b.newPage({ viewport: { width: 1500, height: 860 } });
  const errs = []; p.on('pageerror', e => errs.push(e.message)); p.on('console', m => { if (m.type() === 'error' && !/404/.test(m.text())) errs.push(m.text()); });
  await p.goto(URL); await p.evaluate(() => localStorage.clear()); await p.reload(); await p.waitForTimeout(600);
  const ed = f => p.evaluate(f);
  let seen = 0;
  const waitR = async () => { await p.waitForFunction(n => window.__ed.builds > n, seen, { timeout: 90000 }); seen = await p.evaluate(() => window.__ed.builds); await p.waitForTimeout(150); };
  ok(await ed(() => document.getElementById('engtxt').textContent.includes('conectado')), 'el motor está conectado');

  // 1) dibujar una planta con el mouse
  await p.keyboard.press('d'); await p.waitForTimeout(200);
  const click = async (wx, wy) => { const [sx, sy] = await p.evaluate(([x, y]) => window.__ed.w2s(x, y), [wx, wy]); const bx = await p.locator('#plan').boundingBox(); await p.mouse.move(bx.x + sx, bx.y + sy); await p.mouse.click(bx.x + sx, bx.y + sy); };
  for (const q of [[-8, -5], [8, -5], [8, 5], [-8, 5], [-8, -5]]) await click(...q);
  await waitR();
  ok(await ed(() => __ed.closed && __ed.S.verts.length === 4), 'planta dibujada y cerrada con 4 esquinas');
  ok(await ed(() => __ed.R.tris > 500), 'Blender devolvió una malla (' + await ed(() => __ed.R.tris) + ' triángulos)');

  // 2) mover una esquina arrastrando
  const bx = await p.locator('#plan').boundingBox();
  const [vx, vy] = await ed(() => window.__ed.w2s(8, 5)); await p.mouse.move(bx.x + vx, bx.y + vy); await p.mouse.down();
  const [nx, ny] = await ed(() => window.__ed.w2s(10, 7)); await p.mouse.move(bx.x + nx, bx.y + ny, { steps: 5 }); await p.mouse.up(); await waitR();
  ok(await ed(() => __ed.S.verts.some(v => v[0] === 10 && v[1] === 7)), 'la esquina se movió a (10, 7)');

  // 3) curvar una pared desde el panel
  await ed(() => __ed.setSel({ edge: 1, slot: null, vert: null }));
  const inp = p.locator('#side input[type=number]').nth(0);   // «Curvatura»: primer campo de la pared elegida
  await inp.fill('3'); await inp.dispatchEvent('change'); await waitR();
  ok(await ed(() => __ed.R.walls[1].facets.length > 3), 'la pared curva quedó hecha de facetas (' + await ed(() => __ed.R.walls[1].facets.length) + ')');

  // 4) borrar una ventana, reemplazarla por una puerta y deshacer
  await ed(() => __ed.setView(-1.2, .35, 55)); await p.waitForTimeout(300);
  const info = await ed(() => { const w = __ed.R.walls[0]; const sl = w.slots.find(s => s.f === 1 && !s.deleted); const q = __ed.atS(0, (sl.a0 + sl.a1) / 2); return { x: q.x + __ed.R.center[0], y: q.y + __ed.R.center[1], cx: __ed.R.center[0], cy: __ed.R.center[1], z: (sl.z0 + sl.z1) / 2, id: sl.id }; });
  const cnt = () => ed(() => __ed.R.walls[0].slots.filter(s => !s.deleted).length);
  const n0 = await cnt();
  // el overlay vive en coordenadas de la planta; la malla, centrada: proyectamos el punto de la planta
  const [sx, sy] = await p.evaluate(i => window.__ed.project(i.x, i.y, i.z), { x: info.x - info.cx, y: info.y - info.cy, z: info.z });
  await p.mouse.click(sx, sy); await p.waitForTimeout(300);
  ok(await ed(() => __ed.sel.slot) === info.id, 'clic en la ventana la elige (' + info.id + ')');
  await p.keyboard.press('Delete'); await waitR(); await p.waitForTimeout(200);
  ok(await cnt() === n0 - 1, 'Supr la borra');
  await p.keyboard.press('p'); await p.mouse.move(sx, sy); await p.waitForTimeout(250);
  ok(await ed(() => __ed.ghostVisible), 'aparece el fantasma de la puerta');
  await p.mouse.click(sx, sy); await waitR();
  ok(await ed(() => __ed.S.edges[0].add.some(a => a.kind === 'door')), 'la puerta quedó agregada a la pared');
  await p.keyboard.press('v'); await p.keyboard.press('Control+z'); await waitR();
  ok(await cnt() === n0 - 1, 'deshacer quita la puerta');
  await p.keyboard.press('Control+z'); await waitR();
  ok(await cnt() === n0, 'deshacer devuelve la ventana');
  await p.screenshot({ path: out + '/e2e_final.png' });

  // 5) retranqueo: agregar uno desde el panel y editar una ventana del tramo superior
  await ed(() => __ed.loadPreset('box')); await waitR();
  await p.click('#side .tabs >> text=Retranqueo'); await p.click('text=+ Agregar retranqueo'); await waitR();
  ok(await ed(() => __ed.R.levels.length === 2 && __ed.R.walls.some(w => w.sec === 1)), 'el retranqueo crea un tramo superior con sus paredes');
  await ed(() => __ed.setView(-1.2, .5, 60)); await p.waitForTimeout(300);
  const up = await ed(() => { const w = __ed.R.walls.find(w => w.sec === 1 && w.edge === 0); const sl = w.slots.find(s => !s.deleted && s.kind === 'win'); const q = __ed.atS(0, (sl.a0 + sl.a1) / 2, __ed.wallPath(0, 1), 1); return { x: q.x - __ed.R.center[0], y: q.y - __ed.R.center[1], z: (sl.z0 + sl.z1) / 2, id: sl.id }; });
  const [ux, uy] = await p.evaluate(i => window.__ed.project(i.x, i.y, i.z), up);
  await p.mouse.click(ux, uy); await p.waitForTimeout(300);
  ok(await ed(() => __ed.sel.sec) === 1 && await ed(() => __ed.sel.slot) === up.id, 'se elige una ventana del tramo superior');
  const cu = () => ed(() => __ed.R.walls.find(w => w.sec === 1 && w.edge === 0).slots.filter(s => !s.deleted).length);
  const u0 = await cu(); await p.keyboard.press('Delete'); await waitR();
  ok(await cu() === u0 - 1, 'se puede borrar una ventana del tramo superior');
  await p.screenshot({ path: out + '/e2e_setback.png' });

  // 6) opciones del add-on: una general y una solo de la pared elegida
  await p.click('#side .tabs >> text=Opciones');
  const inp2 = p.locator('#side details input[type=number]').first(); await inp2.fill('2.2'); await inp2.dispatchEvent('change'); await waitR();
  ok(await ed(() => Object.values(__ed.S.props).includes(2.2)), 'una opción general del add-on se guarda en la receta');
  await ed(() => __ed.setSel({ edge: 0, sec: 0, slot: null, vert: null })); await p.click('#side .tabs >> text=Selección');
  const inp3 = p.locator('#side .sec:has-text("Solo esta pared") details input[type=number]').first(); await inp3.fill('3'); await inp3.dispatchEvent('change'); await waitR();
  ok(await ed(() => __ed.S.edges[0].wp && Object.values(__ed.S.edges[0].wp).includes(3)), 'una opción propia de la pared se guarda en esa pared');

  // 7) editor de estilos: forma de arco en una ventana propia
  await p.click('#side .tabs >> text=Estilo'); await p.click('text=Personalizar este estilo… >> nth=0'); await p.waitForSelector('#sted.show');
  await p.locator('#sted select').first().selectOption('ARCH'); await waitR();
  ok(await ed(() => Object.values(__ed.S.custom).some(c => c.params.shape === 'ARCH')), 'el editor de estilos crea un estilo propio y se aplica');
  await p.click('#side .tabs >> text=Proyecto');
  ok(await p.locator('#side details').count() > 0, 'la pestaña Proyecto muestra opciones de juego y texturas');

  // 8) exportar (con el retranqueo)
  const dir = '/tmp/cs2_e2e_' + Date.now();
  const r = await p.evaluate(async d => { const rec = { ...JSON.parse(JSON.stringify({ verts: __ed.S.verts, edges: __ed.S.edges, props: __ed.S.props, custom_styles: __ed.S.custom, floors: __ed.S.floors, floor_h: __ed.S.floor_h, roof: __ed.S.roof, props: __ed.S.props })), out: d, name: 'E2E' }; return await (await fetch('/export', { method: 'POST', body: JSON.stringify(rec) })).json(); }, dir);
  ok(!r.error && r.tris && r.tris[2] <= 500, 'exportación OK, triángulos ' + JSON.stringify(r.tris) + (r.error ? ' ' + r.error.slice(0, 200) : ''));
  ok(errs.length === 0, 'sin errores en la consola del navegador' + (errs.length ? ': ' + errs.slice(0, 3).join(' | ') : ''));
  await b.close(); process.exit(fails ? 1 : 0);
})();
