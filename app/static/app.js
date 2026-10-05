/* Footprint Intelligence — review app.
 * AI layers are read-only; all human work lives in the workspace array (EditOps copies with provenance).
 * FastAPI is the source of truth; the legacy data-bundle server retains its local fallback.
 */
(async function () {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const STORE_KEY = "bfr.workspace.v1";
  const FLAG_TEXT = {
    small_roof: "Small roof — small roofs are often missed or only partly detected",
    possible_merged_buildings: "Large polygon — may contain several merged buildings",
    touching_neighbour: "Touches another polygon — building boundary uncertain",
    possible_over_split: "Touches another instance — a single roof may be over-split",
    low_score: "Low model score",
  };
  const COLORS = { suggested: "#f5b544", accepted: "#4ade80", rejected: "#f87171", human: "#60a5fa", selected: "#22d3ee", cue: "#fb7185" };
  const STATUS_STYLE = {
    suggested: { color: COLORS.suggested, weight: 2.5, dashArray: "6 4", fillOpacity: 0.12, fillColor: COLORS.suggested },
    accepted: { color: COLORS.accepted, weight: 2.5, fillOpacity: 0.22, fillColor: COLORS.accepted },
    rejected: { color: COLORS.rejected, weight: 2, dashArray: "2 5", fillOpacity: 0.06, fillColor: COLORS.rejected },
  };
  const ORIGIN_LABEL = { ai_copy: "AI copy (unchanged geometry)", human_edited: "Human-edited", human_merged: "Human-merged",
    human_split: "Human-split", human_drawn: "Human-drawn" };

  // ---------- feedback ----------
  let toastTimer = null;
  function status(msg, kind) {
    const t = $("status");
    t.textContent = msg;
    t.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.add("hide"), kind === "error" ? 9000 : 4500);
  }
  function chip(id, text, cls) {
    const c = $(id); c.className = "chip" + (cls ? " " + cls : ""); c.title = text; c.setAttribute("aria-label", text);
    c.innerHTML = `<span class="dot"></span><span class="lbl">${esc(text)}</span>`;
  }

  async function getJSON(url) {
    const r = await fetch(url);
    if (!r.ok) throw new Error(url + " → HTTP " + r.status);
    return r.json();
  }

  // ---------- load ----------
  chip("hdr-conn", "Loading data…", "busy");
  let manifest, ortho, windows;
  let api = null;
  const layersData = {};
  try {
    const setup = await window.siteSetup;
    if (setup && !setup.siteId) return;
    if (setup) {
      const root = "/api/sites/" + encodeURIComponent(setup.siteId);
      const site = await getJSON(root);
      api = { site, layers: (await getJSON(root + "/layers")).layers };
      manifest = await getJSON(root + "/manifest");
      windows = await getJSON(root + "/windows");
      ortho = manifest.ortho;
      for (const k of Object.keys(manifest.layers)) layersData[k] = { type: "FeatureCollection", features: [] };
    } else {
      manifest = await getJSON("/data/manifest.json");
      ortho = await getJSON("/data/ortho.json");
      windows = await getJSON("/data/windows.geojson");
      for (const k of Object.keys(manifest.layers)) layersData[k] = await getJSON("/data/" + manifest.layers[k].file);
    }
  } catch (e) {
    chip("hdr-conn", "Data unavailable", "err");
    status("Failed to load data: " + e.message + " — run scripts/build_app_data.py and restart the server.", "error");
    $("project-info").innerHTML = `<dt>Status</dt><dd>Data not loaded</dd>`;
    return;
  }
  const orthoUrl = api ? api.site.imagery_info.display_url : "/data/ortho_3857.png";
  const orthoBounds = api ? api.site.imagery_info.display_bounds_latlon : ortho.bounds_latlon;
  const layerId = (k) => { const l = api && api.layers.find((x) => x.legacy_key === k); return l ? l.id : null; };
  fetch("/api/health").then((r) => r.json()).then((h) => chip("hdr-conn", h.ok ? "Server connected · data loaded" + (api ? " · topology API" : " · basic server") : "Server not ready", h.ok ? "ok" : "err"))
    .catch(() => chip("hdr-conn", "Server unreachable — export disabled", "err"));

  const keys = Object.keys(manifest.layers);
  const img = manifest.imagery || {};
  $("hdr-context").textContent = `${api ? api.site.name : "La Paz, Bolivia"} · ${img.file || "orthophoto"}${img.native_res_m ? " · " + img.native_res_m * 100 + " cm GSD" : ""} · ${manifest.analysis_crs}`;
  $("btn-export-top").title = "Export reviewed features in " + manifest.analysis_crs + " and WGS84";
  const kv = [
    ["Site", api ? api.site.name : "La Paz, Bolivia"],
    ["Imagery", img.file || "n/a"],
    ["Source", img.software ? "Drone orthomosaic (" + img.software + ")" : "Drone orthomosaic"],
    ["Native GSD", img.native_res_m ? img.native_res_m + " m" : "n/a"],
    ["Extent", img.extent_m ? `${img.extent_m[0]} × ${img.extent_m[1]} m` : "n/a"],
    ["Analysis CRS", manifest.analysis_crs],
    ["Layers", keys.map((k) => `${k} ${manifest.layers[k].count}`).join(" · ")],
    ["Accuracy", manifest.quality?.absolute_accuracy || "See source evaluation"],
    ["Quality flags", (manifest.quality?.flags || []).join(", ") || "No additional flags recorded"],
  ];
  $("project-info").innerHTML = kv.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("");

  // ---------- maps ----------
  function makeMap(id) {
    const host = $(id);
    const veil = document.createElement("div");
    veil.className = "loading-veil"; veil.textContent = "Loading orthophoto…";
    host.parentElement.appendChild(veil);
    const m = L.map(id, { maxZoom: 24, minZoom: 1, zoomSnap: 0.25, zoomControl: false });
    L.control.zoom({ position: "bottomright" }).addTo(m);
    const ov = api?.site.imagery_info.tile_url
      ? L.tileLayer(api.site.imagery_info.tile_url, { maxZoom: 24, bounds: orthoBounds, noWrap: true }).addTo(m)
      : L.imageOverlay(orthoUrl, orthoBounds).addTo(m);
    ov.on("load", () => veil.remove());
    ov.on("error", () => { veil.textContent = "Orthophoto failed to load"; });
    m.fitBounds(orthoBounds);
    L.control.scale({ metric: true, imperial: false, position: "bottomright" }).addTo(m);
    return m;
  }
  const mapL = makeMap("map-left");
  const mapR = makeMap("map-right");
  let syncing = false;
  function sync(a, b) {
    a.on("move zoom", () => {
      if (syncing) return;
      syncing = true;
      b.setView(a.getCenter(), a.getZoom(), { animate: false });
      syncing = false;
    });
  }
  sync(mapL, mapR);
  sync(mapR, mapL);
  const resizeObs = new ResizeObserver(() => { mapL.invalidateSize(); mapR.invalidateSize(); });
  resizeObs.observe($("maps"));

  // ---------- candidate (AI) layers ----------
  const showFlags = () => $("show-flags").checked;
  function candidateStyle(key) {
    return (f) => {
      const cue = showFlags() && f.properties.flags;
      const c = manifest.layers[key].color;
      return { color: cue ? COLORS.cue : c, weight: cue ? 2.5 : 1.8, fillColor: c, fillOpacity: 0.16, dashArray: cue ? "4 3" : null };
    };
  }
  let selectedCandidate = null, selectedCandidateLayer = null;
  function highlightCandidate(lyr, f) {
    if (selectedCandidateLayer && rightCand) rightCand.resetStyle(selectedCandidateLayer);
    selectedCandidate = f; selectedCandidateLayer = lyr;
    if (lyr) { lyr.setStyle({ color: COLORS.selected, weight: 3.5, dashArray: null, fillOpacity: 0.25 }); lyr.bringToFront(); if (wsLayer) wsLayer.bringToFront(); }
    updateButtons();
  }
  function candidateLayer(key, map, interactive) {
    const g = L.geoJSON(layersData[key], {
      style: candidateStyle(key),
      onEachFeature: (f, lyr) => {
        lyr.on("click", (ev) => {
          L.DomEvent.stopPropagation(ev);
          if (interactive) highlightCandidate(lyr, f);
          showInfo(f, "ai");
        });
      },
    });
    return g;
  }
  let leftLayer = null, rightCand = null, windowsLayer = null;
  $("compare-layers").innerHTML = keys.map((k, i) => {
    const L_ = manifest.layers[k];
    const cand = k === "C" ? '<span class="tag cand">candidate</span>' : "";
    return `<label class="layer-item${i === 0 ? " active" : ""}"><input type="radio" name="cmp" value="${k}" ${i === 0 ? "checked" : ""}>
      <span class="swatch" style="border-color:${L_.color}"></span>
      <span class="layer-name">${k} · ${esc(shortName(k))}${cand}<small>${esc(L_.name)}</small></span>
      <span class="layer-count">${L_.count}</span></label>`;
  }).join("");
  function shortName(k) { return { A: "WHU baseline", B: "RGB post-processing", C: "Mask R-CNN 0.3 m" }[k] || manifest.layers[k].name; }
  $("review-candidate").innerHTML =
    keys.map((k) => `<option value="${k}" ${k === "C" ? "selected" : ""}>${k} · ${esc(shortName(k))}</option>`).join("") + `<option value="">None (workspace only)</option>`;
  function renderLeft() {
    if (leftLayer) mapL.removeLayer(leftLayer);
    const k = document.querySelector('input[name="cmp"]:checked')?.value;
    if (!k) { $("label-left").textContent = "Imagery · no AI layers yet"; return; }
    document.querySelectorAll(".layer-item").forEach((el) => el.classList.toggle("active", el.querySelector("input").value === k));
    leftLayer = candidateLayer(k, mapL, false).addTo(mapL);
    $("label-left").textContent = `${k} · ${shortName(k)}`;
    renderMapLegends(); scheduleTopology();
  }
  function renderRightCandidate() {
    if (rightCand) mapR.removeLayer(rightCand);
    rightCand = null; selectedCandidate = null; selectedCandidateLayer = null;
    const k = $("review-candidate").value;
    if (k) rightCand = candidateLayer(k, mapR, true).addTo(mapR);
    if (wsLayer) wsLayer.bringToFront();
    $("label-right").textContent = k ? `candidates: ${k} · ${shortName(k)}` : "workspace only";
    renderMapLegends(); updateButtons(); updateCounts(); scheduleTopology();
  }
  function renderWindows() {
    if (windowsLayer) mapL.removeLayer(windowsLayer);
    windowsLayer = null;
    if ($("show-windows").checked) {
      windowsLayer = L.geoJSON(windows, {
        style: (f) => ({ color: f.properties.split.startsWith("held-out") ? "#2dd4bf" : "#f5b544", weight: 2, fill: false, dashArray: "7 5" }),
        onEachFeature: (f, l) => l.bindTooltip(`${f.properties.window_id} — ${f.properties.split}`, { sticky: true }),
      }).addTo(mapL);
    }
    renderMapLegends();
  }
  document.querySelectorAll('input[name="cmp"]').forEach((r) => r.addEventListener("change", () => { renderLeft(); scheduleViewport(); }));
  $("review-candidate").addEventListener("change", () => { renderRightCandidate(); scheduleViewport(); });
  $("show-windows").addEventListener("change", renderWindows);
  $("show-flags").addEventListener("change", () => { renderLeft(); renderRightCandidate(); });

  // ---------- workspace ----------
  let ws = [];
  let storageOk = true;
  const workspace = api ? new WorkspaceClient(api.site.id) : null;
  if (workspace) {
    try { ws = (await workspace.load()).features; }
    catch (e) { status("Workspace load failed: " + e.message, "error"); return; }
    $("review-context").hidden = false;
    $("draw-context").hidden = false;
    $("export-revision-field").hidden = false;
  } else {
    try { ws = JSON.parse(localStorage.getItem(STORE_KEY) || "[]"); } catch (e) { ws = []; storageOk = false; }
  }
  let saving = false, exporting = false, reviewStarted = Date.now();
  let dirty = false;           // workspace changed since last successful export
  let selected = new Set();
  let inspectedWorkspaceId = null;
  let wsLayer = null;
  const layerById = new Map();
  function save() {
    if (workspace) return;
    try { localStorage.setItem(STORE_KEY, JSON.stringify(ws)); storageOk = true; } catch (e) { storageOk = false; }
  }
  function renderWorkspace(changed) {
    if (wsLayer) mapR.removeLayer(wsLayer);
    layerById.clear();
    wsLayer = L.geoJSON({ type: "FeatureCollection", features: ws }, {
      style: (f) => {
        const s = Object.assign({}, STATUS_STYLE[f.properties.review_status]);
        if (f.properties.origin !== "ai_copy" && f.properties.review_status !== "rejected") { s.color = COLORS.human; s.fillColor = COLORS.human; }
        if (selected.has(f.id)) { s.color = COLORS.selected; s.weight = 4; s.dashArray = null; s.fillOpacity = 0.3; }
        return s;
      },
      onEachFeature: (f, lyr) => {
        layerById.set(f.id, lyr);
        lyr.on("click", (ev) => {
          L.DomEvent.stopPropagation(ev);
          if (mode) return;   // ignore selection changes while a tool is active
          if (ev.originalEvent.shiftKey) selected.has(f.id) ? selected.delete(f.id) : selected.add(f.id);
          else selected = new Set([f.id]);
          reviewStarted = Date.now();
          renderWorkspace();
          showInfo(f, "ws");
        });
      },
    }).addTo(mapR);
    if (changed !== false) save();
    updateCounts(); updateButtons(); scheduleTopology();
  }
  function markChanged() { dirty = true; }
  const selFeatures = () => ws.filter((f) => selected.has(f.id));
  function replace(oldIds, newFeatures) {
    ws = ws.filter((f) => !oldIds.includes(f.id)).concat(newFeatures);
    selected = new Set(newFeatures.map((f) => f.id));
    markChanged(); renderWorkspace();
    if (newFeatures.length === 1) showInfo(newFeatures[0], "ws"); else if (!newFeatures.length) clearInfo();
  }
  function need(n) {
    const s = selFeatures();
    if (s.length < n) { status(`Select ${n === 1 ? "a workspace polygon" : "at least " + n + " workspace polygons"} first.`, "warn"); return null; }
    return s;
  }
  const inWorkspace = (f) => ws.some((w) => workspace ? w.properties.source_feature_id === f.id : w.properties.source_layer === f.properties.layer && w.properties.source_id === f.properties.source_id);

  async function savedChange(actions, message) {
    if (saving) return;
    saving = true; updateButtons();
    const context = { actor: $("review-actor").value || "local-reviewer", reason: $("review-reason").value,
      duration_ms: Math.min(86400000, Date.now() - reviewStarted) };
    try {
      for (const [path, body] of actions) {
        const result = await workspace.change(path, body, context);
        context.duration_ms = 0; // one elapsed interval per user action
        const ids = [...result.removed_ids, ...result.features.map((f) => f.id)];
        ws = ws.filter((f) => !ids.includes(f.id)).concat(result.features);
        selected = new Set(result.features.map((f) => f.id));
      }
      reviewStarted = Date.now(); dirty = false;
      renderWorkspace(false); showInfo(selFeatures()[0], "ws"); status(message);
    } catch (e) {
      try { ws = (await workspace.load()).features; selected.clear(); renderWorkspace(false); } catch (_) { storageOk = false; }
      status("Change failed: " + e.message + (e.code === "STALE_REVISION" ? " Current server revision reloaded; review it before retrying." : ""), "error");
    } finally { saving = false; updateButtons(); }
  }

  $("btn-add-selected").onclick = () => {
    if (!selectedCandidate) return status("Click an AI polygon on the right map first.", "warn");
    if (inWorkspace(selectedCandidate)) return status("That AI polygon is already in the workspace.", "warn");
    if (workspace) return savedChange([["workspace", { layer_id: layerId(selectedCandidate.properties.layer), feature_ids: [selectedCandidate.id] }]], "AI feature saved as suggested.");
    const f = EditOps.fromCandidate(selectedCandidate);
    ws.push(f); selected = new Set([f.id]); markChanged(); renderWorkspace(); showInfo(f, "ws");
    status("Added AI polygon to the workspace as 'suggested'.");
  };
  $("btn-add-view").onclick = () => {
    const k = $("review-candidate").value;
    if (!k) return status("Choose a candidate layer first.", "warn");
    const b = mapR.getBounds();
    if (workspace) return savedChange([["workspace", { layer_id: layerId(k), bbox: [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()] }]], "Visible AI features saved as suggested.");
    let n = 0;
    layersData[k].features.forEach((f) => {
      const c = turf.centroid(f).geometry.coordinates;
      if (b.contains([c[1], c[0]]) && !inWorkspace(f)) { ws.push(EditOps.fromCandidate(f)); n++; }
    });
    if (n) markChanged();
    renderWorkspace(); status(n ? `Added ${n} AI polygons from layer ${k} as 'suggested'.` : "No new polygons in view (already in workspace or none visible).", n ? null : "warn");
  };
  function setStatus(s) {
    const sel = need(1); if (!sel) return;
    if (workspace) return savedChange(sel.map((f) => ["features/" + f.id + "/" + {accepted:"accept",rejected:"reject",suggested:"reset"}[s], {}]), `${sel.length} feature(s) saved as ${s}.`);
    ws = ws.map((f) => (selected.has(f.id) ? EditOps.setStatus(f, s) : f));
    markChanged(); renderWorkspace();
    if (sel.length === 1) showInfo(ws.find((f) => f.id === sel[0].id), "ws");
    status(`${sel.length} polygon(s) marked ${s}.`);
  }
  $("btn-accept").onclick = () => setStatus("accepted");
  $("btn-reject").onclick = () => setStatus("rejected");
  $("btn-reset").onclick = () => setStatus("suggested");
  $("btn-remove").onclick = () => {
    const sel = need(1); if (!sel) return;
    if (workspace) return savedChange(sel.map((f) => ["features/" + f.id + "/remove", {}]), "Removed workspace copies.");
    replace(sel.map((f) => f.id), []); status(`Removed ${sel.length} polygon(s) from the workspace. AI layers are unchanged.`);
  };
  $("btn-merge").onclick = () => {
    const sel = need(2); if (!sel) return;
    if (workspace) return savedChange([["features/merge", { feature_ids: sel.map((f) => f.id) }]], "Merged geometry saved; review it before accepting.");
    try {
      const m = EditOps.merge(sel, turf);
      replace(sel.map((f) => f.id), [m]); status(`Merged ${sel.length} polygons (origin: human_merged).`);
    } catch (e) { status("Merge failed: " + e.message, "error"); }
  };

  // ---------- tool modes (edit / draw / split) ----------
  let mode = null, editing = null;
  function setMode(m, text) {
    mode = m;
    const b = $("mode-banner");
    if (!m) { b.hidden = true; chip("hdr-mode", "Mode: Model comparison & review"); }
    else {
      b.hidden = false;
      b.innerHTML = `<span>${esc(text)}</span>` + (m === "edit" ? `<button class="btn" id="mb-finish">Finish</button>` : "") + `<button class="btn" id="mb-cancel">Cancel (Esc)</button>`;
      chip("hdr-mode", "Mode: " + { edit: "Editing vertices", draw: "Drawing polygon", split: "Splitting polygon" }[m], "mode");
      if (m === "edit") $("mb-finish").onclick = () => $("btn-edit").click();
      $("mb-cancel").onclick = cancelMode;
      b.title = { edit: "Drag vertices of the selected polygon, then click Finish. Esc cancels without saving.",
        draw: "Click to add points; click the first point to close the polygon. Esc cancels.",
        split: "Draw a line that crosses the selected polygon completely; click the last point again to finish. Esc cancels." }[m];
    }
    $("btn-edit").classList.toggle("active", m === "edit");
    $("btn-edit").querySelector(".lbl").textContent = m === "edit" ? "Finish edit" : "Edit vertices";
    $("btn-draw").classList.toggle("active", m === "draw");
    $("btn-split").classList.toggle("active", m === "split");
    updateButtons();
  }
  function cancelMode() {
    if (mode === "edit" && editing) { const l = layerById.get(editing); if (l) l.pm.disable(); editing = null; renderWorkspace(false); status("Edit cancelled — geometry unchanged.", "warn"); }
    if (mode === "draw" || mode === "split") { mapR.pm.disableDraw(); status((mode === "split" ? "Split" : "Drawing") + " cancelled.", "warn"); }
    setMode(null);
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && mode) { e.preventDefault(); cancelMode(); }
  });
  $("btn-edit").onclick = () => {
    if (editing) {
      const lyr = layerById.get(editing);
      const gj = lyr.toGeoJSON();
      lyr.pm.disable();
      const id = editing;
      if (workspace) {
        editing = null; setMode(null);
        return savedChange([["features/" + id + "/edit", { geometry: gj.geometry }]], "Geometry saved as suggested; review it before accepting.");
      }
      try {
        ws = ws.map((f) => (f.id === id ? EditOps.withEditedGeometry(f, gj.geometry) : f));
        editing = null; setMode(null); markChanged(); renderWorkspace();
        showInfo(ws.find((f) => f.id === id), "ws");
        return status("Edit saved (origin: human_edited).");
      } catch (e) { editing = null; setMode(null); renderWorkspace(false); return status("Edit failed: " + e.message, "error"); }
    }
    const sel = need(1); if (!sel) return;
    if (sel.length !== 1) return status("Select exactly one polygon to edit.", "warn");
    editing = sel[0].id;
    const editLayer = layerById.get(editing), editId = editing;
    editLayer.off("pm:markerdragstart");
    editLayer.on("pm:markerdragstart", (ev) => flashSharedEdge(editId, ev.markerEvent.target.getLatLng()));
    editLayer.pm.enable({ allowSelfIntersection: false });
    setMode("edit", "Editing — drag vertices");
  };
  $("btn-draw").onclick = () => {
    if (mode === "draw") return cancelMode();
    mapR.pm.enableDraw("Polygon", { allowSelfIntersection: false });
    setMode("draw", "Drawing — close on first point");
  };
  $("btn-split").onclick = () => {
    if (mode === "split") return cancelMode();
    const sel = need(1); if (!sel) return;
    if (sel.length !== 1) return status("Select exactly one polygon to split.", "warn");
    mapR.pm.enableDraw("Line");
    setMode("split", "Split — line across polygon");
  };
  mapR.on("pm:create", (e) => {
    const gj = e.layer.toGeoJSON();
    mapR.removeLayer(e.layer);
    mapR.pm.disableDraw();
    const m = mode;
    if (workspace) {
      const actions = m === "draw" ? [["features/draw", { geometry: gj.geometry, review_status: "suggested", feature_type: $("draw-type").value, class_name: $("draw-class").value || null }]]
        : [["features/" + selFeatures()[0].id + "/split", { line: gj.geometry }]];
      setMode(null);
      return savedChange(actions, "Geometry saved as suggested; review it before accepting.");
    }
    try {
      if (m === "draw") {
        const f = EditOps.drawn(gj.geometry);
        ws.push(f); selected = new Set([f.id]); markChanged(); renderWorkspace(); showInfo(f, "ws");
        status("New polygon added (origin: human_drawn, accepted).");
      } else if (m === "split") {
        const target = selFeatures()[0];
        const r = EditOps.split(target, gj, turf);
        replace([target.id], r.parts); status(`Split into ${r.parts.length} parts (area lost to the 2 cm cut ≈ ${r.area_lost_m2.toFixed(2)} m²).`);
      }
    } catch (err) { status((m === "split" ? "Split" : "Draw") + " failed: " + err.message, "error"); }
    setMode(null);
  });

  // ---------- button state ----------
  function updateButtons() {
    const n = selected.size, busy = !!mode || saving || exporting;
    const set = (id, on) => { $(id).disabled = !on; };
    set("btn-add-selected", !busy && !!selectedCandidate && !inWorkspace(selectedCandidate));
    set("btn-add-view", !busy && !!$("review-candidate").value);
    ["btn-accept", "btn-reject", "btn-reset", "btn-remove"].forEach((id) => set(id, !busy && n >= 1));
    const useEditable = !!workspace && n === 1 && selFeatures()[0]?.id === inspectedWorkspaceId && (selFeatures()[0]?.properties.feature_type || "building_footprint") === "building_footprint";
    $("use-review").hidden = !useEditable;
    ["use-label", "use-evidence", "btn-use-save"].forEach((id) => set(id, !busy && useEditable));
    set("btn-merge", !busy && n >= 2);
    set("btn-edit", mode === "edit" || (!busy && n === 1));
    set("btn-draw", mode === "draw" || !busy);
    set("btn-split", mode === "split" || (!busy && n === 1));
    if (workspace) {
      $("export-revision").max = workspace.revision;
      if ($("export-revision").dataset.auto === "true") $("export-revision").value = workspace.revision;
    }
    const chosenRevision = workspace ? Number($("export-revision").value) : null;
    const exportable = workspace ? Number.isInteger(chosenRevision) && chosenRevision >= 0 && chosenRevision <= workspace.revision && (chosenRevision < workspace.revision || EditOps.exportCollection(ws, exportStatuses()).features.length > 0)
      : EditOps.exportCollection(ws, exportStatuses()).features.length > 0;
    set("btn-export", !busy && exportable); set("btn-export-top", !busy && exportable); set("btn-download", !busy && exportable);
    const sc = $("sel-count"); sc.textContent = n ? `${n} selected` : "0 selected"; sc.classList.toggle("on", n > 0);
    if (workspace) {
      $("review-revision").textContent = `Saved revision ${workspace.revision} · ${ws.filter((f) => f.properties.review_status === "suggested").length} awaiting review`;
      $("btn-next-review").disabled = busy;
      $("btn-history").disabled = saving;
    }
  }

  // ---------- topology conflicts (backend /api/topology/*; real Shapely checks, not simulated) ----------
  const CONFLICT_STYLE = { warning: "#f5b544", error: "#f87171" };
  const topoLayers = [];
  let topoSeq = 0, topoTimer = null, lastEdgeFlash = null;
  const topoToggle = $("show-topology");
  async function postJSON(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error((j.error && j.error.message) || "HTTP " + r.status);
    return j;
  }
  function drawConflicts(map, result) {
    const feats = result.conflicts.filter((c) => c.geometry).map((c) => ({ type: "Feature", geometry: c.geometry, properties: c }));
    const g = L.geoJSON({ type: "FeatureCollection", features: feats }, {
      style: (f) => { const c = CONFLICT_STYLE[f.properties.severity]; return { color: c, fillColor: c, weight: 2.5, fillOpacity: 0.3, className: "conflict-pulse" }; },
      pointToLayer: (f, ll) => L.circleMarker(ll, { radius: 7, color: CONFLICT_STYLE.error, fillColor: CONFLICT_STYLE.error, fillOpacity: 0.4, className: "conflict-pulse" }),
      onEachFeature: (f, lyr) => lyr.bindTooltip(`${f.properties.severity}: ${f.properties.message} (${f.properties.features.join(", ")})`),
    }).addTo(map);
    topoLayers.push({ map, layer: g });
  }
  function clearConflicts() { topoLayers.splice(0).forEach((t) => t.map.removeLayer(t.layer)); }
  function scheduleTopology() {
    clearTimeout(topoTimer);
    topoTimer = setTimeout(renderTopology, 250);
  }
  async function renderTopology() {
    const seq = ++topoSeq;
    clearConflicts();
    const note = $("topology-note");
    if (!topoToggle.checked) { note.textContent = ""; return; }
    if (!api) { note.textContent = "Topology validation unavailable: the FastAPI backend is not running (see docs/backend.md)."; return; }
    note.textContent = "Checking topology…";
    try {
      const parts = [];
      const kL = (document.querySelector('input[name="cmp"]:checked') || {}).value;
      if (kL && layerId(kL)) parts.push(["left " + kL, mapL, await postJSON("/api/topology/validate-layer", { site_id: api.site.id, layer_id: layerId(kL) })]);
      const kR = $("review-candidate").value;
      if (kR && layerId(kR)) parts.push(["right " + kR, mapR, await postJSON("/api/topology/validate-layer", { site_id: api.site.id, layer_id: layerId(kR) })]);
      const live = EditOps.exportCollection(ws, ["suggested", "accepted"]);
      if (live.features.length) parts.push(["workspace", mapR, await postJSON("/api/topology/validate", { site_id: api.site.id, features: live })]);
      if (seq !== topoSeq) return;                     // a newer request superseded this one
      const lines = [];
      parts.forEach(([name, map, res]) => {
        drawConflicts(map, res);
        lines.push(res.valid ? `${name}: no conflicts (${res.summary.feature_count} footprints, ${res.summary.shared_boundary_count} shared edges)`
          : `${name}: ${res.summary.conflict_count} conflict(s) — ` + Object.entries(res.summary.by_type).map(([t, n]) => n + " " + t.replace("_", " ")).join(", "));
      });
      note.textContent = lines.join(" · ") + ". Footprint relationships only — not cadastral topology.";
    } catch (e) { if (seq === topoSeq) note.textContent = "Topology validation failed: " + e.message; }
  }
  topoToggle.addEventListener("change", renderTopology);
  if (!api) { topoToggle.disabled = true; $("topology-note").textContent = "Topology validation unavailable (FastAPI backend not running)."; }

  // Shared-boundary edit cue: dragging a vertex asks the backend which neighbour shares that edge and flashes it ~200 ms.
  const EDGE_FLASH_MS = 200;
  async function flashSharedEdge(featureId, latlng) {
    if (!api) return;
    try {
      const live = EditOps.exportCollection(ws, ["suggested", "accepted"]);
      live.features.forEach((f) => { f.id = f.properties.workspace_id; });
      const r = await postJSON("/api/topology/shared-boundary", { site_id: api.site.id, feature_id: featureId, vertex: [latlng.lng, latlng.lat], features: live });
      if (!r.shared) return;
      const edge = L.geoJSON(r.edge, { style: { color: COLORS.cue, weight: 6, opacity: 0.95, className: "edge-flash" }, interactive: false }).addTo(mapR);
      setTimeout(() => mapR.removeLayer(edge), EDGE_FLASH_MS);
      lastEdgeFlash = { neighbor: r.neighbor_feature_id, at: Date.now() };
    } catch (e) { /* the cue is best-effort; editing continues */ }
  }

  // ---------- export ----------
  const exportStatuses = () => Array.from(document.querySelectorAll(".exp-status:checked")).map((c) => c.value);
  document.querySelectorAll(".exp-status").forEach((c) => c.addEventListener("change", updateButtons));
  $("export-revision").oninput = () => { $("export-revision").dataset.auto = "false"; updateButtons(); };
  $("btn-export").onclick = async () => {
    if (exporting) return;
    const fc = EditOps.exportCollection(ws, exportStatuses());
    const revision = workspace ? Number($("export-revision").value) : null;
    const out = $("export-result");
    if (!workspace && !fc.features.length) { out.className = "result error"; out.textContent = "Nothing to export for the chosen statuses."; return; }
    out.className = "result"; out.textContent = "";
    exporting = true; updateButtons(); status("Exporting…");
    try {
      const body = workspace ? { site_id: api.site.id, revision, statuses: exportStatuses(), note: "exported from review app" } : { features: fc, note: "exported from review app" };
      const r = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) {
        const er = j.error;   // legacy server: {"error": str, "details": [str]}; FastAPI backend: {"error": {code, message, details}}
        const obj = er && typeof er === "object";
        const det = obj ? (er.details && er.details.errors ? er.details.errors.map((x) => `feature ${x.feature_index}: ${x.message}`) : []) : (j.details || []);
        throw new Error((obj ? `${er.code}: ${er.message}` : er || r.status) + (det.length ? "\n" + det.join("\n") : ""));
      }
      out.className = "result ok";
      out.textContent = `Exported ${j.feature_count} footprints (${j.total_area_m2} m²)\n${j.dir}\n• ${j.files.analysis_crs.file} (${j.files.analysis_crs.crs})\n• ${j.files.wgs84.file}\nBuilding footprints only — not cadastral boundaries.`;
      dirty = false; status(`Exported ${j.feature_count} footprints.`);
    } catch (e) { out.className = "result error"; out.textContent = "Export failed: " + e.message; status("Export failed — see Step 4.", "error"); }
    exporting = false;
    updateButtons();
  };
  $("btn-export-top").onclick = () => {
    const d = $("btn-export").closest("details"); if (d) d.open = true;
    $("btn-export").scrollIntoView({ block: "center", behavior: "smooth" });
    $("btn-export").click();
  };
  $("btn-download").onclick = async () => {
    let fc = EditOps.exportCollection(ws, exportStatuses());
    if (workspace) {
      try {
        const saved = await getJSON(`/api/review/workspace?site_id=${api.site.id}&revision=${Number($("export-revision").value)}`);
        fc = { ...saved, features: saved.features.filter((f) => exportStatuses().includes(f.properties.review_status)) };
      } catch (e) { return status("Download failed: " + e.message, "error"); }
    }
    const blob = new Blob([JSON.stringify(fc)], { type: "application/geo+json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = "building_footprints_reviewed_WGS84.geojson"; a.click();
    URL.revokeObjectURL(a.href);
    status("Downloaded WGS84 GeoJSON (client-side copy).");
  };
  window.addEventListener("beforeunload", (e) => {
    if (saving || exporting || mode === "edit" || (!workspace && dirty && (!storageOk || ws.length))) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---------- inspector ----------
  function clearInfo() { $("info").innerHTML = `<p class="empty">Nothing selected. Click a polygon on either map.</p>`; inspectedWorkspaceId = null; $("use-review").hidden = true; }
  function showInfo(f, kind) {
    if (!f) return clearInfo();
    inspectedWorkspaceId = kind === "ws" ? f.id : null;
    const p = f.properties || {};
    const rows = [];
    if (p.feature_type) rows.push(["Feature type", p.feature_type.replaceAll("_", " ")]);
    if (p.class_name) rows.push(["Cover class", p.class_name]);
    if (p.functional_use) rows.push(["Functional use", p.functional_use]);
    if (p.functional_use_suggestion) rows.push(["Use suggestion", p.functional_use_suggestion + " · candidate"]);
    if (p.use_candidate) rows.push(["Use candidate model", p.use_candidate.model_id], ["Use candidate note", p.use_candidate.reason]);
    if (p.functional_use_review) {
      rows.push(["Use review", p.functional_use_review.status.replaceAll("_", " ")],
        ["Use reviewer", p.functional_use_review.actor], ["Use source references", p.functional_use_review.evidence_refs.join("; ") || "none"]);
    }
    let head;
    const flags = (p.flags || p.ai_flags || "").split(",").filter(Boolean);
    if (kind === "ai") {
      const L_ = manifest.layers[p.layer];
      head = `<div class="ins-head"><span class="badge ai">AI prediction</span><span class="ins-title">${esc(shortName(p.layer))}</span></div>`;
      rows.push(["Source model", `${p.layer} · ${L_.name}`], ["Feature ID", p.source_id], ["Area", `${p.area_m2} m² (${manifest.analysis_crs})`],
        [L_.score_label || "Model score", p.score ?? "n/a"], ["Editable", "No — add to workspace to review"]);
    } else {
      head = `<div class="ins-head"><span class="badge ${esc(p.review_status)}">${esc(p.review_status)}</span><span class="ins-title">Workspace feature</span></div>`;
      let area = null;
      try { area = turf.area(f); } catch (e) { area = null; }
      rows.push(["Provenance", ORIGIN_LABEL[p.origin] || p.origin]);
      if (p.source_layer) rows.push(["Source model", p.source_layer.split(",").map((k) => manifest.layers[k] ? `${k} · ${shortName(k)}` : k).join(", ")]);
      if (p.source_id !== undefined && p.source_id !== null) rows.push(["Source feature ID", p.source_id]);
      if (p.ai_score !== undefined && p.ai_score !== null) rows.push(["Model score (at copy)", p.ai_score]);
      if (area !== null) rows.push(["Area", `≈ ${area.toFixed(1)} m² (geodesic; export reports ${manifest.analysis_crs} area)`]);
      if (p.parents && p.parents.length) rows.push(["Derived from", `${p.parents.length} workspace feature(s)`]);
      rows.push(["Workspace ID", f.id]);
    }
    const cues = flags.length ? `<ul class="cues">${flags.map((x) => `<li><svg><use href="#i-warn"/></svg><span>${esc(FLAG_TEXT[x] || x)}</span></li>`).join("")}</ul><p class="help">Cues are display heuristics, not evaluated errors.</p>` : "";
    $("info").innerHTML = head + `<dl class="kv">${rows.map(([a, b]) => `<dt>${esc(a)}</dt><dd>${esc(b)}</dd>`).join("")}</dl>` + cues;
    const editable = !!workspace && kind === "ws" && selected.size === 1 && (p.feature_type || "building_footprint") === "building_footprint";
    $("use-review").hidden = !editable;
    if (editable) {
      $("use-label").value = p.functional_use || "unknown";
      $("use-evidence").value = p.functional_use_review?.status === "reviewed" ? p.functional_use_review.evidence_refs.join("\n") : "";
    }
    updateButtons();
  }

  $("use-review").onsubmit = (e) => {
    e.preventDefault();
    if (!workspace || mode || saving || exporting) return;
    const features = need(1);
    if (!features || features.length !== 1) return;
    return savedChange([[`features/${features[0].id}/functional-use`, {
      functional_use: $("use-label").value.trim(),
      evidence_refs: $("use-evidence").value.split("\n").map((ref) => ref.trim()).filter(Boolean),
    }]], "Use review saved; accept the updated feature after checking it.");
  };

  // ---------- counts ----------
  function inView(fc, map) {
    const b = map.getBounds();
    return fc.features.filter((f) => { const c = turf.centroid(f).geometry.coordinates; return b.contains([c[1], c[0]]); }).length;
  }
  function updateCounts() {
    $("counts").innerHTML = "<tr><th>Layer</th><th>Total</th><th>In view</th><th>Cues</th></tr>" +
      keys.map((k) => `<tr><td><span class="swatch" style="border-color:${manifest.layers[k].color}"></span> ${k}</td><td>${manifest.layers[k].count}</td><td>${inView(layersData[k], mapR)}</td><td>${layersData[k].features.filter((f) => f.properties.flags).length}</td></tr>`).join("");
    const by = (key) => ws.reduce((o, f) => ((o[f.properties[key]] = (o[f.properties[key]] || 0) + 1), o), {});
    const st = by("review_status"), human = ws.filter((f) => f.properties.origin !== "ai_copy").length;
    const pills = [`<span class="pill">${ws.length} in workspace</span>`]
      .concat(["suggested", "accepted", "rejected"].filter((s) => st[s]).map((s) => `<span class="pill ${s}">${st[s]} ${s}</span>`));
    if (human) pills.push(`<span class="pill human">${human} human-edited</span>`);
    if (!storageOk) pills.push(`<span class="pill warn">not saved locally — export before closing</span>`);
    else if (ws.length) pills.push(`<span class="pill">${workspace ? "saved on server · revision " + workspace.revision : "autosaved in this browser"}</span>`);
    if (dirty && ws.length) pills.push(`<span class="pill warn">unexported changes</span>`);
    $("ws-counts").innerHTML = pills.join("");
  }
  mapR.on("moveend", updateCounts);
  mapR.on("click", () => { if (!mode) highlightCandidate(null, null); });

  // ---------- legends ----------
  function lg(color, label, dash) { return `<div class="row"><span class="lg" style="border-color:${color};${dash ? "border-style:dashed" : ""}"></span>${esc(label)}</div>`; }
  function renderMapLegends() {
    const k = (document.querySelector('input[name="cmp"]:checked') || {}).value;
    let l = k ? lg(manifest.layers[k].color, `${k} · ${shortName(k)} (AI)`) : "";
    if (showFlags()) l += lg(COLORS.cue, "AI polygon with cue", true);
    if ($("show-windows").checked) l += lg("#2dd4bf", "Held-out test window", true) + lg("#f5b544", "Development window", true);
    $("legend-left").innerHTML = l;
    const rk = $("review-candidate").value;
    $("legend-right").innerHTML = (rk ? lg(manifest.layers[rk].color, `${rk} candidates (read-only)`) : "") +
      lg(COLORS.suggested, "Suggested", true) + lg(COLORS.accepted, "Accepted") + lg(COLORS.rejected, "Rejected", true) +
      lg(COLORS.human, "Human-edited / drawn") + lg(COLORS.selected, "Selected");
  }
  $("legend").innerHTML =
    keys.map((k) => lg(manifest.layers[k].color, `${k} · ${manifest.layers[k].name} — AI, read-only`)).join("") +
    lg(COLORS.cue, "AI polygon with failure-mode cue", true) + lg(COLORS.suggested, "Workspace: suggested (AI copy)", true) +
    lg(COLORS.accepted, "Workspace: accepted") + lg(COLORS.rejected, "Workspace: rejected", true) +
    lg(COLORS.human, "Workspace: human-edited, merged, split or drawn") + lg(COLORS.selected, "Selected") +
    lg("#2dd4bf", "Held-out test window (T1–T4)", true) + lg("#f5b544", "Development window (W/V)", true);
  $("failure-modes").innerHTML = manifest.known_failure_modes.map((m) => `<li><b>${esc(m.mode)}</b> <span class="small">(${esc(m.applies_to)})</span><br><span class="small">${esc(m.evidence)}</span></li>`).join("");
  $("flag-rules").innerHTML = "<b>Cue rules:</b> " + Object.entries(manifest.flag_rules).map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(" · ");

  // ---------- evaluation drawer ----------
  const fmt = (v) => (v === null || v === undefined ? "n/a" : typeof v === "number" && !Number.isInteger(v) ? v.toFixed(3) : v);
  const models = keys.map((k) => ({ key: k, name: shortName(k), color: manifest.layers[k].color, metrics: manifest.layers[k].metrics, tag: k === "C" ? "candidate · not promoted" : "" }))
    .concat(Object.entries(manifest.metrics_only || {}).map(([k, v]) => ({ key: k, name: "Hybrid (WHU + Mask R-CNN)", color: v.color, metrics: v.metrics, tag: "metrics only · no map layer" })));
  const BANNER = {
    "test_T1-T4": "Held-out test: four held-out 40 × 40 m windows of the same orthophoto, 30 reference buildings, scored once under a fixed protocol. These results do not establish city-wide or cadastral accuracy. No model was promoted; Mask R-CNN remains a candidate with promising building-level results and lower roof-area coverage.",
    "development_W1-W4": "DEVELOPMENT windows W1–W4 (32 labels) — already viewed during method design. Not test metrics.",
    "development_V1-V4": "DEVELOPMENT windows V1–V4 (49 labels) — already viewed during method design. Not test metrics.",
  };
  function renderMetrics() {
    const split = $("metric-split").value;
    const isTest = split.startsWith("test");
    document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", String(t.dataset.split === split)));
    const banner = $("metric-banner");
    if (!models.some((m) => m.metrics?.[split])) {
      banner.textContent = "No evaluation is available for this site and split. Accuracy needs reviewed reference data.";
      $("metric-cards").innerHTML = ""; $("metrics").innerHTML = ""; $("eval-notes").innerHTML = "";
      return;
    }
    banner.className = "banner " + (isTest ? "test" : "dev");
    banner.textContent = BANNER[split];
    const bar = (v) => `<div class="bar"><i style="width:${Math.max(0, Math.min(1, v || 0)) * 100}%"></i></div>`;
    $("metric-cards").innerHTML = models.map((m) => {
      const b = m.metrics?.[split];
      if (!b) return `<div class="card"><div class="card-head"><span class="swatch" style="border-color:${m.color}"></span><span>${m.key} · ${esc(m.name)}</span></div><p class="small">Not available for this split.</p></div>`;
      const o = b.overall;
      return `<div class="card"><div class="card-head"><span class="swatch" style="border-color:${m.color}"></span><span>${m.key} · ${esc(m.name)}</span></div>
        ${m.tag ? `<div class="small" style="margin:-4px 0 6px">${esc(m.tag)}</div>` : ""}
        <div class="metric"><span>Pixel IoU</span><b>${fmt(o.pixel_iou)}</b>${bar(o.pixel_iou)}</div>
        <div class="metric"><span>Building precision</span><b>${fmt(o.building_precision)}</b>${bar(o.building_precision)}</div>
        <div class="metric"><span>Building recall</span><b>${fmt(o.building_recall)}</b>${bar(o.building_recall)}</div>
        <div class="foot">Matched <b>${o.matched}</b> of <b>${o.n_label}</b> buildings · TP/FP/FN <b>${o.matched}/${o.n_pred - o.matched}/${o.n_label - o.matched}</b></div></div>`;
    }).join("");
    const first = models.find((m) => m.metrics?.[split]).metrics[split];
    const wins = Object.keys(first.per_window || {});
    $("metrics").innerHTML = `<tr><th>Per window (pixel IoU · buildings matched/labels)</th>${wins.map((w) => `<th>${esc(w.split("_")[0])}</th>`).join("")}</tr>` +
      models.map((m) => { const b = m.metrics?.[split]; return `<tr><td>${m.key} · ${esc(m.name)}</td>${wins.map((w) => { const x = b && b.per_window[w]; return `<td>${x ? fmt(x.pixel_iou) + " · " + x.matched + "/" + x.n_label : "n/a"}</td>`; }).join("")}</tr>`; }).join("");
    const s = first.settings;
    $("eval-notes").innerHTML = [`Scoring: ${s.n_labels} reference buildings · ${s.eval_res_m} m grid · building match IoU ≥ ${s.match_iou} · pieces < ${s.min_piece_m2} m² dropped · identical settings for all models.`]
      .concat(manifest.evaluation_notes).map((n) => `<li>${esc(n)}</li>`).join("");
  }
  $("metric-split").addEventListener("change", renderMetrics);
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { $("metric-split").value = t.dataset.split; $("metric-split").dispatchEvent(new Event("change")); }));
  function toggleEval(open) {
    const d = $("eval-drawer"); d.hidden = open === undefined ? !d.hidden : !open;
    $("btn-toggle-eval").setAttribute("aria-expanded", String(!d.hidden));
    $("btn-toggle-eval").classList.toggle("active", !d.hidden);
  }
  $("btn-toggle-eval").onclick = () => toggleEval();
  $("btn-close-eval").onclick = () => toggleEval(false);

  // Fetch only the visible parts of the active layers, in bounded pages.
  let viewportSeq = 0, viewportTimer = null;
  function scheduleViewport() {
    if (!api) return;
    clearTimeout(viewportTimer); viewportTimer = setTimeout(loadViewport, 200);
  }
  async function loadViewport() {
    if (!api) return;
    const seq = ++viewportSeq, bounds = mapR.getBounds();
    const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(",");
    const active = new Set([document.querySelector('input[name="cmp"]:checked')?.value, $("review-candidate").value]);
    active.delete(undefined); active.delete("");
    try {
      for (const key of active) {
        const features = []; let page;
        do {
          page = await getJSON(`/api/sites/${api.site.id}/layers/${layerId(key)}/features?bbox=${encodeURIComponent(bbox)}&limit=500&offset=${features.length}`);
          if (seq !== viewportSeq) return;
          features.push(...page.features.map((f) => ({ ...f, properties: { ...f.properties, layer: key } })));
        } while (page.numberReturned && features.length < page.numberMatched && features.length < 5000);
        layersData[key] = { type: "FeatureCollection", features };
        if (page.numberMatched > features.length) status("Showing the first 5,000 visible features; zoom in to review a smaller area.", "warn");
      }
      if (seq === viewportSeq && !mode) { renderLeft(); renderRightCandidate(); updateCounts(); }
    } catch (e) { if (seq === viewportSeq) status("Layer loading failed: " + e.message, "error"); }
  }
  mapR.on("moveend", scheduleViewport);
  $("btn-next-review").onclick = async () => {
    if (!workspace || mode || saving) return;
    try {
      const q = await getJSON("/api/review/queue?site_id=" + api.site.id);
      const f = q.features.find((x) => !selected.has(x.id)) || q.features[0];
      if (!f) return status("No suggested features remain.");
      selected = new Set([f.id]); reviewStarted = Date.now(); renderWorkspace(false); showInfo(f, "ws");
      mapR.fitBounds(L.geoJSON(f).getBounds().pad(0.4));
    } catch (e) { status("Review queue failed: " + e.message, "error"); }
  };
  $("btn-history").onclick = async () => {
    if (!workspace) return;
    try {
      const h = await getJSON("/api/review/history?site_id=" + api.site.id + "&limit=20");
      const list = $("review-history"); list.hidden = false;
      list.innerHTML = h.events.map((e) => `<li>Revision ${e.revision} · ${esc(e.operation || "change")} · ${esc(e.actor || "local-reviewer")} · ${esc(e.timestamp)}${e.reason ? " · " + esc(e.reason) : ""}</li>`).join("") || "<li>No saved changes yet.</li>";
    } catch (e) { status("History failed: " + e.message, "error"); }
  };

  // ---------- start ----------
  renderLeft(); renderRightCandidate(); renderWorkspace(false); renderMetrics(); clearInfo();
  await loadViewport();
  status(`Loaded ${keys.map((k) => k + "=" + manifest.layers[k].count).join(", ")} · workspace ${ws.length}`);
  window.__app = { mapL, mapR, get ws() { return ws; }, get revision() { return workspace?.revision; }, get saving() { return saving; }, toggleEval, get api() { return api; }, renderTopology, get lastEdgeFlash() { return lastEdgeFlash; } };  // for automated checks
  if (api) window.dispatchEvent(new CustomEvent("site-ready", { detail: api.site }));
})();
