/* Site onboarding also works when there is no imagery or model bundle installed. */
window.siteSetup = (async function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  let response;
  try { response = await fetch("/api/sites"); } catch (_) { return null; }
  if (response.status === 401) {
    const overlay = document.createElement("div");
    overlay.className = "workspace-login";
    overlay.innerHTML = '<form><h2>Review workspace sign-in</h2><p>Enter the workspace password supplied by the deployment owner.</p><label>Password<input type="password" autocomplete="current-password" required></label><button class="btn" type="submit">Sign in</button><p role="status" aria-live="polite"></p></form>';
    document.body.append(overlay);
    await new Promise((resolve) => {
      const form = overlay.querySelector("form"), input = form.querySelector("input"), note = form.querySelector('[role="status"]'), button = form.querySelector("button");
      input.focus();
      form.onsubmit = async (event) => {
        event.preventDefault(); button.disabled = true; note.textContent = "Signing in…";
        try {
          const result = await fetch("/api/auth/login", { method: "POST", headers: {"Content-Type":"application/json"}, body: JSON.stringify({password: input.value}) });
          if (!result.ok) throw new Error("Sign-in failed. Check the password and try again.");
          input.value = ""; overlay.remove(); resolve();
        } catch (error) { note.textContent = error.message; button.disabled = false; }
      };
    });
    response = await fetch("/api/sites");
  }
  if (response.status === 404) return null; // legacy app/server.py
  if (!response.ok) throw new Error("Site catalog unavailable (HTTP " + response.status + ")");
  try {
    const health = await (await fetch("/api/health")).json();
    if (health.deployment?.temporary_storage) {
      const notice = document.createElement("div");
      notice.className = "deployment-notice";
      notice.textContent = "Temporary hosted demo · edits and uploads reset when the backend restarts. Export your work before leaving.";
      document.querySelector("#site-controls summary").after(notice);
    }
  } catch (_) { /* The health chip reports connection failures separately. */ }
  const { sites } = await response.json();
  $("site-controls").hidden = false;
  const requested = new URL(location.href).searchParams.get("site");
  const site = sites.find((s) => s.id === requested) || sites[0];
  if (requested && !sites.some((s) => s.id === requested)) $("site-result").textContent = "Requested site was not found; showing the first available site.";
  $("site-select").innerHTML = sites.map((s) => `<option value="${esc(s.id)}" ${site && s.id === site.id ? "selected" : ""}>${esc(s.name)}</option>`).join("") || '<option>No sites yet</option>';
  $("site-select").disabled = !sites.length;
  $("site-select").onchange = () => { const url = new URL(location.href); url.searchParams.set("site", $("site-select").value); location.assign(url); };
  $("site-import").open = !sites.length;
  $("site-form").onsubmit = async (e) => {
    e.preventDefault();
    const button = $("site-submit"), result = $("site-result");
    button.disabled = true; result.textContent = "Uploading and preparing imagery…";
    try {
      const data = new FormData(e.target);
      for (const [key, value] of Array.from(data.entries())) if (value instanceof File && !value.size) data.delete(key);
      const r = await fetch("/api/sites", { method: "POST", body: data });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error?.message || "HTTP " + r.status);
      const url = new URL(location.href); url.searchParams.set("site", j.id); location.assign(url);
    } catch (err) { result.textContent = "Import failed: " + err.message; button.disabled = false; }
  };
  if (!site) {
    $("hdr-context").textContent = "Add a georeferenced site to begin";
    $("hdr-conn").textContent = "Server connected · no sites";
    $("project-info").innerHTML = "<dt>Status</dt><dd>Ready to import RGB imagery</dd>";
    document.querySelectorAll(".btn").forEach((b) => { if (b.id !== "site-submit") b.disabled = true; });
    document.querySelectorAll(".map-label").forEach((el) => { el.textContent = "No site loaded"; });
  }
  return { siteId: site?.id || null, sites };
})();
