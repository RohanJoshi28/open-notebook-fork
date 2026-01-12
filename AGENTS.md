# Deployment notes for agents

Keep these mistakes in mind when deploying so we don’t repeat them:

- **Missing API env vars (Jan 11, 2026):** Updating `open-notebook-api` without the SurrealDB envs caused it to fall back to `ws://127.0.0.1:8000`, breaking notebook create/delete and models fetch. Always include `SURREAL_URL`, `SURREAL_USER/PASSWORD`, `SURREAL_NAMESPACE/DATABASE`, DB_VM_* and auth envs in every `gcloud run services update`.
- **Worker missing Gemini key (Jan 11, 2026):** `open-notebook-worker` was deployed without `GEMINI_API_KEY`, so all embedding chunks failed with “Google API key not found.” Always set `GEMINI_API_KEY` on the worker.
- **Forced sync processing left on (Jan 11, 2026):** `FORCE_SYNC_PROCESSING=true` was enabled temporarily and forgot to be turned off, bypassing the worker. Only use this flag for emergencies; reset to `false` afterward.
- **Partial env updates wipe vars (Jan 12, 2026):** Using `gcloud run services update --set-env-vars` with a partial list deletes any omitted vars. We lost `GEMINI_API_KEY`, storage, and DB envs, which made the API fall back to localhost and caused embedding jobs to fail. Always pass the complete env set (DB, auth, storage, processing, Gemini) on every update or use `--update-env-vars` with care.
- **Worker asleep with min_instances=0 (Jan 12, 2026):** When min instances were set to 0 we forgot to ping the worker or warm it, so uploads produced no embeddings/insights. If min_instances=0, send a ping or temporarily set min_instances=1 before ingesting.
- **Stale VM status cache bypassed gate (Jan 12, 2026):** Cached “running” status prevented the start/stop gate from showing when the VM was actually off, exposing the login page and blocking the start button. Always force a live status check on mount and invalidate stale cache.
- **Frontend/backend auth domain mismatch (Jan 12, 2026):** Backend `ALLOWED_GOOGLE_DOMAIN` was `berkeley.edu` while the frontend forced `force10partners.com`, causing Google sign-in failures. Keep the allowed domain consistent across API, frontend, and worker env vars.

## Deployment env checklist (always pass the full set; never a partial `--set-env-vars`)

Shared API & Worker (required):
- `SURREAL_URL`, `SURREAL_USER`, `SURREAL_PASSWORD` (or `SURREAL_PASS`), `SURREAL_NAMESPACE`, `SURREAL_DATABASE`
- `AUTH_JWT_SECRET`
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- `GOOGLE_ALLOWED_DOMAIN` (keep consistent across services)
- `STORAGE_BACKEND`, `GCS_BUCKET_NAME`
- `GEMINI_API_KEY` (and optional `GOOGLE_API_KEY` alias)
- `FORCE_SYNC_PROCESSING` (normally `false`)
- `WORKER_PING_URL` **and** `WORKER_URL` (API pings either; prevents cold-start stalls with min_instances=0)
- `WORKER_PING_TIMEOUT` (e.g., `6`)
- `SOURCE_PROCESS_TIMEOUT` (worker; e.g., `180`)

API-only:
- `DB_VM_PROJECT`, `DB_VM_ZONE`, `DB_VM_NAME`
- `CHAT_TIMEOUT_SECONDS` (optional override)

Frontend:
- `NEXT_PUBLIC_API_URL` and `API_URL`
- `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (or reuse `GOOGLE_CLIENT_ID`)
- `GOOGLE_ALLOWED_DOMAIN` or `NEXT_PUBLIC_GOOGLE_ALLOWED_DOMAIN` (match backend)
- Optional toggles: `NEXT_PUBLIC_DISABLE_DB_VM_GATE`, `NEXT_PUBLIC_FORCE_DB_VM_GATE`, `NEXT_PUBLIC_DEBUG_LOGS`

Principle: every deploy must supply the **complete** env set above (use `--set-env-vars` with the full list or `--update-env-vars` only if you include all existing keys). Missing any of these has previously broken sign-in, DB access, embeddings, or worker wake-ups.
