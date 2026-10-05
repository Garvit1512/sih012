/* Jobs never promote model accuracy: completion means validated software output. */
window.addEventListener("site-ready", async ({ detail: site }) => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const loaded = new Set(site.layers.map((l) => l.id));
  let busy = false;
  async function request(url, body) {
    const r = await fetch(url, body ? { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify(body) } : {});
    const j = await r.json(); if (!r.ok) throw new Error(j.error?.message || "HTTP " + r.status); return j;
  }
  try {
    $("job-controls").hidden = false;
    const { models } = await request("/api/models");
    const imageModels = models.filter((m) => m.architecture !== "use_centroid");
    $("job-model").innerHTML = imageModels.map((m) => `<option value="${esc(m.id)}" ${m.available ? "" : "disabled"}>${esc(m.name)}${m.available ? " · candidate" : " · missing " + esc(m.missing.join(", "))}</option>`).join("");
    const ready = imageModels.find((m) => m.available);
    if (ready) $("job-model").value = ready.id;
    $("job-run").disabled = !ready;
    $("job-run").onclick = () => submit({kind:"inference", model_id:$("job-model").value,
      name:models.find((m) => m.id === $("job-model").value).name, target_res_m:Number($("job-resolution").value), min_area_m2:Number($("job-area").value)});
    const useModels = models.filter((m) => m.architecture === "use_centroid");
    $("use-model").innerHTML = useModels.map((m) => `<option value="${esc(m.id)}" ${m.available ? "" : "disabled"}>${esc(m.name)}${m.available ? " · candidate" : " · unavailable"}</option>`).join("") || '<option value="">No registered use candidate</option>';
    const useReady = useModels.find((m) => m.available);
    if (useReady) $("use-model").value = useReady.id;
    const layerOptions = site.layers.map((l) => `<option value="${esc(l.id)}">${esc(l.name)}</option>`).join("") || '<option value="">No layers yet</option>';
    $("use-buildings").innerHTML = layerOptions; $("use-roads").innerHTML = layerOptions;
    $("job-run-use").disabled = !useReady || !site.layers.length;
    $("job-run-use").onclick = () => submit({kind:"use_classification", model_id:$("use-model").value,
      building_layer_id:$("use-buildings").value, road_layer_id:$("use-roads").value,
      name:useModels.find((m) => m.id === $("use-model").value).name});
    $("job-import").onclick = async () => {
      const file = $("job-file").files[0];
      if (!file) { $("job-note").textContent = "Choose a GeoJSON file first."; return; }
      try {
        if (file.size > 20 * 1024 * 1024) throw new Error("Import at most 20 MiB at a time.");
        await submit({kind:"import_layer", features:JSON.parse(await file.text()), name:file.name.slice(0,150),
          source:"local file: " + file.name, input_crs:$("job-crs").value, feature_type:$("job-type").value});
      } catch (e) { $("job-note").textContent = "Import failed: " + e.message; }
    };
    async function submit(body) {
      if (busy) return; busy = true;
      try { await request(`/api/sites/${site.id}/jobs`, body); $("job-note").textContent = "Job queued. Output remains a candidate until evaluated."; await refresh(); }
      catch (e) { $("job-note").textContent = "Job failed: " + e.message; }
      finally { busy = false; }
    }
    async function refresh() {
      const { jobs } = await request(`/api/sites/${site.id}/jobs`);
      const list = $("job-list");
      list.innerHTML = jobs.slice(0,20).map((j) => `<div class="job-item"><b>${esc(j.kind)} · ${esc(j.status)}</b><small>${esc(j.id)}</small>
        ${j.status === "running" ? `<progress max="1" value="${j.progress?.fraction || 0}"></progress>` : ""}
        ${j.error ? `<p class="small">${esc(j.error)}</p>` : ""}
        <div class="btn-row">${["queued","running"].includes(j.status) ? `<button class="btn" data-job="${j.id}" data-action="cancel">Cancel</button>` : ""}
        ${["failed","cancelled","interrupted"].includes(j.status) ? `<button class="btn" data-job="${j.id}" data-action="retry">Retry</button>` : ""}
        ${j.status === "succeeded" && !loaded.has(j.layer_id) ? `<button class="btn" data-action="load">Load result</button>` : ""}
        <button class="btn" data-job="${j.id}" data-action="log">Details</button></div></div>`).join("") || '<p class="small">No jobs yet.</p>';
      list.querySelectorAll("button").forEach((button) => { button.onclick = async () => {
        if (button.dataset.action === "load") { location.reload(); return; }
        button.disabled = true;
        try {
          if (button.dataset.action === "log") {
            const j = await request("/api/jobs/" + button.dataset.job);
            let pre = button.closest(".job-item").querySelector("pre");
            if (!pre) { pre = document.createElement("pre"); button.closest(".job-item").appendChild(pre); }
            pre.textContent = (j.progress?.stage || j.status) + "\n" + (j.log_tail || "No worker output yet.");
            if (j.status === "succeeded") {
              const artifacts = await request(`/api/jobs/${button.dataset.job}/artifacts`);
              let links = button.closest(".job-item").querySelector(".job-files");
              if (!links) { links = document.createElement("div"); links.className = "job-files small"; button.closest(".job-item").appendChild(links); }
              links.innerHTML = artifacts.files.map((f) => `<a href="${esc(f.url)}" download>${esc(f.name)}</a>`).join("<br>");
            }
          } else { await request(`/api/jobs/${button.dataset.job}/${button.dataset.action}`, {}); await refresh(); }
        } catch (e) { $("job-note").textContent = e.message; }
        finally { button.disabled = false; }
      }; });
      if (jobs.some((j) => ["queued","running"].includes(j.status))) setTimeout(() => refresh().catch((e) => { $("job-note").textContent = e.message; }), 2000);
    }
    await refresh();
  } catch (e) { $("job-note").textContent = e.message; }
});
