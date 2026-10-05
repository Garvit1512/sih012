// Vercel Build Output API: static UI and a same-origin rewrite to Render.
import { cp, mkdir, rm, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("../", import.meta.url));
const backend = new URL(process.env.SIH_BACKEND_URL || "https://missing.invalid");
if (backend.hostname === "missing.invalid" || backend.protocol !== "https:" || backend.username || backend.password || backend.pathname !== "/" || backend.search || backend.hash) {
  throw new Error("Set SIH_BACKEND_URL to the Render service HTTPS origin, without credentials or a path.");
}
const output = path.join(root, ".vercel/output");
await rm(output, { recursive: true, force: true });
await mkdir(path.join(output, "static"), { recursive: true });
await cp(path.join(root, "app/static"), path.join(output, "static/static"), { recursive: true });
await cp(path.join(root, "app/static/index.html"), path.join(output, "static/index.html"));
await writeFile(path.join(output, "config.json"), JSON.stringify({
  version: 3,
  routes: [
    { src: "/api/(.*)", dest: `${backend.origin}/api/$1` },
    { handle: "filesystem" },
    { src: "/(.*)", status: 404 }
  ]
}, null, 2));
console.log(`Prepared frontend and API rewrite to ${backend.origin}`);
