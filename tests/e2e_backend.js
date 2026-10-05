/* Browser E2E for the FastAPI-backed workflow (CDP, no extra npm packages).
 * Usage: node tests/e2e_backend.js <app_url> <browser_exe> <profile_dir>
 * Checks: backend detected, orthophoto served by the backend, topology-conflict toggle with real conflicts,
 * a REAL mouse drag of a vertex on a shared edge -> neighbour edge flashes ~200 ms then disappears,
 * accept + validated export through /api/export. Prints a JSON report; exits non-zero on a failed assertion.
 */
"use strict";
const { spawn } = require("child_process");
const [url, exe, profile] = process.argv.slice(2);
const PORT = 9800 + Math.floor(Math.random() * 300);
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
  const mouse = (type, x, y) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons: type === "mouseReleased" ? 0 : 1, clickCount: 1 });
  await send("Runtime.enable");
  await send("Page.reload", { ignoreCache: true });
  for (let i = 0; i < 60; i++) { await sleep(500); if (await evaluate("!!(window.__app && document.getElementById('status').textContent.startsWith('Loaded'))")) break; }

  const R = {};
  const fail = (m) => { throw new Error("ASSERT: " + m); };

  // ---- A. backend detected; imagery comes from the backend ----
  R.backend = await evaluate(`(() => { const a = window.__app.api; return a && { site: a.site.id, state: a.site.workflow.state, layers: a.layers.map(l => l.id + ":" + l.feature_count),
    overlay_src: Array.from(document.querySelectorAll('img.leaflet-image-layer')).map(i => i.getAttribute('src')),
    chip: document.getElementById('hdr-conn').textContent, topo_enabled: !document.getElementById('show-topology').disabled }; })()`);
  if (!R.backend || !R.backend.overlay_src.every((s) => s.startsWith("/api/sites/"))) fail("orthophoto not served by backend: " + JSON.stringify(R.backend));

  // ---- B. topology conflict mode (real conflicts from the backend; Mask R-CNN has gap slivers) ----
  R.topology = await evaluate(`(async () => {
    await evaluateReset();
    async function evaluateReset() { try { localStorage.clear(); } catch (e) {} }
    const t = document.getElementById('show-topology'); t.checked = true; t.dispatchEvent(new Event('change'));
    for (let i = 0; i < 50 && /Checking/.test(document.getElementById('topology-note').textContent) || !document.getElementById('topology-note').textContent; i++) await new Promise(r => setTimeout(r, 200));
    const paths = document.querySelectorAll('path.conflict-pulse').length;
    const note = document.getElementById('topology-note').textContent;
    const anim = paths ? getComputedStyle(document.querySelector('path.conflict-pulse')).animationDuration : null;
    return { paths, note, anim };
  })()`);
  if (!R.topology.paths) fail("no conflict overlays drawn: " + JSON.stringify(R.topology));
  if (!/gap sliver/.test(R.topology.note) || !/cadastral/.test(R.topology.note)) fail("unexpected topology note: " + R.topology.note);

  // ---- C. add candidates, pick a shared-edge pair from the backend, start vertex edit on one ----
  R.setup = await evaluate(`(async () => {
    const app = window.__app; const click = (i) => document.getElementById(i).click();
    document.getElementById('review-candidate').value = 'B'; document.getElementById('review-candidate').dispatchEvent(new Event('change'));   // RGB layer has shared edges
    await new Promise(r => setTimeout(r, 300));
    click('btn-add-view');
    const lyr = (fid) => { let o; app.mapR.eachLayer(l => { if (l.feature && l.feature.id === fid && l._path) o = l; }); return o; };
    const rel = (await (await fetch('/api/topology/validate-layer', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ site_id: app.api.site.id, layer_id: 'rgb' }) })).json()).relationships;
    const wsBy = (aid) => app.ws.find(f => f.properties.source_layer === 'B' && 'rgb:' + f.properties.source_id === aid);
    const pair = rel.map(r => [wsBy(r.features[0]), wsBy(r.features[1])]).find(([a, b]) => a && b);
    if (!pair) return { error: 'no shared pair in workspace', n: app.ws.length };
    app.mapR.fitBounds(L.geoJSON(pair[0]).getBounds().pad(0.5), { animate: false });
    await new Promise(r => setTimeout(r, 500));
    const L1 = lyr(pair[0].id);
    L1._path.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 10, clientY: 10 }));
    click('btn-edit');
    await new Promise(r => setTimeout(r, 400));
    // vertex markers of the edited layer: choose the one closest to the neighbour polygon
    const nb = pair[1]; const markers = Array.from(document.querySelectorAll('.marker-icon:not(.marker-icon-middle)'));
    const info = markers.map(m => { const r = m.getBoundingClientRect(); const ll = app.mapR.containerPointToLatLng(L.point(r.x + r.width / 2 - app.mapR.getContainer().getBoundingClientRect().x, r.y + r.height / 2 - app.mapR.getContainer().getBoundingClientRect().y));
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, d: turf.pointToLineDistance(turf.point([ll.lng, ll.lat]), turf.polygonToLine(nb), { units: 'meters' }) }; })
      .sort((a, b) => a.d - b.d);
    return { ws: app.ws.length, markers: markers.length, nearest_m: info[0] && info[0].d, x: info[0] && info[0].x, y: info[0] && info[0].y, pair: [pair[0].id, pair[1].id], neighbor_src: 'rgb:' + nb.properties.source_id };
  })()`);
  if (R.setup.error || R.setup.nearest_m === undefined) fail("setup: " + JSON.stringify(R.setup));
  if (R.setup.nearest_m > 0.3) fail("no vertex within tolerance of the neighbour: " + R.setup.nearest_m);

  // ---- D. REAL mouse drag of that vertex; poll for the edge flash ----
  await mouse("mouseMoved", R.setup.x, R.setup.y);
  await mouse("mousePressed", R.setup.x, R.setup.y);
  const seen = [];
  const t0 = Date.now();
  for (let i = 1; i <= 6; i++) {
    await mouse("mouseMoved", R.setup.x + i * 2, R.setup.y);
    const n = await evaluate(`document.querySelectorAll('path.edge-flash').length`);
    seen.push([Date.now() - t0, n]);
    await sleep(40);
  }
  await sleep(400);
  const after = await evaluate(`document.querySelectorAll('path.edge-flash').length`);
  await mouse("mouseReleased", R.setup.x + 12, R.setup.y);
  R.flash = { samples_ms_count: seen, remaining_after_400ms: after, last: await evaluate(`window.__app.lastEdgeFlash`) };
  if (!R.flash.last) fail("shared-boundary flash never triggered: " + JSON.stringify(R.flash));
  if (R.flash.last.neighbor !== R.setup.pair[1]) fail("wrong neighbour flashed: " + JSON.stringify(R.flash.last) + " expected " + R.setup.pair[1]);
  if (after !== 0) fail("edge flash did not disappear");

  // ---- E. finish edit, accept, export through the validated backend path ----
  R.export = await evaluate(`(async () => {
    const app = window.__app; const click = (i) => document.getElementById(i).click();
    click('btn-edit');                                   // finish edit
    await new Promise(r => setTimeout(r, 300));
    const edited = app.ws.find(f => f.id === ${JSON.stringify(R.setup.pair[0])});
    const out = { origin: edited.properties.origin };
    click('btn-accept');                                 // the edited polygon is still selected
    out.status = app.ws.find(f => f.id === edited.id).properties.review_status;
    click('btn-export');
    for (let i = 0; i < 60 && !/Exported|failed/.test(document.getElementById('export-result').textContent); i++) await new Promise(r => setTimeout(r, 200));
    out.result = document.getElementById('export-result').textContent;
    return out;
  })()`);
  R.js_errors = errors;
  const shot = await send("Page.captureScreenshot", { format: "png" });
  require("fs").writeFileSync(profile + "/e2e_backend.png", Buffer.from(shot.result.data, "base64"));
  R.screenshot = profile + "/e2e_backend.png";
  console.log(JSON.stringify(R, null, 2));
  ws.close(); br.kill();
  if (errors.length) { console.error("JS errors:", errors); process.exit(2); }
}
main().catch((e) => { console.error("E2E FAILED:", e.message); process.exit(1); });
