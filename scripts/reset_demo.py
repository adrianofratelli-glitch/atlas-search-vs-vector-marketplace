#!/usr/bin/env python3
"""reset_demo.py — single, idempotent command that leaves the demo database clean.

What it does, in order:
  1. Clears this PoV's agent memory (marketplace_checkpoints and
     marketplace_checkpoint_writes). Other collections of a shared database are
     never touched.
  2. Catalog: with --rebuild-catalog (or when `produtos` is empty) drops and
     regenerates produtos, produtos_vector and avaliacoes with
     populate_marketplace.py, plus the regular B-tree indexes.
  3. Synonyms: rewrites the `sinonimos` source collection (7 mappings).
  4. Search indexes: produtos_search, produtos_vector (Vector Search with
     autoEmbed voyage-4: Atlas generates the embeddings while building) and
     produtos_vector_search. Idempotent (setup_search_indexes.py).
  5. Waits until every search index is READY and queryable, then runs one
     $search and one $vectorSearch as a smoke check.

The app itself never writes to the catalog; its change-stream mirror
(backend/catalog_sync.py) only copies writes made on `produtos` into
`produtos_vector`. A rebuild pauses that mirror and records a cluster time so
the seed is never copied (the seed decides the vector sample). A normal reset
(no flag) only clears agent memory, prepares the mirror (pre-images on
produtos, produto_id index on produtos_vector) and verifies indexes: seconds. --rebuild-catalog on the demo
sizes (500K products, a vector index re-embedding 500K descriptions) takes
tens of minutes and consumes Voyage credits through autoEmbed.

Safety: refuses any database that does not end in `_test` unless
ALLOW_DEMO_DB_WRITE=1. Never run it during a live demo.

Usage:
  python scripts/reset_demo.py --db marketplace_test --rebuild-catalog \\
      --products 20000 --vector 3000 --reviews 20000
  ALLOW_DEMO_DB_WRITE=1 python scripts/reset_demo.py            # demo db (DB_NAME)
  ALLOW_DEMO_DB_WRITE=1 python scripts/reset_demo.py --rebuild-catalog \\
      --products 500000 --vector 500000 --reviews 100000        # full demo rebuild
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"), override=False)

from pymongo import MongoClient  # noqa: E402

import populate_marketplace as pop  # noqa: E402
import setup_search_indexes as ssi  # noqa: E402
import catalog_sync  # noqa: E402

CHECKPOINT_COLLECTIONS = (
    os.getenv("AGENT_CHECKPOINT_COLLECTION", "marketplace_checkpoints"),
    os.getenv("AGENT_CHECKPOINT_WRITES_COLLECTION", "marketplace_checkpoint_writes"),
)

SYNONYMS = [  # exactly what the demo database holds (sinonimos_produtos source)
    {"mappingType": "equivalent", "synonyms": ["notebook", "laptop", "computador"]},
    {"mappingType": "equivalent", "synonyms": ["celular", "smartphone", "aparelho", "telefone"]},
    {"mappingType": "equivalent", "synonyms": ["perfume", "fragrância", "cologne"]},
    {"mappingType": "equivalent", "synonyms": ["tênis", "calçado", "sapatênis", "sneaker"]},
    {"mappingType": "equivalent", "synonyms": ["fone", "headphone", "headset", "auricular"]},
    {"mappingType": "equivalent", "synonyms": ["tv", "televisão", "televisor", "smart tv"]},
    {"mappingType": "equivalent", "synonyms": ["academia", "musculação", "fitness", "gym"]},
]

EXPECTED_INDEXES = {
    ("produtos", "produtos_search"),
    ("produtos_vector", "produtos_vector"),
    ("produtos_vector", "produtos_vector_search"),
}


def step(msg: str) -> float:
    print(f"\n▶ {msg}", flush=True)
    return time.time()


def done(t0: float, extra: str = "") -> None:
    print(f"  ✓ {time.time() - t0:.1f}s {extra}", flush=True)


def rebuild_catalog(db, products: int, vector: int, reviews: int) -> None:
    t0 = step(f"catálogo: drop + {products:,} produtos, {vector:,} no subset vetorial, {reviews:,} avaliações")
    for coll in (pop.COL_PRODUTOS, pop.COL_PRODUTOS_VECTOR, pop.COL_AVALIACOES):
        db[coll].drop()  # also drops that collection's search indexes
    pop.VECTOR_SAMPLE_SIZE = min(vector, products)
    pop.TOTAL_DOCS_AVALIACOES = reviews
    pop.BATCH_SIZE = min(pop.BATCH_SIZE, max(1000, products))
    ids = pop.populate_produtos(db, products)
    pop.populate_vector_sample(db)
    pop.populate_avaliacoes(db, ids)
    pop.create_indexes(db)
    done(t0)


def wait_ready(db, minutes: float) -> bool:
    t0 = step(f"aguardando índices READY e queryable (até {minutes:.0f} min)")
    deadline = time.time() + minutes * 60
    last = ""
    while time.time() < deadline:
        state = {}
        for coll in ("produtos", "produtos_vector"):
            for ix in ssi.list_indexes(db, coll):
                state[(coll, ix.get("name"))] = (ix.get("status"), bool(ix.get("queryable")))
        missing = EXPECTED_INDEXES - set(state)
        pending = {k: v for k, v in state.items() if k in EXPECTED_INDEXES and v != ("READY", True)}
        line = ", ".join(f"{n}={s}" for (_, n), (s, _) in sorted(pending.items())) or "-"
        if not missing and not pending:
            done(t0, "todos READY")
            return True
        if line != last:
            print(f"  … pendentes: {line}; ausentes: {sorted(n for _, n in missing) or '-'}", flush=True)
            last = line
        time.sleep(10)
    print("  ✗ tempo esgotado; os builds continuam no Atlas (rode de novo para verificar)")
    return False


def smoke(db) -> bool:
    t0 = step("smoke: um $search e um $vectorSearch")
    ok = True
    s = list(db.produtos.aggregate([
        {"$search": {"index": "produtos_search", "text": {"query": "Samsung", "path": "nome"}}},
        {"$limit": 5}, {"$project": {"_id": 0, "nome": 1}}], maxTimeMS=15000))
    v = list(db.produtos_vector.aggregate([
        {"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": "academia em casa",
                           "numCandidates": 100, "limit": 5}},
        {"$project": {"_id": 0, "nome": 1}}], maxTimeMS=20000))
    print(f"  $search 'Samsung': {len(s)} · $vectorSearch 'academia em casa': {len(v)}")
    if not s or not v:
        ok = False
        print("  ✗ uma das buscas voltou vazia")
    done(t0)
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.getenv("DB_NAME", "POC"), help="banco alvo (padrão: DB_NAME)")
    ap.add_argument("--rebuild-catalog", action="store_true", help="drop + regenerar o catálogo")
    ap.add_argument("--products", type=int, default=int(os.getenv("TOTAL_DOCS", "500000")))
    ap.add_argument("--vector", type=int, default=int(os.getenv("VECTOR_SAMPLE", "500000")))
    ap.add_argument("--reviews", type=int, default=int(os.getenv("TOTAL_AVAL", "100000")))
    ap.add_argument("--wait-minutes", type=float, default=60)
    ap.add_argument("--drop-db", action="store_true",
                    help="só para bancos *_test: apaga o banco inteiro e sai (limpeza de teste)")
    args = ap.parse_args(argv)

    pop.assert_writable_db(args.db)
    uri = os.getenv("MONGODB_URI")
    if not uri:
        sys.exit("❌ MONGODB_URI não definido (veja .env.example).")
    client = MongoClient(uri, serverSelectionTimeoutMS=10000, appname="marketplace-reset")
    db = client[args.db]
    started = time.time()
    print(f"reset_demo · banco '{args.db}'")

    if args.drop_db:
        if not args.db.endswith("_test"):
            sys.exit("❌ --drop-db só é aceito em bancos *_test.")
        client.drop_database(args.db)
        print("  ✓ banco removido")
        return 0

    t0 = step("memória do agente (checkpoints deste PoV)")
    for coll in CHECKPOINT_COLLECTIONS:
        n = db[coll].estimated_document_count()
        db[coll].drop()
        print(f"  · {coll}: {n} doc(s) removido(s)")
    done(t0)

    if args.rebuild_catalog or db.produtos.estimated_document_count() == 0:
        catalog_sync.pause(db)  # a running app must not mirror the seed
        try:
            rebuild_catalog(db, args.products, args.vector, args.reviews)
        finally:
            catalog_sync.resume_from_now(db)

    t0 = step("espelho produtos → produtos_vector (pré-imagens + índice produto_id)")
    catalog_sync.CatalogMirror(db).prepare()
    done(t0)

    t0 = step("sinônimos (coleção sinonimos)")
    db.sinonimos.delete_many({})
    db.sinonimos.insert_many([dict(d) for d in SYNONYMS])
    done(t0, f"{len(SYNONYMS)} mapeamentos")

    t0 = step("índices de busca (idempotente)")
    ssi.apply_all(db, poll_minutes=0)
    done(t0)

    ready = wait_ready(db, args.wait_minutes)
    ok = ready and smoke(db)
    counts = {c: db[c].estimated_document_count() for c in ("produtos", "produtos_vector", "avaliacoes", "sinonimos")}
    print(f"\nresumo: {counts} · {time.time() - started:.1f}s · {'OK' if ok else 'INCOMPLETO'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
