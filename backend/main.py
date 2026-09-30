"""
main.py — FastAPI API for the Search & Vector POC.
Serves the endpoints consumed by the React frontend (axios).

Run:  uvicorn main:app --reload --port 8200
"""

import logging
import os
import time
import uuid
import warnings
from threading import BoundedSemaphore
from uuid import UUID
from uuid import uuid4
from dotenv import load_dotenv

# Load the .env at the project root (one level above backend/)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)
warnings.filterwarnings("ignore")

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, model_validator

import observability
import atlas
from agent import run_agent
from reviews import summarize_reviews

observability.setup_logging()
logger = logging.getLogger("searchxvector")

app = FastAPI(title="Search × Vector POC API", version="1.0")

# Allowed origins — configurable via CORS_ORIGINS (comma-separated list)
_origins = os.getenv("CORS_ORIGINS", "http://localhost:5273,http://127.0.0.1:5273").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def _request_observability(request: Request, call_next):
    """request_id on every response + per-route latency/error counters at /api/metrics."""
    request_id = request.headers.get("x-request-id") or uuid4().hex[:16]
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        observability.metrics.observe(request.url.path, 500, (time.perf_counter() - start) * 1000)
        logger.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    observability.metrics.observe(request.url.path, response.status_code, elapsed_ms)
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/api/metrics")
def api_metrics():
    """In-process counters: requests/errors/latency per route + business counters."""
    return observability.metrics.snapshot()


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics():
    return Response(observability.metrics.prometheus(), media_type="text/plain; version=0.0.4")


# ── Request models ───────────────────────────────────────────────────────────
class SearchReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    categorias: list[str] | None = None
    preco_min: float = Field(0, ge=0)
    preco_max: float = Field(15000, ge=0)
    only_stock: bool = True
    synonyms: bool = False

class CompareReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    mode: str = Field("phrase", pattern="^(phrase|compound)$")

class HybridReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    # Bounded to a practically meaningful RRF range: k<10 is dominated almost
    # entirely by the top-1 rank of each pipeline, k>200 flattens rank
    # differences to the point the fusion stops discriminating between results.
    k: int = Field(60, ge=10, le=200)
    n_search: int = Field(20, ge=1, le=100)
    n_vector: int = Field(20, ge=1, le=100)

class AgentReq(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    thread_id: UUID | None = None

class SimilarReq(BaseModel):
    produto_id: str | None = Field(default=None, min_length=1, max_length=120)
    nome: str | None = Field(default=None, min_length=1, max_length=300)
    same_category: bool = True

    @model_validator(mode="after")
    def require_product_reference(self):
        if not self.produto_id and not self.nome:
            raise ValueError("produto_id or nome is required")
        return self

class ReviewsReq(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)


# ── Routes ───────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        atlas.db.command("ping")
        return {"status": "ok", "db": atlas.DB_NAME}
    except Exception:
        logger.exception("Atlas ping failed")
        return JSONResponse({"status": "degraded", "db": atlas.DB_NAME}, status_code=503)


@app.get("/health/live")
def liveness():
    return {"status": "alive"}

@app.get("/stats")
def stats():
    counts, degraded = atlas.get_stats()
    # Real status via $listSearchIndexes — a building/failed index shows as such
    indices = atlas.get_index_status()
    if not indices:  # cluster without $listSearchIndexes support / no indexes yet
        indices = [
            {"name": "produtos_search", "type": "Atlas Search", "status": "UNKNOWN"},
            {"name": "produtos_vector", "type": "Vector Search", "status": "UNKNOWN"},
        ]
    # degraded=True → the cluster itself is unreachable (this endpoint always
    # returns 200, so the frontend can't rely on an HTTP failure to detect it).
    return {"collections": counts, "indices": indices, "degraded": degraded}

@app.post("/search")
def search(req: SearchReq):
    return atlas.atlas_search(
        req.query, categorias=req.categorias, preco_min=req.preco_min,
        preco_max=req.preco_max, only_stock=req.only_stock, with_synonyms=req.synonyms,
    )

@app.post("/search/facets")
def facets(req: SearchReq):
    return atlas.search_facets(req.query, with_synonyms=req.synonyms)

@app.post("/compare")
def compare(req: CompareReq):
    return atlas.compare_search_vector(req.query, mode=req.mode)

@app.post("/hybrid")
def hybrid(req: HybridReq):
    return atlas.hybrid_rrf(req.query, k=req.k, n_search=req.n_search, n_vector=req.n_vector)

@app.post("/hybrid-native")
def hybrid_native(req: CompareReq):
    """Hybrid search using the NATIVE $rankFusion stage (8.1+), with an RRF fallback on 8.0."""
    return atlas.hybrid_native(req.query)

@app.post("/hybrid-score-fusion")
def hybrid_score_fusion(req: CompareReq):
    """Hybrid search using the NATIVE $scoreFusion stage (Relative Score Fusion), with an
    application-side RRF fallback when the stage or same-corpus index is unavailable."""
    return atlas.hybrid_score_fusion(req.query)

_ai_slots = BoundedSemaphore(max(1, int(os.getenv("AI_MAX_CONCURRENCY", os.getenv("AGENT_MAX_CONCURRENCY", "4")))))


@app.post("/agent")
def agent_route(req: AgentReq):
    if not _ai_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="AI concurrency limit reached; retry shortly.")
    try:
        thread_id = str(req.thread_id or uuid.uuid4())
        out = run_agent(req.message, thread_id)
        out["thread_id"] = thread_id
        return out
    finally:
        _ai_slots.release()

# The analytics $facet is costly to repeat on every refresh; cache 5 min per mode
_analytics_cache = {}  # "sample"/"full" -> {"data": dict, "ts": float}

@app.get("/analytics")
def analytics(full: bool = False):
    key = "full" if full else "sample"
    hit = _analytics_cache.get(key)
    if hit is None or time.time() - hit["ts"] > 300:
        data = atlas.get_analytics(full=full)
        if isinstance(data, dict) and data.get("error"):
            return data  # do not cache errors
        _analytics_cache[key] = {"data": data, "ts": time.time()}
        return data
    return hit["data"]

@app.post("/similar")
def similar(req: SimilarReq):
    return atlas.find_similar(produto_id=req.produto_id, nome=req.nome, same_category=req.same_category)

@app.post("/reviews-rag")
def reviews_rag(req: ReviewsReq):
    if not _ai_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="AI concurrency limit reached; retry shortly.")
    try:
        return summarize_reviews(req.query)
    finally:
        _ai_slots.release()
