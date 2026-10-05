/* Persisted, revision-checked workspace adapter. No optimistic geometry changes. */
class WorkspaceClient {
  constructor(siteId) { this.siteId = siteId; this.revision = 0; }
  async load() {
    const r = await fetch("/api/review/workspace?site_id=" + encodeURIComponent(this.siteId));
    const j = await r.json();
    if (!r.ok) throw new Error(j.error?.message || "Workspace unavailable");
    this.revision = j.revision;
    return j;
  }
  async change(path, payload = {}, context = {}) {
    const r = await fetch("/api/review/" + path, { method: "POST", headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ ...payload, ...context, site_id: this.siteId, expected_revision: this.revision }) });
    const j = await r.json();
    if (!r.ok) { const error = new Error(j.error?.message || "HTTP " + r.status); error.code = j.error?.code; throw error; }
    this.revision = j.revision;
    return j;
  }
}
window.WorkspaceClient = WorkspaceClient;
