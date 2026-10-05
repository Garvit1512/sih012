/* Minimal parcel/evidence workflow; source geometry remains distinct from roofs. */
window.addEventListener("sih-ready", async ({ detail: { siteId, mapL, mapR } }) => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let snapshot, overlays = [], busy = false;
  const panel = $("parcel-controls"), result = $("parcel-result");
  panel.hidden = false;
  $("parcel-date").value = new Date().toISOString().slice(0, 10);
  const refs = () => $("parcel-records").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const context = () => ({ site_id: siteId, expected_revision: snapshot.revision,
    actor: $("review-actor").value.trim() || "local-reviewer", reason: $("parcel-reason").value });
  async function request(path, body) {
    const r = await fetch("/api/parcels" + path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    const data = await r.json();
    if (!r.ok) throw new Error(data.error?.message || data.detail?.[0]?.msg || "HTTP " + r.status);
    return data;
  }
  function selected() { return snapshot.features.find((f) => f.id === $("parcel-selected").value); }
  function selectionChanged(fit = false) {
    const f = selected();
    $("parcel-state-save").disabled = busy || !f;
    $("parcel-shared-save").disabled = busy || !f;
    if (f) $("parcel-state").value = f.properties.parcel_state;
    $("parcel-selection-info").textContent = f ? `${f.properties.parcel_state} · ${f.properties.area_m2} m² · ${f.properties.field_check?.status || "no field check"}${f.properties.reference_conflict ? " · reference mismatch: survey required" : ""}` : "No parcel proposal yet.";
    if (f && fit) mapR.fitBounds(L.geoJSON(f).getBounds().pad(.3));
  }
  async function refresh() {
    const old = $("parcel-selected").value;
    snapshot = await request("/" + encodeURIComponent(siteId));
    const parcels = snapshot.features.filter((f) => f.properties.feature_type === "parcel");
    $("parcel-selected").innerHTML = '<option value="">Select a parcel</option>' + parcels.map((f) => `<option value="${esc(f.id)}">${esc(f.id)} · ${esc(f.properties.parcel_state)}</option>`).join("");
    if (parcels.some((f) => f.id === old)) $("parcel-selected").value = old;
    $("parcel-neighbour").innerHTML = parcels.map((f) => `<option value="${esc(f.id)}">${esc(f.id)}</option>`).join("");
    $("parcel-summary").textContent = `Parcel revision ${snapshot.revision} · ${parcels.length} proposals · ${snapshot.features.length - parcels.length} evidence entities · ${snapshot.diagnostics.conflicts.length} topology conflicts · ${snapshot.diagnostics.unresolved_linework_m ?? "—"} m open linework`;
    overlays.forEach(([map, layer]) => map.removeLayer(layer)); overlays = [];
    for (const map of [mapL, mapR]) {
      const layer = L.geoJSON(snapshot, {
        style: (f) => ({ color: f.properties.feature_type === "reference_parcel" ? "#d5a64f" : f.properties.feature_type === "parcel" ? "#66c6b8" : "#7ca7d5",
          weight: 2, fillOpacity: .12, dashArray: f.properties.feature_type === "parcel" ? "5 3" : null }),
        pointToLayer: (_, latlng) => L.circleMarker(latlng, { radius: 4, color: "#7ca7d5" }),
        onEachFeature: (f, item) => {
          item.bindTooltip(`${esc(f.properties.feature_type)} · ${esc(f.id)}${f.properties.parcel_state ? " · " + esc(f.properties.parcel_state) : ""}`);
          if (f.properties.feature_type === "parcel") item.on("click", () => { $("parcel-selected").value = f.id; selectionChanged(); });
        },
      }).addTo(map);
      overlays.push([map, layer]);
    }
    $("parcel-export").disabled = busy || !snapshot.features.length;
    selectionChanged();
  }
  async function change(action) {
    if (busy) return;
    busy = true; panel.querySelectorAll("button").forEach((b) => { b.disabled = true; });
    result.textContent = "Saving parcel workflow…";
    try { await action(); await refresh(); }
    catch (e) { result.textContent = e.message; await refresh().catch(() => {}); }
    finally { busy = false; panel.querySelectorAll("button").forEach((b) => { b.disabled = false; }); selectionChanged(); $("parcel-export").disabled = !snapshot?.features.length; }
  }
  $("parcel-selected").onchange = () => selectionChanged(true);
  $("parcel-import-form").onsubmit = (event) => {
    event.preventDefault();
    change(async () => {
      const file = $("parcel-file").files[0];
      if (!file || file.size > 2_000_000) throw new Error("Choose a GeoJSON file up to 2 MB.");
      const response = await request("/import", { ...context(), kind: $("parcel-kind").value, features: JSON.parse(await file.text()),
        crs: $("parcel-crs").value, source: $("parcel-source").value, captured_at: $("parcel-date").value,
        horizontal_datum: $("parcel-datum").value, accuracy_m: $("parcel-accuracy").value ? Number($("parcel-accuracy").value) : null,
        reviewed: $("parcel-reviewed").checked, observation_method: $("parcel-method").value, parcel_id: selected()?.id || null });
      result.textContent = `Imported ${response.info.imported} ${response.info.kind} entities at revision ${response.revision}.`;
    });
  };
  $("parcel-propose").onclick = () => change(async () => {
    const data = await request("/propose", { ...context(), mode: $("parcel-mode").value });
    const open = Object.values(data.info.unresolved_linework).reduce((sum, part) => sum + part.length_m, 0);
    result.textContent = `Created ${data.info.closed_faces} supported proposals; ${open.toFixed(2)} m unresolved linework; ${data.info.conflicts.length} proposal conflicts.`;
  });
  $("parcel-state-save").onclick = () => change(async () => {
    const f = selected(); if (!f) throw new Error("Select a parcel.");
    await request("/features/" + f.id + "/state", { ...context(), state: $("parcel-state").value, record_refs: refs() });
    result.textContent = "Parcel state saved for this geometry. Field checked is a local evidence assertion.";
  });
  $("parcel-shared-save").onclick = () => change(async () => {
    const f = selected(); if (!f) throw new Error("Select a parcel.");
    await request("/shared-edge", { ...context(), feature_ids: [f.id, $("parcel-neighbour").value],
      line: JSON.parse($("parcel-edge").value), crs: $("parcel-crs").value, evidence_refs: refs() });
    result.textContent = "Both neighbouring parcels saved atomically; their field checks need review.";
  });
  $("parcel-export").onclick = () => change(async () => {
    const data = await request("/export", { ...context(), revision: snapshot.revision });
    result.textContent = `Saved parcel revision ${data.parcel_revision}. `;
    const link = document.createElement("a"); link.href = data.download_url; link.textContent = "Download GIS export ZIP"; link.download = ""; result.append(link);
  });
  try { await refresh(); }
  catch (e) { result.textContent = "Parcel workflow unavailable: " + e.message; }
});
