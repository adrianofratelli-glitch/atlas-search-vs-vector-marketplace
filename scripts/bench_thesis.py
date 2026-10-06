#!/usr/bin/env python3
"""bench_thesis.py — measured evidence for "Atlas Search + Vector in one database".

Two questions a skeptical architect (Elasticsearch/OpenSearch + pgvector) asks:

1. "Hybrid in one query — so what?"  Compares, on the same corpus, native
   $rankFusion (ONE aggregation, fusion on the server) with the classic
   two-engine pattern (lexical query + vector query + fusion in the app),
   which is what a search engine plus a separate vector store forces you to do.
   Read-only: safe on the demo database.

2. "How long until a new product is searchable, and who keeps the copies in
   sync?"  Inserts ONE document and polls until it is returned by $search
   (lexical) AND by $vectorSearch (autoEmbed generates the embedding inside
   Atlas). No CDC, no queue, no embedding job. Writes: *_test databases only.

Usage:
  python scripts/bench_thesis.py --db marketplace_test --runs 15 --freshness
  python scripts/bench_thesis.py --db POC --runs 15        # latency only, read-only
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
import uuid

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"), override=False)


def pct(values, p):
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return values[k]


def latency(atlas, runs: int, queries: list[str]) -> None:
    native, app = [], []
    for i in range(runs):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        r1 = atlas.hybrid_native(q)
        native.append((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
        r2 = atlas.hybrid_rrf(q, k=60, n_search=20, n_vector=20)
        app.append((time.perf_counter() - t0) * 1000)
        if not r1.get("native"):
            print(f"  ! $rankFusion caiu no fallback: {r1.get('reason')}")
        if r2.get("error"):
            print(f"  ! RRF na aplicação falhou: {r2.get('error')}")
    print(f"\n1) híbrido, {runs} execuções por modo (corpus: produtos_vector, mesmas queries)")
    for name, vals, trips in (("$rankFusion nativo", native, 1), ("2 queries + RRF no app", app, 2)):
        print(f"   {name:<24} p50 {statistics.median(vals):7.0f} ms · p95 {pct(vals, 95):7.0f} ms · "
              f"round-trips ao banco: {trips}")


def freshness(db, timeout_s: float) -> None:
    token = "Zylquor" + uuid.uuid4().hex[:6]
    doc = {"produto_id": str(uuid.uuid4()), "nome": f"{token} Garrafa Térmica — Azul",
           "descricao": (f"Garrafa térmica {token} de aço inox que mantém o café quente por 12 horas, "
                         "ideal para levar ao escritório, trilhas e viagens longas."),
           "categoria": "Casa & Cozinha", "preco": 129.9, "em_estoque": True, "avaliacao_media": 4.7,
           "marca": token}
    print(f"\n2) frescor: 1 insert em produtos_vector, poll a cada 250 ms até {timeout_s:.0f}s")
    t0 = time.perf_counter()
    db.produtos_vector.insert_one(doc)
    insert_ms = (time.perf_counter() - t0) * 1000
    lex_ms = vec_ms = None
    try:
        while time.perf_counter() - t0 < timeout_s and (lex_ms is None or vec_ms is None):
            if lex_ms is None:
                hit = list(db.produtos_vector.aggregate([
                    {"$search": {"index": "produtos_vector_search", "text": {"query": token, "path": "nome"}}},
                    {"$limit": 1}, {"$project": {"_id": 0, "produto_id": 1}}], maxTimeMS=5000))
                if hit and hit[0]["produto_id"] == doc["produto_id"]:
                    lex_ms = (time.perf_counter() - t0) * 1000
            if vec_ms is None:
                hit = list(db.produtos_vector.aggregate([
                    {"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": doc["descricao"],
                                       "numCandidates": 100, "limit": 5}},
                    {"$project": {"_id": 0, "produto_id": 1}}], maxTimeMS=10000))
                if any(h["produto_id"] == doc["produto_id"] for h in hit):
                    vec_ms = (time.perf_counter() - t0) * 1000
            time.sleep(0.25)
    finally:
        db.produtos_vector.delete_one({"produto_id": doc["produto_id"]})
    fmt = lambda v: f"{v / 1000:.1f} s" if v is not None else f"> {timeout_s:.0f} s (não encontrado)"  # noqa: E731
    print(f"   insert_one: {insert_ms:.0f} ms")
    print(f"   visível no $search (lexical):            {fmt(lex_ms)}")
    print(f"   visível no $vectorSearch (autoEmbed):     {fmt(vec_ms)}")
    print("   pipeline de sync/embedding mantido pela aplicação: nenhum (documento removido ao final)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.getenv("DB_NAME", "POC"))
    ap.add_argument("--runs", type=int, default=15)
    ap.add_argument("--freshness", action="store_true")
    ap.add_argument("--timeout", type=float, default=180)
    args = ap.parse_args()
    os.environ["DB_NAME"] = args.db
    import atlas  # noqa: E402 — reads DB_NAME at import

    print(f"bench_thesis · banco '{args.db}' · {atlas.db.produtos_vector.estimated_document_count():,} docs em produtos_vector")
    atlas.hybrid_native("aquecimento")  # warm the pool/caches outside the measurement
    latency(atlas, args.runs, ["academia em casa", "fone sem fio para correr", "presente para o dia dos pais",
                               "notebook para programar", "cuidados com a pele"])
    if args.freshness:
        if not args.db.endswith("_test"):
            sys.exit("❌ --freshness escreve: só em bancos *_test.")
        freshness(atlas.db, args.timeout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
