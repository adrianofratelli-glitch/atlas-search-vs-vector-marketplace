# Search & AI agent PoV on MongoDB Atlas

Most e-commerce stacks glue together a search engine, a vector database, an analytics warehouse, and a store for agent memory. This PoV runs all four on MongoDB Atlas alone, over a synthetic catalog of 20 million products.

Seven tabs, one Atlas capability in each. Every screen prints the MQL that actually ran; nothing is mocked. Point it at any dataset through `MONGODB_URI` / `DB_NAME`.

```
React + LeafyGreen  ──axios──►  FastAPI  ──►  MongoDB Atlas
     (:5273)                     (:8200)
```

The UI text is in Brazilian Portuguese on purpose (Brazilian audience); the documentation and code are in English unless noted.

## The demo, tab by tab

**1. Atlas Search**: full-text over the catalog with autocomplete, fuzzy matching (`"adidass"` → Adidas), clickable facets via `$searchMeta`, highlighting, match counts, and `scoreDetails`. Filters run inside `$search` when the index allows it, so counts reflect them; otherwise the application falls back to the other path and says so.

![Atlas Search tab: facets, highlights, and total match count](docs/screenshots/atlas-search.png)

**2. Search vs Vector**: the same query on both engines, side by side. Exact-phrase lexical search returns **zero** for `"academia em casa"` ("home gym"); vector search understands the intent. Each engine reports its own latency.

![Lexical search returning zero next to vector search returning relevant products](docs/screenshots/search-vs-vector.png)

**3. Hybrid, RRF vs RSF**: two native engines side by side. `$rankFusion` (RRF) combines by rank position; `$scoreFusion` (RSF) combines by real scores normalized with `minMaxScaler`. It falls back to RRF computed in the application, with the reason shown in the UI, when the native stage's requirements are not met (no lexical index on the vector collection, or a cluster version below the minimum).

![Hybrid tab running native $rankFusion, with a real rank_vector and no "NA"](docs/screenshots/hybrid-rrf.png)

A synthetic catalog with near-identical text across products in the same subcategory produces mass ties in the Atlas Search lexical score, and a tied score collapses `$rankFusion` (same rank for everyone) and `$scoreFusion`/`minMaxScaler` (`0/0 → 0` for everyone). This was fixed in data generation (`populate_marketplace.py`: attributes plus a second axis of variation per product); see [ADR-001](docs/adr/0001-rankfusion-vs-scorefusion.md) for the full root cause and before/after evidence. `$scoreFusion` now produces a differentiated real score:

![$scoreFusion with a differentiated real score per product, no collapse to zero](docs/screenshots/hybrid-scorefusion.png)

Some residual ties remain: with finite templates there is always **some** group of documents with identical text for a fixed query. That is a property of BM25 over a finite vocabulary, not a bug. The UI shows it honestly instead of hiding it:

![$rankFusion showing a tied rank_search when the query lands in the residual group: correct server behavior on this data](docs/screenshots/hybrid-rankfusion-residual.png)

**4. Similar products**: vector "more like this" from a product's description, with category and stock filters running *inside* `$vectorSearch`, not after it.

![Similar-product results with the pre-filter applied inside $vectorSearch](docs/screenshots/similares.png)

**5. Analytics**: a `$facet` pipeline running several aggregations in parallel on the server. The default is a 12k `$sample`; toggle to run over the whole collection and compare timings.

![Analytics tab: parallel $facet aggregations over the catalog](docs/screenshots/analytics.png)

**6. Review RAG**: `$search` finds the most relevant product that has reviews, MongoDB returns the reviews, and Claude summarizes strictly grounded in that data.

**7. AI agent**: a LangGraph ReAct agent with four MongoDB tools, long-term memory through `MongoDBSaver`, and a trace built by the same functions the tools execute, byte for byte what ran.

![AI agent tab with tool calls and the MQL trace](docs/screenshots/ai-agent.png)

## Collections

```
POC
├── produtos          20M products      — Atlas Search: produtos_search
├── produtos_vector   500K subset       — Vector Search: produtos_vector (voyage-4, autoEmbed)
│                                       — Atlas Search: produtos_vector_search
├── avaliacoes        reviews           — review RAG + agent
└── checkpoints       LangGraph memory
```

The 500K vector subset is a cost/build-time decision, not a limit: it is a representative `$sample`. The extra lexical index on `produtos_vector` exists because `$rankFusion` and `$scoreFusion` need both sub-pipelines on the same collection. The application detects available indexes through `$listSearchIndexes` and degrades gracefully.

## Setup

Requires Atlas 8.0+ (8.1+ for native `$rankFusion`/`$scoreFusion`), Python 3.11+, Node 18+, and an Anthropic key.

`.env` at the repository root:

```env
MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/
DB_NAME=POC
ANTHROPIC_API_KEY=sk-ant-...
```

```bash
python3 setup_search_indexes.py    # once, idempotent; --status to follow progress
bash start.sh                      # backend + frontend → http://localhost:5273
```

The launcher serves the optimized frontend build without a watcher; use `POV_DEV=1 bash start.sh` for HMR. The build is only redone when sources, lockfile, or configuration change. Custom ports: `BACKEND_PORT=8201 FRONTEND_PORT=5274 bash start.sh`.

## Synonyms (optional)

The synonyms toggle needs a mapping named `sinonimos_produtos` on `produtos_search`: Atlas UI → Atlas Search → Synonyms → source collection `sinonimos`, analyzer `lucene.portuguese`. Then insert documents like this:

```json
[
  { "mappingType": "equivalent", "synonyms": ["notebook", "laptop", "computador portátil"] },
  { "mappingType": "equivalent", "synonyms": ["celular", "smartphone", "telefone"] },
  { "mappingType": "explicit", "input": ["presente"], "synonyms": ["kit", "combo", "caixa"] }
]
```

The index is rebuilt in about two minutes; the toggle warns while the build is in progress.

## Stack

React 18 + Vite + LeafyGreen · FastAPI · LangGraph (ReAct) · Claude Sonnet 4.6 · Voyage `voyage-4` via Atlas autoEmbed · MongoDB Atlas 8.0+.

## Production boundary

MongoDB and LLM calls have pool limits, socket and model timeouts, and retries; the AI routes share a bounded-concurrency gate and return 429 under saturation. Aggregation errors are sanitized before reaching clients. The image runs as UID 10001, but the API has no user authentication: put it behind an IdP/API gateway, TLS, and per-tenant quotas before any external exposure.

Component details: [`frontend/README.md`](frontend/README.md) · [`backend/README.md`](backend/README.md).

## License

MIT, see [LICENSE](LICENSE).
