# Changelog

## 1.2.0 (2026-10-09)

- Catalog mirror: `backend/catalog_sync.py` keeps `produtos_vector` in step with `produtos` through a change stream in the API process (insert/replace/update upserted by `produto_id`, deletes propagated, resume token in `catalog_sync_state`, `CATALOG_SYNC=0` to revert). A product inserted in `produtos` now reaches `$vectorSearch`.
- `scripts/reset_demo.py --rebuild-catalog` pauses the mirror and skips the seed's writes; every reset enables pre-images on `produtos` and the `produto_id` index on `produtos_vector`. The `$sample` copy keeps the source `_id`.
- `scripts/bench_thesis.py --freshness` also measures the mirror path (copy, `$vectorSearch`, delete).
- README and briefing: the two-collection catalog and its sync are described as they are; the thesis no longer says "no sync".
- `pov-shared` 0.2.1 (editable).

## 1.1.0 (2026-10-06)

- UI: layout MongoDB 2026 "Dark Stage v4" (tokens mais escuros, Special Gothic / Source Code Pro locais, motivos de escada e grade, movimento escalonado).
- LLM only through the Grove gateway (`grove_client` from `pov-shared`): retry, circuit breaker and model fallback on by default; default model `claude-sonnet-5-5`; no fallback to a personal key.
- MongoDB: readable "Atlas unreachable" errors, one retry for transient and autoEmbed/Voyage throttling errors, faster degraded `/stats`.
- Agent: PII masked before the model, checkpoint and trace; injection heuristic; ReAct step limit; dedicated checkpoint collections with a 30-day TTL; trace and token metrics scoped to the current turn.
- Review RAG: reviews that look like prompt injection stay out of the prompt and are flagged in the UI.
- Optional Langfuse tracing (v2, fail-open, after PII masking).
- API hardening: normalized and bounded inputs, `NaN`/`Infinity` refused (used to cause a 500), 64 KB body limit, 422 without payload echo, sanitized `X-Request-Id`, single-flight analytics.
- `scripts/reset_demo.py`: one guarded, idempotent reset (agent memory, catalog, synonyms, search indexes, READY wait, smoke). Seeders refuse non-`_test` databases without `ALLOW_DEMO_DB_WRITE=1`.
- `scripts/bench_thesis.py`: measured freshness (one insert to `$search`/`$vectorSearch`) and native vs. application hybrid latency.
- Adversarial unit suite and Playwright end-to-end walkthrough; axios 1.20 (npm audit 0).

## 1.0.0 (2026-09-30)

First public release.

- Repository rebuilt with a clean, single-commit history.
- English README and repository description, with screenshots captured against a real Atlas cluster.
- MIT license.
- Internal notes, presentation decks, test-output snapshots, and tooling configuration removed from the repository.
