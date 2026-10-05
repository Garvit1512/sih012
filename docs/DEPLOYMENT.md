# Vercel frontend and Render backend

Vercel serves the existing static interface. Its Build Output API configuration rewrites every `/api/*` request to the Render HTTPS origin. Cookies, imagery tiles and downloads remain on the frontend origin; no backend credentials are embedded in the JavaScript. The current API uses one local JSON writer and a subprocess worker, so Render runs **one Uvicorn process**, with the worker in that same service.

## Render

Deploy the public repository with the Docker runtime and `Dockerfile`. The container installs `requirements-app.txt`; weights, source imagery, local datasets and the optional PyTorch runtime are excluded. It supports onboarding, imported extraction layers, review, parcels and exports. Learned-model jobs remain unavailable until their runtime and verified weights are provisioned separately. Cloud training is not enabled by this deployment.

The checked-in `render.yaml` configures a **free synthetic demo**. On first start, `SIH_SEED_DEMO=1` generates CC0 imagery and evidence; those are software fixtures, not survey observations. `SIH_STORAGE_DIR=/tmp/sih` and `SIH_STORAGE_TEMPORARY=1` make the transient nature explicit in the UI. Edits/uploads disappear when that instance restarts or is replaced. Export results before leaving. The shared workspace password must be at least 16 characters; a Blueprint generates it as `SIH_APP_PASSWORD`. Obtain it through Render's environment settings, and keep it out of Git and frontend build variables.

For durable review, use a paid single-instance service, attach a persistent disk at `/var/sih`, set `SIH_STORAGE_DIR=/var/sih`, and set `SIH_STORAGE_TEMPORARY=0`. All site assets, jobs, model provisioning, review journals and exports reside beneath that directory. Configure `/api/health` as the health-check path. Render provides `PORT`; the entrypoint binds to `0.0.0.0`. A disk cannot be shared with a second service, so do not move this file-backed worker to a separate Render worker. Restarted running jobs are marked interrupted and can be retried.

Shared-password sessions last 12 hours, use Secure/HttpOnly/SameSite cookies, and protect data, uploads, editing and downloads. The health endpoint and frontend assets are public. This is a private pilot login, not individual reviewer accounts or a production access-control system. Keep one reviewer active at a time and retain offline backups/exports.

Render plans and disk charges require the deployment owner's selection before creating paid resources. See [Render persistent disks](https://render.com/docs/disks) and [Render Docker services](https://render.com/docs/docker).

## Vercel

Use the repository root, framework **Other**, and the build settings from `vercel.json`. Set **`SIH_BACKEND_URL`** to the Render service origin, for example `https://your-service.onrender.com`, with no path, credentials, query or fragment. Set it for each deployment environment and redeploy after changing it.

```sh
SIH_BACKEND_URL=https://your-service.onrender.com node deployment/build.mjs
vercel deploy --prod
```

On Windows PowerShell, set `$env:SIH_BACKEND_URL` before the build. The build creates `.vercel/output/static` and `.vercel/output/config.json` and refuses a missing or invalid backend origin. The `/api` rewrite lets the existing forms, tile URLs, cookie sign-in and download links work without a frontend rewrite or permissive CORS.

Verify the production root, sign-in, `/api/health`, site listing, map tiles, editing/reload and a downloaded export through the **Vercel URL**. On the durable plan, also restart the Render instance and verify the same saved revision. A successful frontend build alone does not establish a connected backend or persistence.

Large GeoTIFF imports and long inference requests must respect both providers' proxy/runtime limits. Test a small permitted site first; transfer large assets directly to backend storage through a controlled provisioning process. Secrets, `.vercel` account metadata, datasets and model binaries stay outside Git.
