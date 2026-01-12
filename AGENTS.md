# Deployment notes for agents

Keep these mistakes in mind when deploying so we don’t repeat them:

- **Missing API env vars (Jan 11, 2026):** Updating `open-notebook-api` without the SurrealDB envs caused it to fall back to `ws://127.0.0.1:8000`, breaking notebook create/delete and models fetch. Always include `SURREAL_URL`, `SURREAL_USER/PASSWORD`, `SURREAL_NAMESPACE/DATABASE`, DB_VM_* and auth envs in every `gcloud run services update`.
- **Worker missing Gemini key (Jan 11, 2026):** `open-notebook-worker` was deployed without `GEMINI_API_KEY`, so all embedding chunks failed with “Google API key not found.” Always set `GEMINI_API_KEY` on the worker.
- **Forced sync processing left on (Jan 11, 2026):** `FORCE_SYNC_PROCESSING=true` was enabled temporarily and forgot to be turned off, bypassing the worker. Only use this flag for emergencies; reset to `false` afterward.
- **Partial env updates wipe vars (Jan 12, 2026):** Using `gcloud run services update --set-env-vars` with a partial list deletes any omitted vars. We lost `GEMINI_API_KEY`, storage, and DB envs, which made the API fall back to localhost and caused embedding jobs to fail. Always pass the complete env set (DB, auth, storage, processing, Gemini) on every update or use `--update-env-vars` with care.
