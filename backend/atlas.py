"""
atlas.py — MongoDB Atlas data-access layer.
Atlas Search, Vector Search, Hybrid RRF, and facet pipelines,
exposed as pure functions for the FastAPI layer.
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pymongo import MongoClient
from pymongo.errors import PyMongoError, ExecutionTimeout
from dotenv import load_dotenv

# Load the .env at the project root (self-contained — does not depend on main.py)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

logger = logging.getLogger("searchxvector.atlas")

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME     = os.getenv("DB_NAME", "POC")
QUERY_TIMEOUT_MS = 10_000

_client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=15000,
    maxPoolSize=max(1, int(os.getenv("MONGODB_MAX_POOL_SIZE", "50"))),
)
db = _client[DB_NAME]  # lazy connection — only connects on the first query


# ── Safe execution helper ────────────────────────────────────────────────────
def safe_aggregate(collection: str, pipeline: list):
    """Run an aggregate with friendly error handling. Returns (results, error).

    NOTE (known ambiguity): a 10s ExecutionTimeout does not tell us whether the
    bottleneck was the MongoDB cluster itself or the external embedding
    provider (Voyage AI, via autoEmbed on $vectorSearch/$search). The driver
    does not currently surface that distinction in the exception; explain()
    would be needed to separate "cluster-side" from "provider-side" latency,
    and we don't run explain() on the hot path. Flagging this for whoever
    debugs a timeout spike later — check Atlas metrics for autoEmbed latency
    before assuming a cluster-side regression.
    """
    try:
        return list(db[collection].aggregate(pipeline, maxTimeMS=QUERY_TIMEOUT_MS)), None
    except ExecutionTimeout:
        return None, "Operação cancelada — tempo limite de 10s atingido."
    except PyMongoError as e:
        msg = str(e)
        if "index not found" in msg.lower() or "no such index" in msg.lower():
            # The lexical/vector index on this collection may be mid-rebuild or
            # dropped — the 60s TTL cache in get_search_indexes() could still be
            # holding a stale "index exists" view. Force the next read to hit
            # $listSearchIndexes for real, so "same corpus" badges and native
            # $rankFusion eligibility reflect the current state, not a cached one.
            invalidate_index_cache(collection)
            return None, "Índice não encontrado. Verifique se o Search/Vector index está READY no Atlas."
        if "synonym" in msg.lower():
            return None, "synonym-analyzer"  # flag for graceful fallback
        logger.warning("aggregate rejected collection=%s", collection, exc_info=True)
        return None, "Consulta MongoDB rejeitada. Consulte o request-id nos logs do backend."
    except Exception:
        logger.exception("aggregate failed collection=%s", collection)
        return None, "Falha interna ao executar a consulta MongoDB."


# ── Collection stats ─────────────────────────────────────────────────────────
def get_stats() -> tuple[dict, bool]:
    """Returns (counts, degraded). degraded=True means the cluster itself was
    unreachable (not just an empty collection) — the caller uses this instead
    of relying on an HTTP failure, since this endpoint always returns 200."""
    out, degraded = {}, False
    for c in ["produtos", "produtos_vector", "avaliacoes"]:
        try:
            out[c] = db[c].estimated_document_count()
        except Exception:
            logger.exception("estimated_document_count failed collection=%s", c)
            out[c] = 0
            degraded = True
    return out, degraded


# ── Search-index introspection ($listSearchIndexes) ─────────────────────────
# The demo adapts to whatever indexes exist on the cluster: filters move inside
# $search only if the field types support it, and the hybrid tabs go native
# ($rankFusion) only if produtos_vector also has a lexical search index.
_INDEX_CACHE_TTL = 60
_index_cache = {}  # collection -> {"ts": float, "indexes": list}

def get_search_indexes(collection: str) -> list:
    now = time.time()
    hit = _index_cache.get(collection)
    if hit and now - hit["ts"] < _INDEX_CACHE_TTL:
        return hit["indexes"]
    try:
        idx = list(db[collection].aggregate([{"$listSearchIndexes": {}}], maxTimeMS=5000))
    except Exception:
        logger.exception("listSearchIndexes failed collection=%s", collection)
        idx = []
    _index_cache[collection] = {"ts": now, "indexes": idx}
    return idx


def invalidate_index_cache(collection: str | None = None) -> None:
    """Drop the cached $listSearchIndexes result so the next read reflects the
    real index state instead of waiting out the TTL. Called when a query fails
    with an index-not-found error — the strongest live signal that an index is
    being rebuilt/dropped and the cached view (used for "same corpus" badges
    and native $rankFusion eligibility) is stale."""
    if collection is None:
        _index_cache.clear()
    else:
        _index_cache.pop(collection, None)


def get_index_status() -> list:
    """Real status of every search/vector index (READY, BUILDING, …)."""
    out = []
    for coll in ["produtos", "produtos_vector"]:
        for ix in get_search_indexes(coll):
            out.append({
                "name": ix.get("name"),
                "collection": coll,
                "type": "Vector Search" if ix.get("type") == "vectorSearch" else "Atlas Search",
                "status": ix.get("status", "UNKNOWN"),
                "queryable": bool(ix.get("queryable")),
            })
    return out


def _field_types(index_doc: dict, path: str) -> set:
    """Set of mapping types declared for a field in a lexical search index."""
    fields = (index_doc.get("latestDefinition") or {}).get("mappings", {}).get("fields", {})
    spec = fields.get(path)
    if spec is None:
        return set()
    specs = spec if isinstance(spec, list) else [spec]
    return {s.get("type") for s in specs if isinstance(s, dict)}


def search_filter_caps() -> dict:
    """Which filters produtos_search can run INSIDE $search (compound.filter)."""
    for ix in get_search_indexes("produtos"):
        if ix.get("name") == "produtos_search":
            return {
                "preco_range": "number" in _field_types(ix, "preco"),
                "categoria_in": "token" in _field_types(ix, "categoria"),
                "em_estoque_eq": "boolean" in _field_types(ix, "em_estoque"),
            }
    return {"preco_range": False, "categoria_in": False, "em_estoque_eq": False}


def vector_collection_search_index() -> str | None:
    """Name of a LEXICAL search index on produtos_vector, if one exists.
    Required for same-corpus hybrid and for native $rankFusion (both
    sub-pipelines must run against the same collection)."""
    for ix in get_search_indexes("produtos_vector"):
        if ix.get("type") != "vectorSearch" and ix.get("queryable"):
            return ix.get("name")
    return None


# ── Analytics — Aggregation Framework ($facet) ───────────────────────────────
def get_analytics(full: bool = False) -> dict:
    """A single $facet runs several aggregations in parallel on the server.
    full=False → $sample of 12k docs (instant, demo default)
    full=True  → whole collection, to show the same pipeline over 20M docs"""
    pipeline = [
        {"$facet": {
            "por_categoria": [
                {"$group": {"_id": "$categoria", "total": {"$sum": 1},
                            "preco_medio": {"$avg": "$preco"},
                            "avaliacao_media": {"$avg": "$avaliacao_media"}}},
                {"$sort": {"total": -1}},
            ],
            "top_marcas": [
                {"$group": {"_id": "$marca", "total": {"$sum": 1}}},
                {"$sort": {"total": -1}}, {"$limit": 8},
            ],
            "faixa_preco": [
                {"$bucket": {
                    "groupBy": "$preco",
                    "boundaries": [0, 100, 500, 1000, 3000, 5000, 10000, 999999],
                    "default": "outros",
                    "output": {"total": {"$sum": 1}},
                }},
            ],
            "por_mes": [
                {"$match": {"created_at": {"$type": "date"}}},
                {"$group": {"_id": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}},
                            "total": {"$sum": 1}}},
                {"$sort": {"_id": 1}}, {"$limit": 12},
            ],
            "geral": [
                {"$group": {"_id": None,
                            "total": {"$sum": 1},
                            "preco_medio": {"$avg": "$preco"},
                            "desconto_medio": {"$avg": "$desconto_pct"},
                            "em_estoque": {"$sum": {"$cond": ["$em_estoque", 1, 0]}}}},
            ],
        }},
    ]
    if full:
        run_pipeline, timeout = pipeline, 60000
    else:
        # Representative sample keeps the tab instant on 20M docs
        run_pipeline, timeout = [{"$sample": {"size": 12000}}] + pipeline, 25000
    t0 = time.time()
    try:
        res = list(db["produtos"].aggregate(run_pipeline, maxTimeMS=timeout, allowDiskUse=True))
    except Exception:
        logger.exception("analytics aggregate failed full=%s", full)
        return {"error": "Falha ao executar a agregação analítica."}
    elapsed = (time.time() - t0) * 1000
    data = res[0] if res else {}
    geral = (data.get("geral") or [{}])[0]
    # $bucket omits EMPTY buckets, so labels must be keyed by the _id (lower
    # boundary) — mapping by array position shifts every label after a gap.
    faixa_labels = {0: "R$ 0–100", 100: "R$ 100–500", 500: "R$ 500–1K", 1000: "R$ 1K–3K",
                    3000: "R$ 3K–5K", 5000: "R$ 5K–10K", 10000: "R$ 10K+"}
    faixa = data.get("faixa_preco", [])
    return {
        "por_categoria": data.get("por_categoria", []),
        "top_marcas": data.get("top_marcas", []),
        "faixa_preco": [{"label": faixa_labels.get(b.get("_id"), str(b.get("_id"))),
                         "total": b.get("total", 0)} for b in faixa],
        "por_mes": data.get("por_mes", []),
        "geral": {
            # `or 0` — $avg over a field absent from the docs returns None
            "preco_medio": round(geral.get("preco_medio") or 0, 2),
            "desconto_medio": round(geral.get("desconto_medio") or 0, 1),
            "em_estoque_pct": round(100 * (geral.get("em_estoque") or 0) / max(geral.get("total") or 1, 1), 1),
            "amostra": geral.get("total", 0),
        },
        "full": full,
        "elapsed_ms": round(elapsed),
        "pipeline": run_pipeline,
    }


def _num_candidates(limit: int, multiplier: int = 10) -> int:
    """numCandidates for $vectorSearch, derived from `limit`.

    MongoDB's guidance for $vectorSearch is to oversample the candidate pool
    10x-20x the requested `limit`, then let HNSW/ANN narrow it down — too low
    a multiplier hurts recall, too high wastes latency. 10x is the default here
    (matches the previous hybrid_rrf/hybrid_native behavior); callers can pass
    a higher multiplier for an operation-specific reason (e.g. a smaller result
    set where the extra recall is cheap), but that choice should be measured,
    not guessed — leave a comment at the call site explaining why.
    """
    return max(1, limit) * multiplier


# ── Recommendations — Vector "more like this" with PRE-FILTERING ──────────────
def find_similar(produto_id: str = None, nome: str = None, same_category: bool = True) -> dict:
    """Semantically similar products (autoEmbed). Demonstrates VECTOR PRE-FILTERING:
    the filter (category / in-stock) runs INSIDE $vectorSearch — semantic plus
    structured filtering in a single operation, with no application-side post-filter."""
    # Find the base product via Atlas Search (indexed) — NOT via a $match on an
    # unindexed field, which would trigger a collection scan over millions of docs.
    # Prefer looking it up in produtos_vector itself: the categoria used in the
    # pre-filter then belongs to the SAME collection being filtered.
    lex_index = vector_collection_search_index()
    base_coll = "produtos_vector" if lex_index else "produtos"
    base_index = lex_index or "produtos_search"
    if not lex_index:
        # Cross-collection heuristic: base product comes from `produtos` (20M
        # docs) but the $vectorSearch below runs on `produtos_vector` (a 500K
        # $sample) — different textual distributions, so relevance can degrade
        # silently. Logged for visibility, not corrected here (see audit #4).
        logger.info(
            "find_similar: no lexical index on produtos_vector — using cross-collection "
            "heuristic (base from produtos, vector search on produtos_vector) produto_id=%s nome=%s",
            produto_id, nome,
        )
    proj = {"$project": {"_id": 0, "nome": 1, "descricao": 1, "categoria": 1,
                         "preco": 1, "produto_id": 1}}
    if produto_id:
        base, err = safe_aggregate(base_coll, [
            {"$match": {"produto_id": produto_id}}, {"$limit": 1}, proj,
        ])
    else:
        # name match boosted, but description text also counts — "notebook"
        # finds a Dell XPS even when no product NAME contains the word
        base, err = safe_aggregate(base_coll, [
            {"$search": {"index": base_index, "compound": {"should": [
                {"autocomplete": {"query": nome, "path": "nome",
                                  "fuzzy": {"maxEdits": 1}, "score": {"boost": {"value": 3}}}},
                {"text": {"query": nome, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
            ], "minimumShouldMatch": 1}}},
            {"$limit": 1}, proj,
        ])
    if err or not base:
        return {"error": err or "Produto não encontrado", "base": None, "similares": []}
    b = base[0]

    # Vector search on the meaning of the base product's description.
    # NATIVE PRE-FILTERING: the filter (category + in-stock) runs INSIDE
    # $vectorSearch — the index stores those fields as `filter`. Semantic plus
    # structured filtering in a single operation, with no application-side post-filter.
    vector_stage = {"$vectorSearch": {
        "index": "produtos_vector", "path": "descricao",
        "query": b.get("descricao", b["nome"]),
        # ~22x multiplier: recommendations run over a category pre-filter, so
        # extra oversampling compensates for candidates the filter discards.
        "numCandidates": _num_candidates(9, multiplier=22), "limit": 9,
    }}
    pre_filter = None
    if same_category:
        pre_filter = {"categoria": b.get("categoria"), "em_estoque": True}
        vector_stage["$vectorSearch"]["filter"] = pre_filter

    pipeline = [
        vector_stage,
        {"$project": {"_id": 0, "nome": 1, "marca": 1, "categoria": 1, "preco": 1,
                      "avaliacao_media": 1, "produto_id": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]
    sim, err2 = safe_aggregate("produtos_vector", pipeline)
    if err2:
        return {"error": err2, "base": b, "similares": []}

    # exclude the base product by id (names collide in a generated catalog)
    similares = [s for s in (sim or []) if s.get("produto_id") != b.get("produto_id")][:8]
    return {"base": b, "similares": similares, "filtered": same_category,
            "pre_filter": pre_filter, "pipeline": pipeline}


# ── Product reviews (for RAG) ────────────────────────────────────────────────
# Only a small subset of products has reviews. Cache that subset (catalog by id
# + id set) with a TTL, so review lookups always land on a product that HAS
# reviews — the demo never hits an empty "0 reviews" state — and a data reload
# doesn't require restarting the backend.
_REVIEWED_TTL = 600
_reviewed_cache = {"ts": 0.0, "by_id": None}

def _get_reviewed():
    """dict produto_id -> product doc, for every product that has reviews."""
    now = time.time()
    if _reviewed_cache["by_id"] is not None and now - _reviewed_cache["ts"] < _REVIEWED_TTL:
        return _reviewed_cache["by_id"]
    try:
        # distinct uses the produto_id index (~0.2s) instead of a $group scan
        id_list = db["avaliacoes"].distinct("produto_id")
    except Exception:
        logger.exception("distinct produto_id failed")
        id_list = []
    prods, _ = safe_aggregate("produtos", [
        {"$match": {"produto_id": {"$in": id_list}}},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "marca": 1,
                      "categoria": 1, "preco": 1, "avaliacao_media": 1, "total_avaliacoes": 1}},
    ])
    _reviewed_cache["by_id"] = {p["produto_id"]: p for p in (prods or [])}
    _reviewed_cache["ts"] = now
    return _reviewed_cache["by_id"]


# Fallback scan is O(n) over the reviewed catalog (Python sort, not indexed).
# Concurrent calls hitting the fallback for the SAME query (typical during an
# Atlas Search outage — many requests fall back at once) would otherwise each
# redo the full scan. Cache the winning produto_id per query for a short TTL.
_FALLBACK_TTL = 30
_fallback_cache = {}  # query(lower/stripped) -> {"ts": float, "produto_id": str}


def get_product_and_reviews(query: str, n_reviews: int = 8) -> dict:
    """Find the most relevant product THAT HAS reviews and fetch them (top by
    helpfulness). Relevance comes from a REAL Atlas Search query: we take the
    top-300 $search candidates and pick the best-ranked one that has reviews
    (only ~2% of the catalog is reviewed, so the pool must be generous).
    Falls back to an in-memory match over the reviewed catalog only if the
    search index is unavailable."""
    by_id = _get_reviewed()
    if not by_id:
        return {"error": "Nenhum produto com avaliações", "produto": None, "reviews": []}

    search_pipeline = [
        {"$search": {"index": "produtos_search", "compound": {"should": [
            {"autocomplete": {"query": query, "path": "nome",
                              "fuzzy": {"maxEdits": 1}, "score": {"boost": {"value": 3}}}},
            {"text": {"query": query, "path": ["descricao", "marca"], "fuzzy": {"maxEdits": 1}}},
        ], "minimumShouldMatch": 1}}},
        {"$limit": 300},
        {"$project": {"_id": 0, "produto_id": 1, "score": {"$meta": "searchScore"}}},
    ]
    candidates, err = safe_aggregate("produtos", search_pipeline)

    produto, via = None, "atlas_search"
    if not err:
        for c in candidates or []:
            hit = by_id.get(c.get("produto_id"))
            if hit:
                produto = hit
                break

    if produto is None:
        # index unavailable or no reviewed product in the top 50 → keyword match
        # over the reviewed catalog (never leaves the user with 0 reviews)
        via = "catalog_fallback"
        q = query.lower().strip()
        now = time.time()
        cached = _fallback_cache.get(q)
        if cached and now - cached["ts"] < _FALLBACK_TTL and cached["produto_id"] in by_id:
            produto = by_id[cached["produto_id"]]
        else:
            catalog = list(by_id.values())
            tokens = [t for t in q.split() if len(t) > 2]
            def score(p):
                blob = f"{p.get('nome','')} {p.get('marca','')} {p.get('categoria','')}".lower()
                s = sum(1 for t in tokens if t in blob)
                if q in blob:
                    s += 3
                return s
            ranked = sorted(catalog, key=score, reverse=True)
            produto = ranked[0] if score(ranked[0]) > 0 else \
                max(catalog, key=lambda p: int(p.get("total_avaliacoes", 0) or 0))
            _fallback_cache[q] = {"ts": now, "produto_id": produto["produto_id"]}

    reviews_pipeline = [
        {"$match": {"produto_id": produto["produto_id"]}},
        {"$sort": {"util_count": -1}},
        {"$limit": n_reviews},
        {"$project": {"_id": 0, "nota": 1, "titulo": 1, "texto": 1, "util_count": 1,
                      "verificado": 1, "usuario": 1}},
    ]
    reviews, _ = safe_aggregate("avaliacoes", reviews_pipeline)
    return {"produto": produto, "reviews": reviews or [], "via": via,
            "pipeline": {"busca_produto": search_pipeline, "avaliacoes": reviews_pipeline}}


# ── Atlas Search (tab 1) ─────────────────────────────────────────────────────
# BUSINESS-SIGNAL scoring: multiply text relevance by the product rating
# (avaliacao_media). Well-rated products rank higher — e-commerce relevance
# tuning driven by a business rule, not text relevance alone.
def _business_score() -> dict:
    return {"function": {
        "multiply": [
            {"score": "relevance"},
            {"path": {"value": "avaliacao_media", "undefined": 3.0}},
        ]
    }}


def build_search_op(query: str, with_synonyms: bool = False, boost_business: bool = True) -> dict:
    if with_synonyms:
        return {"text": {"query": query, "path": ["nome", "descricao"],
                         "synonyms": "sinonimos_produtos"}}
    nome_score = _business_score() if boost_business else {"boost": {"value": 2}}
    return {
        "compound": {
            "should": [
                {"autocomplete": {"query": query, "path": "nome",
                                  "fuzzy": {"maxEdits": 1}, "score": nome_score}},
                {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
            ],
            "minimumShouldMatch": 1,
        }
    }


def build_filters(categorias, preco_min, preco_max, only_stock, caps: dict):
    """Split the filters between compound.filter (index-level, inside $search)
    and a post-$search $match, based on what the index supports.
    Index-level filters keep $$SEARCH_META.count truthful."""
    search_filters, mql_filter = [], {}

    if caps.get("preco_range"):
        search_filters.append({"range": {"path": "preco", "gte": preco_min, "lte": preco_max}})
    else:
        mql_filter["preco"] = {"$gte": preco_min, "$lte": preco_max}

    if only_stock:
        if caps.get("em_estoque_eq"):
            search_filters.append({"equals": {"path": "em_estoque", "value": True}})
        else:
            mql_filter["em_estoque"] = True

    if categorias:
        if caps.get("categoria_in"):
            search_filters.append({"in": {"path": "categoria", "value": categorias}})
        else:
            mql_filter["categoria"] = {"$in": categorias}

    return search_filters, mql_filter


def _apply_search_filters(search_op: dict, search_filters: list) -> dict:
    """Attach compound.filter clauses to a search operator (wrapping non-compound
    operators, e.g. the synonyms `text`, in a compound.must)."""
    if not search_filters:
        return search_op
    if "compound" in search_op:
        op = {"compound": {**search_op["compound"], "filter": search_filters}}
    else:
        op = {"compound": {"must": [search_op], "filter": search_filters}}
    return op


def build_search_pipeline(search_op: dict, mql_filter: dict) -> list:
    pipeline = [
        {"$search": {
            "index": "produtos_search",
            **search_op,
            "count": {"type": "total"},
            "highlight": {"path": ["nome", "descricao"], "maxCharsToExamine": 500, "maxNumPassages": 1},
            "scoreDetails": True,   # transparency: WHY this product ranked where it did
        }},
    ]
    if mql_filter:
        pipeline.append({"$match": mql_filter})
    pipeline += [
        {"$limit": 50},
        {"$addFields": {"_total_matches": "$$SEARCH_META.count.total"}},
        {"$project": {
            "_id": 0,
            "nome": 1, "marca": 1, "categoria": 1, "subcategoria": 1,
            "preco": 1, "preco_original": 1, "desconto_pct": 1,
            "avaliacao_media": 1, "total_avaliacoes": 1,
            "em_estoque": 1, "score": {"$meta": "searchScore"},
            "highlights": {"$meta": "searchHighlights"},
            "scoreDetails": {"$meta": "searchScoreDetails"},
            "_total_matches": 1,
        }},
    ]
    return pipeline


def atlas_search(query: str, categorias=None, preco_min=0, preco_max=15000,
                 only_stock=True, with_synonyms=False) -> dict:
    caps = search_filter_caps()
    search_filters, mql_filter = build_filters(categorias, preco_min, preco_max, only_stock, caps)

    search_op = _apply_search_filters(build_search_op(query, with_synonyms), search_filters)
    pipeline  = build_search_pipeline(search_op, mql_filter)

    t0 = time.time()
    results, err = safe_aggregate("produtos", pipeline)
    elapsed = (time.time() - t0) * 1000

    synonyms_fallback = False
    if err == "synonym-analyzer" and with_synonyms:
        synonyms_fallback = True
        search_op = _apply_search_filters(build_search_op(query, False), search_filters)
        pipeline  = build_search_pipeline(search_op, mql_filter)
        t0 = time.time()
        results, err = safe_aggregate("produtos", pipeline)
        elapsed = (time.time() - t0) * 1000

    if err:
        return {"error": err, "results": [], "pipeline": pipeline}

    total = results[0].get("_total_matches") if results else 0
    return {
        "results": results or [],
        "total_matches": total,
        # True → count reflects the filters (they ran inside $search).
        # False → count is pre-filter (post-$match fallback); UI flags it.
        "filters_in_search": not mql_filter,
        "elapsed_ms": round(elapsed),
        "synonyms_fallback": synonyms_fallback,
        "pipeline": pipeline,
    }


# ── $searchMeta facets ───────────────────────────────────────────────────────
def search_facets(query: str, with_synonyms=False) -> dict:
    search_op = build_search_op(query, with_synonyms)
    meta_pipeline = [{"$searchMeta": {
        "index": "produtos_search",
        "facet": {
            "operator": search_op,
            "facets": {
                "categorias":   {"type": "string", "path": "categoria", "numBuckets": 10},
                "faixas_preco": {"type": "number", "path": "preco",
                                 "boundaries": [0, 100, 500, 1000, 3000, 5000, 10000, 15000]},
            },
        },
    }}]
    meta, err = safe_aggregate("produtos", meta_pipeline)
    if err or not meta:
        return {"error": err, "categorias": [], "faixas_preco": []}
    data = meta[0]
    return {
        "total": data.get("count", {}).get("lowerBound", 0),
        "categorias":   data.get("facet", {}).get("categorias", {}).get("buckets", []),
        "faixas_preco": data.get("facet", {}).get("faixas_preco", {}).get("buckets", []),
    }


# ── Search vs Vector vs RRF (tab 2) ──────────────────────────────────────────
def _rrf_fuse(text_res: list, vec_res: list, k: int = 60, limit: int = 10) -> list:
    """Reciprocal Rank Fusion keyed by produto_id (names can collide in a
    template-generated catalog)."""
    def key(doc):
        return doc.get("produto_id") or doc.get("nome")

    rrf_map, seen_s, seen_v = {}, set(), set()
    for rank, doc in enumerate(text_res or []):
        kk = key(doc)
        if kk in seen_s:
            continue
        seen_s.add(kk)
        rrf_map.setdefault(kk, {"doc": doc, "rrf": 0, "s": None, "v": None})
        rrf_map[kk]["rrf"] += 1 / (k + rank + 1)
        rrf_map[kk]["s"] = rank + 1
    for rank, doc in enumerate(vec_res or []):
        kk = key(doc)
        if kk in seen_v:
            continue
        seen_v.add(kk)
        rrf_map.setdefault(kk, {"doc": doc, "rrf": 0, "s": None, "v": None})
        rrf_map[kk]["rrf"] += 1 / (k + rank + 1)
        rrf_map[kk]["v"] = rank + 1
    return sorted(rrf_map.values(), key=lambda x: x["rrf"], reverse=True)[:limit]


def compare_search_vector(query: str, mode: str = "phrase") -> dict:
    """Lexical vs semantic, side by side.
    mode="phrase"   → exact phrase on nome (the classic '0 results' moment)
    mode="compound" → the same e-commerce operator from tab 1 (fair comparison)
    When produtos_vector also has a lexical index, both engines run over the
    SAME corpus, so the RRF column compares like with like."""
    proj = {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "marca": 1, "categoria": 1,
                         "preco": 1, "avaliacao_media": 1, "score": {"$meta": "searchScore"}}}
    if mode == "compound":
        search_op = build_search_op(query, with_synonyms=False, boost_business=False)
    else:
        search_op = {"phrase": {"query": query, "path": "nome"}}

    same_index = vector_collection_search_index()
    search_coll = "produtos_vector" if same_index else "produtos"
    search_index = same_index or "produtos_search"

    search_pipeline = [
        {"$search": {"index": search_index, **search_op}},
        {"$limit": 10}, proj,
    ]
    vector_pipeline = [
        {"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                           "query": query,
                           # 15x multiplier: unfiltered comparison, matches the
                           # ratio this tab shipped with.
                           "numCandidates": _num_candidates(10, multiplier=15), "limit": 10}},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "marca": 1, "categoria": 1, "preco": 1,
                      "avaliacao_media": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]

    t0 = time.time()
    text_res, err_s = safe_aggregate(search_coll, search_pipeline)
    t_search = (time.time() - t0) * 1000
    t0 = time.time()
    vec_res, err_v = safe_aggregate("produtos_vector", vector_pipeline)
    t_vector = (time.time() - t0) * 1000

    fused = _rrf_fuse(text_res, vec_res, k=60, limit=10)

    # Machine-readable degradation signal — err_s/err_v are nested under
    # search/vector already, but the UI needs a top-level flag to badge
    # "index missing/building" instead of rendering a silent "no results".
    degraded = {"search": err_s, "vector": err_v} if (err_s or err_v) else None

    return {
        "search":  {"results": text_res or [], "error": err_s,
                    "pipeline": search_pipeline, "elapsed_ms": round(t_search)},
        "vector":  {"results": vec_res or [],  "error": err_v,
                    "pipeline": vector_pipeline, "elapsed_ms": round(t_vector)},
        "hybrid":  [{"nome": x["doc"].get("nome"), "categoria": x["doc"].get("categoria"),
                     "preco": x["doc"].get("preco"), "rrf": round(x["rrf"], 5),
                     "rank_search": x["s"], "rank_vector": x["v"],
                     "both": bool(x["s"] and x["v"])} for x in fused],
        "mode": mode,
        # True → both engines queried the same 500K collection (honest fusion).
        "same_corpus": bool(same_index),
        "elapsed_ms": round(t_search + t_vector),
        "degraded": degraded,
    }


# ── Tunable Hybrid RRF (tab 3) ───────────────────────────────────────────────
def hybrid_rrf(query: str, k=60, n_search=20, n_vector=20) -> dict:
    # Same corpus for both engines whenever produtos_vector has a lexical index
    same_index = vector_collection_search_index()
    search_coll = "produtos_vector" if same_index else "produtos"
    search_index = same_index or "produtos_search"

    s_pipe = [
        {"$search": {"index": search_index, "compound": {"should": [
            {"autocomplete": {"query": query, "path": "nome",
                              "fuzzy": {"maxEdits": 1}, "score": {"boost": {"value": 3}}}},
            {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
        ]}}},
        {"$limit": n_search},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                      "search_score": {"$meta": "searchScore"}}},
    ]
    v_pipe = [
        {"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                           "query": query, "numCandidates": _num_candidates(n_vector), "limit": n_vector}},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                      "vector_score": {"$meta": "vectorSearchScore"}}},
    ]

    t0 = time.time()
    # As duas pernas são independentes — em paralelo o híbrido responde no
    # tempo da perna mais lenta, não na soma das duas.
    with ThreadPoolExecutor(max_workers=2) as pool:
        search_future = pool.submit(safe_aggregate, search_coll, s_pipe)
        vector_res, err_v = safe_aggregate("produtos_vector", v_pipe)
        search_res, err_s = search_future.result()
    elapsed = (time.time() - t0) * 1000

    if err_s or err_v:
        # Name which engine failed and why — a bare "err_s or err_v" hides
        # whether it was the lexical or the vector side (or both).
        parts = []
        if err_s:
            parts.append(f"busca textual: {err_s}")
        if err_v:
            parts.append(f"busca vetorial: {err_v}")
        reason = " · ".join(parts)
        return {"error": reason, "reason": reason, "fused": [], "counts": {}}

    fused = _rrf_fuse(search_res, vector_res, k=k, limit=20)

    only_s = sum(1 for x in fused if x["s"] and not x["v"])
    only_v = sum(1 for x in fused if x["v"] and not x["s"])
    both   = sum(1 for x in fused if x["s"] and x["v"])

    return {
        "fused": [{"nome": x["doc"].get("nome"), "categoria": x["doc"].get("categoria"),
                   "preco": x["doc"].get("preco"), "rrf": round(x["rrf"], 5),
                   "rank_search": x["s"], "rank_vector": x["v"],
                   "both": bool(x["s"] and x["v"]),
                   "s_score": round(1/(k + x["s"]), 5) if x["s"] else 0,
                   "v_score": round(1/(k + x["v"]), 5) if x["v"] else 0} for x in fused],
        "counts": {"only_search": only_s, "only_vector": only_v, "both": both,
                   "n_search": len(search_res), "n_vector": len(vector_res)},
        "same_corpus": bool(same_index),
        "elapsed_ms": round(elapsed),
        "k": k,
    }


# ── Hybrid via NATIVE $rankFusion — with graceful fallback ───────────────────
def _parse_rank_fusion_details(doc: dict) -> dict:
    """Extract per-pipeline ranks from $rankFusion scoreDetails (defensively —
    the exact shape varies by server version)."""
    out = {"rank_search": None, "rank_vector": None}
    sd = doc.get("scoreDetails") or {}
    details = sd.get("details")
    if not details:
        # Unexpected shape for this Atlas version — silently leaving rank_search/
        # rank_vector as None would make "both" report False even when the doc
        # DID appear in both sub-pipelines. Log the shape we actually got so a
        # future debugging session can see what changed across Atlas versions.
        logger.warning(
            "rankFusion scoreDetails missing 'details' — unexpected shape for this "
            "Atlas version. scoreDetails keys=%s scoreDetails=%r",
            list(sd.keys()), sd,
        )
        return out
    for d in details:
        name = d.get("inputPipelineName", "")
        rank = d.get("rank")
        if not isinstance(rank, int):  # absent pipelines report rank "NA"
            rank = None
        if name == "textual":
            out["rank_search"] = rank
        elif name == "semantico":
            out["rank_vector"] = rank
    return out


def hybrid_native(query: str, limit: int = 20) -> dict:
    """Hybrid search with the NATIVE $rankFusion stage — the fusion happens
    server-side, in one aggregation, with no application code. Requirements:
      • MongoDB 8.1+ ($rankFusion stage)
      • BOTH indexes on the SAME collection (sub-pipelines share the target
        collection) — i.e. a lexical search index on produtos_vector.
    Anything missing → falls back to the application RRF and says why."""
    search_index = vector_collection_search_index()
    if not search_index:
        fb = hybrid_rrf(query, k=60, n_search=limit, n_vector=limit)
        return {
            "native": False,
            "reason": ("$rankFusion exige os dois índices na MESMA coleção — crie um "
                       "índice Atlas Search em produtos_vector (veja populate_marketplace.py). "
                       "Exibindo RRF calculado na aplicação."),
            "fused": fb.get("fused", []), "counts": fb.get("counts", {}),
            "elapsed_ms": fb.get("elapsed_ms", 0), "k": 60, "pipeline": None,
        }

    pipeline = [
        {"$rankFusion": {
            "input": {"pipelines": {
                "textual": [
                    {"$search": {"index": search_index, "compound": {"should": [
                        {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}}},
                        {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
                    ]}}},
                    {"$limit": limit},
                ],
                "semantico": [
                    {"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                                       "query": query, "numCandidates": _num_candidates(limit), "limit": limit}},
                ],
            }},
            "combination": {"weights": {"textual": 1, "semantico": 1}},
            "scoreDetails": True,
        }},
        {"$limit": limit},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                      "score": {"$meta": "score"},
                      "scoreDetails": {"$meta": "scoreDetails"}}},
    ]
    t0 = time.time()
    results, err = safe_aggregate("produtos_vector", pipeline)
    elapsed = (time.time() - t0) * 1000

    if err:
        # e.g. MongoDB 8.0 → no $rankFusion stage
        fb = hybrid_rrf(query, k=60, n_search=limit, n_vector=limit)
        return {
            "native": False,
            "reason": f"$rankFusion indisponível neste cluster ({err[:120]}) — usando RRF na aplicação.",
            "fused": fb.get("fused", []), "counts": fb.get("counts", {}),
            "elapsed_ms": fb.get("elapsed_ms", 0), "k": 60, "pipeline": pipeline,
        }

    fused = []
    for doc in results or []:
        ranks = _parse_rank_fusion_details(doc)
        fused.append({
            "nome": doc.get("nome"), "categoria": doc.get("categoria"),
            "preco": doc.get("preco"), "rrf": round(doc.get("score", 0), 5),
            "rank_search": ranks["rank_search"], "rank_vector": ranks["rank_vector"],
            "both": bool(ranks["rank_search"] and ranks["rank_vector"]),
            "s_score": round(1 / (60 + ranks["rank_search"]), 5) if ranks["rank_search"] else 0,
            "v_score": round(1 / (60 + ranks["rank_vector"]), 5) if ranks["rank_vector"] else 0,
        })
    only_s = sum(1 for x in fused if x["rank_search"] and not x["rank_vector"])
    only_v = sum(1 for x in fused if x["rank_vector"] and not x["rank_search"])
    both   = sum(1 for x in fused if x["rank_search"] and x["rank_vector"])

    return {
        "native": True,
        "fused": fused,
        "counts": {"only_search": only_s, "only_vector": only_v, "both": both,
                   "n_search": limit, "n_vector": limit},
        "elapsed_ms": round(elapsed), "k": 60, "pipeline": pipeline,
    }


# ── Hybrid via NATIVE $scoreFusion (Relative Score Fusion) ──────────────────
def _parse_score_fusion_details(doc: dict) -> dict:
    """Extract per-pipeline normalized + raw scores from $scoreFusion scoreDetails —
    defensively, since the exact shape varies by server version."""
    out = {"s_score": None, "v_score": None, "s_raw": None, "v_raw": None}
    sd = doc.get("scoreDetails") or {}
    details = sd.get("details")
    if not details:
        logger.warning(
            "scoreFusion scoreDetails missing 'details' — unexpected shape for this "
            "Atlas version. scoreDetails keys=%s scoreDetails=%r",
            list(sd.keys()), sd,
        )
        return out
    for d in details:
        name = d.get("inputPipelineName", "")
        value = d.get("value")
        raw = d.get("inputPipelineRawScore")
        if not isinstance(value, (int, float)):  # absent pipelines report "NA"
            value = None
        if not isinstance(raw, (int, float)):
            raw = None
        if name == "textual":
            out["s_score"] = value
            out["s_raw"] = raw
        elif name == "semantico":
            out["v_score"] = value
            out["v_raw"] = raw
    return out


def hybrid_score_fusion(query: str, limit: int = 20) -> dict:
    """Hybrid search with the NATIVE $scoreFusion stage — Relative Score
    Fusion (RSF). Unlike $rankFusion (rank-only), this combines the actual
    relevance SCORE of each sub-pipeline: normalizes both to 0-1 (minMaxScaler)
    then averages the weighted values. Same same-collection requirement as
    $rankFusion. Anything missing → falls back to the application RRF."""
    search_index = vector_collection_search_index()
    if not search_index:
        fb = hybrid_rrf(query, k=60, n_search=limit, n_vector=limit)
        return {
            "native": False,
            "reason": ("$scoreFusion exige os dois índices na MESMA coleção — crie um "
                       "índice Atlas Search em produtos_vector (veja populate_marketplace.py). "
                       "Exibindo RRF calculado na aplicação."),
            "fused": fb.get("fused", []), "counts": fb.get("counts", {}),
            "elapsed_ms": fb.get("elapsed_ms", 0), "pipeline": None,
        }

    pipeline = [
        {"$scoreFusion": {
            "input": {
                "pipelines": {
                    "textual": [
                        {"$search": {"index": search_index, "compound": {"should": [
                            {"autocomplete": {"query": query, "path": "nome", "fuzzy": {"maxEdits": 1}}},
                            {"text": {"query": query, "path": "descricao", "fuzzy": {"maxEdits": 1}}},
                        ]}}},
                        {"$limit": limit},
                    ],
                    "semantico": [
                        {"$vectorSearch": {"index": "produtos_vector", "path": "descricao",
                                           "query": query, "numCandidates": _num_candidates(limit), "limit": limit}},
                    ],
                },
                "normalization": "minMaxScaler",
            },
            "combination": {"weights": {"textual": 1, "semantico": 1}, "method": "avg"},
            "scoreDetails": True,
        }},
        {"$limit": limit},
        {"$project": {"_id": 0, "produto_id": 1, "nome": 1, "categoria": 1, "preco": 1,
                      "score": {"$meta": "score"},
                      "scoreDetails": {"$meta": "scoreDetails"}}},
    ]
    t0 = time.time()
    results, err = safe_aggregate("produtos_vector", pipeline)
    elapsed = (time.time() - t0) * 1000

    if err:
        # e.g. cluster below the $scoreFusion minimum version
        fb = hybrid_rrf(query, k=60, n_search=limit, n_vector=limit)
        return {
            "native": False,
            "reason": f"$scoreFusion indisponível neste cluster ({err[:120]}) — usando RRF na aplicação.",
            "fused": fb.get("fused", []), "counts": fb.get("counts", {}),
            "elapsed_ms": fb.get("elapsed_ms", 0), "pipeline": pipeline,
        }

    fused = []
    for doc in results or []:
        scores = _parse_score_fusion_details(doc)
        fused.append({
            "nome": doc.get("nome"), "categoria": doc.get("categoria"),
            "preco": doc.get("preco"), "score": round(doc.get("score", 0), 5),
            "s_score": round(scores["s_score"], 5) if scores["s_score"] is not None else 0,
            "v_score": round(scores["v_score"], 5) if scores["v_score"] is not None else 0,
            "both": bool(scores["s_score"] is not None and scores["v_score"] is not None),
        })
    only_s = sum(1 for x in fused if x["s_score"] and not x["v_score"])
    only_v = sum(1 for x in fused if x["v_score"] and not x["s_score"])
    both   = sum(1 for x in fused if x["both"])

    # Observed bug in this cluster's $scoreFusion: minMaxScaler normalization can
    # collapse EVERY document's combined score to 0 — even documents with a
    # genuinely non-zero raw score in one sub-pipeline (verified by inspecting
    # scoreDetails directly: inputPipelineRawScore > 0 but value == 0 regardless).
    # Detect the visible symptom (every combined score is 0) rather than silently
    # showing a meaningless, non-discriminating ranking.
    degraded_reason = None
    if fused and all(x["score"] == 0 for x in fused):
        degraded_reason = (
            "$scoreFusion executou no servidor, mas a normalização minMaxScaler "
            "colapsou o score combinado de TODOS os resultados para 0 (bug observado "
            "neste cluster — confirmado inspecionando scoreDetails diretamente: "
            "inputPipelineRawScore > 0 em alguns documentos, mas value sempre 0). "
            "O ranking exibido não é discriminativo. Limitação atual do Atlas, não "
            "da aplicação — considere $rankFusion (RRF) até isso ser corrigido."
        )

    return {
        "native": True,
        "degraded_reason": degraded_reason,
        "fused": fused,
        "counts": {"only_search": only_s, "only_vector": only_v, "both": both,
                   "n_search": limit, "n_vector": limit},
        "elapsed_ms": round(elapsed), "pipeline": pipeline,
    }
