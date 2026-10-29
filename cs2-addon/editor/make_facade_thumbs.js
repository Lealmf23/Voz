// Genera editor/thumbs/facade_<id>.png con capturas de la vista 3D de cada tipo de fachada (motor andando).
// NODE_PATH=<node_modules con playwright> EDITOR_URL=http://127.0.0.1:8765/ node make_facade_thumbs.js
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const b = await chromium.launch({ args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'] });
  const p = await b.newPage({ viewport: { width: 1500, height: 900 } });
  await p.goto(process.env.EDITOR_URL || 'http://127.0.0.1:8765/'); await p.evaluate(() => localStorage.clear()); await p.reload(); await p.waitForTimeout(700);
  await p.click('text=Rectángulo'); await p.waitForFunction(() => window.__ed.R);
  await p.click('#bRoof').catch(() => { });  // el techo se queda visible
  await p.click('#bRoof').catch(() => { });
  const ids = await p.evaluate(() => __ed.CAT.facades.map(f => f.id));
  for (const id of ids) {
    const n = await p.evaluate(() => __ed.builds);
    await p.evaluate(i => __ed.applyFacade(__ed.CAT.facades.find(f => f.id === i)), id);
    await p.waitForFunction(n => __ed.builds > n, n, { timeout: 120000 }); await p.waitForTimeout(500);
    await p.evaluate(() => { const S = __ed.S; __ed.setSel({ edge: null, sec: 0, slot: null, vert: null }); });
    await p.evaluate(h => { __ed.setView(-0.9, 0.38, Math.max(55, h * 2.2 + 30)); }, await p.evaluate(() => __ed.S.floors * __ed.S.floor_h));
    await p.evaluate(h => { __ed.tgt.z = h / 2; }, await p.evaluate(() => __ed.S.floors * __ed.S.floor_h)); await p.waitForTimeout(300);
    const box = await p.locator('#v3').boundingBox();
    await p.screenshot({ path: path.join(__dirname, 'thumbs', 'facade_' + id + '.png'), clip: { x: box.x + box.width * .12, y: box.y + box.height * .12, width: box.width * .76, height: box.height * .76 } });
    console.log('ok', id);
  }
  await b.close();
})();
