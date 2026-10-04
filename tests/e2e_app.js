/* End-to-end UI check for the review app via the Chrome DevTools Protocol (no extra npm packages).
 * Usage: node tests/e2e_app.js <app_url> <browser_exe> <profile_dir>
 * Drives the real UI buttons/DOM: add candidates, select (click / shift-click), accept, reject, merge,
 * split (Geoman line draw), edit vertices, draw new, export via /api/export. Prints a JSON report.
 */
"use strict";
const { spawn } = require("child_process");
const [url, exe, profile] = process.argv.slice(2);
const PORT = 9300 + Math.floor(Math.random() * 500);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const br = spawn(exe, ["--headless=new", "--disable-gpu", `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
    "--window-size=1600,1000", "--no-first-run", url], { stdio: "ignore" });
  let target;
  for (let i = 0; i < 60 && !target; i++) {
    await sleep(500);
    try { target = (await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()).find((t) => t.type === "page" && t.url.startsWith(url)); } catch (e) { /* not up yet */ }
  }
  if (!target) { br.kill(); throw new Error("browser did not start"); }
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
  let id = 0; const pending = new Map(); const errors = [];
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && pending.has(d.id)) { pending.get(d.id)(d); pending.delete(d.id); }
    if (d.method === "Runtime.exceptionThrown") errors.push(d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text);
    if (d.method === "Runtime.consoleAPICalled" && d.params.type === "error") errors.push(d.params.args.map((a) => a.value || a.description).join(" "));
  };
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; pending.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evaluate = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error(r.result.exceptionDetails.exception?.description || "eval error");
    return r.result.result.value;
  };
  await send("Runtime.enable");
  await send("Page.reload", { ignoreCache: true });
  for (let i = 0; i < 60; i++) { await sleep(500); if (await evaluate("!!(window.__app && document.getElementById('status').textContent.startsWith('Loaded'))")) break; }

  const report = await evaluate(`(async () => {
    const R = {}; const app = window.__app; const st = () => document.getElementById('status').textContent;
    const click = (id) => document.getElementById(id).click();
    const wsLayers = () => { const o = []; app.mapR.eachLayer(l => { if (l.feature && String(l.feature.id || '').startsWith('ws-') && l._path) o.push(l); }); return o; };
    const pathClick = (l, shift) => l._path.dispatchEvent(new MouseEvent('click', { bubbles: true, shiftKey: !!shift, clientX: 10, clientY: 10 }));
    const find = (id) => app.ws.find(f => f.id === id);
    try { localStorage.clear(); } catch (e) {}
    const dis = (id) => document.getElementById(id).disabled;
    R.disabled_initial = { accept: dis('btn-accept'), merge: dis('btn-merge'), split: dis('btn-split'), export: dis('btn-export'), add_view: dis('btn-add-view') };
    // 1. add all Mask R-CNN candidates in view
    click('btn-add-view'); R.added = app.ws.length; R.status_add = st();
    // 2. select one, accept
    let ls = wsLayers(); pathClick(ls[0]); const a = ls[0].feature.id;
    R.disabled_one_selected = { accept: dis('btn-accept'), merge: dis('btn-merge'), split: dis('btn-split'), edit: dis('btn-edit') };
    R.inspector_one = document.getElementById('info').textContent.includes('Workspace footprint');
    click('btn-accept');
    R.accept = find(a).properties.review_status;
    // 3. reject another
    ls = wsLayers(); const rj = ls.find(l => l.feature.id !== a); pathClick(rj); click('btn-reject'); R.reject = find(rj.feature.id).properties.review_status;
    // 4. merge two touching polygons (shift-click)
    const polys = app.ws.filter(f => f.properties.origin === 'ai_copy');
    let pair = null;
    for (let i = 0; i < polys.length && !pair; i++) for (let j = i + 1; j < polys.length; j++) {
      if (turf.booleanIntersects(turf.buffer(polys[i], 0.3, {units: 'meters'}), polys[j])) { pair = [polys[i].id, polys[j].id]; break; } }
    pathClick(wsLayers().find(l => l.feature.id === pair[0]));
    pathClick(wsLayers().find(l => l.feature.id === pair[1]), true);  // re-query: the workspace re-renders after each click
    click('btn-merge');
    const merged = app.ws.find(f => f.properties.origin === 'human_merged');
    R.merge = merged ? { parents: merged.properties.parents.length, gone: !find(pair[0]) && !find(pair[1]), status: st() } : st();
    // 5. split the largest remaining AI copy with a Geoman line across its bbox centre
    const big = app.ws.filter(f => f.properties.origin === 'ai_copy').sort((x, y) => turf.area(y) - turf.area(x))[0];
    ls = wsLayers(); pathClick(ls.find(l => l.feature.id === big.id)); click('btn-split');
    R.split_banner = document.getElementById('mode-banner').hidden ? null : document.getElementById('mode-banner').textContent;
    R.split_blocks_accept = dis('btn-accept');
    const [w, s, e, n] = turf.bbox(big); const cy = (s + n) / 2, pad = (e - w) * 0.2;
    app.mapR.fire('click', { latlng: L.latLng(cy, w - pad) });
    app.mapR.fire('click', { latlng: L.latLng(cy, e + pad) });
    app.mapR.pm.Draw.Line._finishShape();
    await new Promise(r => setTimeout(r, 200));
    R.banner_after_split_hidden = document.getElementById('mode-banner').hidden;
    const parts = app.ws.filter(f => f.properties.origin === 'human_split');
    R.split = { parts: parts.length, parent_removed: !find(big.id), status: st() };
    // 6. edit vertices of one AI copy (move first vertex slightly)
    const ed = app.ws.find(f => f.properties.origin === 'ai_copy');
    ls = wsLayers(); const EL = ls.find(l => l.feature.id === ed.id); pathClick(EL); click('btn-edit');
    const ll = EL.getLatLngs(); const ring = Array.isArray(ll[0]) ? ll[0] : ll; ring[1] = L.latLng(ring[1].lat + 0.000005, ring[1].lng);
    EL.setLatLngs(ll); click('btn-edit');
    R.edit = { origin: find(ed.id).properties.origin, status: st() };
    // 7. draw a new polygon
    click('btn-draw');
    const c = app.mapR.getCenter(); const d = 0.00003;
    [[0,0],[0,d],[d,d],[d,0]].forEach(([dy,dx]) => app.mapR.fire('click', { latlng: L.latLng(c.lat + dy, c.lng + dx) }));
    app.mapR.pm.Draw.Polygon._finishShape();
    await new Promise(r => setTimeout(r, 200));
    R.drawn = app.ws.filter(f => f.properties.origin === 'human_drawn').length;
    // 8. export accepted (server)
    click('btn-export');
    for (let i = 0; i < 50 && !/Exported|failed/.test(document.getElementById('export-result').textContent); i++) await new Promise(r => setTimeout(r, 200));
    R.export = document.getElementById('export-result').textContent;
    R.counts_by_origin = app.ws.reduce((o, f) => (o[f.properties.origin] = (o[f.properties.origin] || 0) + 1, o), {});
    R.counts_by_status = app.ws.reduce((o, f) => (o[f.properties.review_status] = (o[f.properties.review_status] || 0) + 1, o), {});
    // 9. AI layers untouched
    const src = await (await fetch('/data/layer_C.geojson')).json();
    R.ai_layer_C_count = src.features.length;
    R.metric_banner = document.getElementById('metric-banner').textContent;
    app.toggleEval(true);
    R.eval_drawer_visible = !document.getElementById('eval-drawer').hidden;
    R.metric_cards = Array.from(document.querySelectorAll('#metric-cards .card')).map(c => c.textContent.replace(/\\s+/g, ' ').trim());
    document.getElementById('metric-split').value = 'development_V1-V4'; document.getElementById('metric-split').dispatchEvent(new Event('change'));
    R.metric_banner_dev = document.getElementById('metric-banner').textContent;
    return R;
  })()`);
  report.js_errors = errors;
  const shot = await send("Page.captureScreenshot", { format: "png" });
  require("fs").writeFileSync(profile + "/e2e_final.png", Buffer.from(shot.result.data, "base64"));
  report.screenshot = profile + "/e2e_final.png";
  console.log(JSON.stringify(report, null, 2));
  ws.close(); br.kill();
}
main().catch((e) => { console.error("E2E FAILED:", e.message); process.exit(1); });
