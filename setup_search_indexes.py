"""
setup_search_indexes.py — applies the search-index changes the demo expects.

  1. produtos_search (on `produtos`) — PATCHES the existing definition in place:
       • categoria   → [stringFacet, token]   (enables `in` inside compound.filter)
       • preco       → [numberFacet, number]  (enables `range` inside compound.filter)
       • produto_id  → token
     Everything else in the live definition (synonyms, analyzers, …) is preserved.
     Atlas rebuilds in the background and keeps serving the old index meanwhile.

  2. produtos_vector_search (on `produtos_vector`) — CREATES a lexical index so
     hybrid search runs both engines over the same corpus and native $rankFusion
     (8.1+) works. Skipped if a lexical index already exists on the collection.

Idempotent — safe to run multiple times.

Usage:
    python3 setup_search_indexes.py            # apply + poll status for 3 min
    python3 setup_search_indexes.py --status   # just show current index status
"""

import os
import sys
import time
from pymongo import MongoClient
from pymongo.operations import SearchIndexModel
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME     = os.getenv("DB_NAME", "POC")

if not MONGODB_URI:
    sys.exit("❌ MONGODB_URI não definido — crie o .env na raiz (veja .env.example).")

client = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000)
db = client[DB_NAME]

VECTOR_SEARCH_INDEX_NAME = "produtos_vector_search"

# Vector index with Atlas autoEmbed (voyage-4) — same definition shape Atlas
# reports via $listSearchIndexes for autoEmbed indexes
VECTOR_INDEX_NAME = "produtos_vector"
VECTOR_INDEX_DEF = {
    "fields": [
        {"type": "autoEmbed", "modality": "text", "model": "voyage-4", "path": "descricao"},
        {"type": "filter", "path": "categoria"},
        {"type": "filter", "path": "preco"},
        {"type": "filter", "path": "em_estoque"},
    ]
}

# Lexical index for produtos_vector — mirrors produtos_search minus synonyms
VECTOR_SEARCH_INDEX_DEF = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "nome": [
                {"type": "autocomplete", "analyzer": "lucene.standard",
                 "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15},
                {"type": "string", "analyzer": "lucene.standard"},
            ],
            "descricao":  {"type": "string", "analyzer": "lucene.portuguese"},
            "marca":      {"type": "string"},
            "produto_id": {"type": "token"},
            "categoria":  {"type": "token"},
            "em_estoque": {"type": "boolean"},
            "preco":      {"type": "number"},
        },
    }
}

# Desired extra types on produtos_search (merged into the live definition)
PRODUTOS_SEARCH_PATCH = {
    "categoria":  [{"type": "stringFacet"}, {"type": "token"}],
    "preco":      [{"type": "numberFacet"}, {"type": "number"}],
    "produto_id": {"type": "token"},
}

# Full definition, used when produtos_search doesn't exist yet
PRODUTOS_SEARCH_FULL_DEF = {
    "mappings": {
        "dynamic": False,
        "fields": {
            "nome": [
                {"type": "autocomplete", "analyzer": "lucene.standard",
                 "tokenization": "edgeGram", "minGrams": 2, "maxGrams": 15},
                {"type": "string", "analyzer": "lucene.standard"},
            ],
            "descricao":   {"type": "string", "analyzer": "lucene.portuguese"},
            "marca":       {"type": "string"},
            "produto_id":  {"type": "token"},
            "categoria":   [{"type": "stringFacet"}, {"type": "token"}],
            "subcategoria": {"type": "stringFacet"},
            "genero":      {"type": "stringFacet"},
            "em_estoque":  {"type": "boolean"},
            "preco":       [{"type": "numberFacet"}, {"type": "number"}],
            "avaliacao_media": {"type": "number"},
        },
    },
    "synonyms": [
        {"name": "sinonimos_produtos", "analyzer": "lucene.standard",
         "source": {"collection": "sinonimos"}},
    ],
}


def list_indexes(coll):
    try:
        return list(db[coll].aggregate([{"$listSearchIndexes": {}}]))
    except Exception as e:
        print(f"  ⚠ não consegui listar índices de {coll}: {e}")
        return []


def show_status():
    for coll in ["produtos", "produtos_vector"]:
        print(f"\n  {coll}:")
        idx = list_indexes(coll)
        if not idx:
            print("    (nenhum índice de busca)")
        for ix in idx:
            print(f"    • {ix.get('name'):28s} {ix.get('type', 'search'):13s} "
                  f"{ix.get('status'):10s} queryable={ix.get('queryable')}")


def _as_list(spec):
    return spec if isinstance(spec, list) else [spec]


def patch_produtos_search():
    live = next((ix for ix in list_indexes("produtos") if ix.get("name") == "produtos_search"), None)
    if live is None:
        db["produtos"].create_search_index(
            SearchIndexModel(definition=PRODUTOS_SEARCH_FULL_DEF,
                             name="produtos_search", type="search")
        )
        print("  🔄 produtos_search não existia — criado do zero (com os tipos filtráveis).")
        return True

    definition = live.get("latestDefinition") or {}
    fields = definition.setdefault("mappings", {}).setdefault("fields", {})

    changed = False
    for path, desired in PRODUTOS_SEARCH_PATCH.items():
        current = _as_list(fields.get(path)) if path in fields else []
        current_types = {c.get("type") for c in current if isinstance(c, dict)}
        for spec in _as_list(desired):
            if spec["type"] not in current_types:
                current.append(spec)
                changed = True
        fields[path] = current if len(current) > 1 else current[0]

    if not changed:
        print("  ✅ produtos_search já tem os tipos necessários — nada a fazer.")
        return False

    db["produtos"].update_search_index("produtos_search", definition)
    print("  🔄 produtos_search atualizado — o Atlas reconstrói em background")
    print("     (o índice antigo continua servindo consultas até o novo ficar READY).")
    return True


def create_vector_index():
    """Vector Search index with autoEmbed (voyage-4). Building it re-embeds the
    whole collection — ~30-50 min for 200-500K docs."""
    existing = [ix for ix in list_indexes("produtos_vector") if ix.get("type") == "vectorSearch"]
    if existing:
        names = ", ".join(ix.get("name") for ix in existing)
        print(f"  ✅ produtos_vector já tem índice vetorial ({names}) — nada a fazer.")
        return False
    db["produtos_vector"].create_search_index(
        SearchIndexModel(definition=VECTOR_INDEX_DEF,
                         name=VECTOR_INDEX_NAME, type="vectorSearch")
    )
    print(f"  🔄 {VECTOR_INDEX_NAME} criado — autoEmbed vai embeddar a coleção "
          f"(~30-50 min para 200K docs). Requer a integração Voyage AI ativa no Atlas.")
    return True


def create_vector_search_index():
    existing = list_indexes("produtos_vector")
    lexical = [ix for ix in existing if ix.get("type") != "vectorSearch"]
    if lexical:
        names = ", ".join(ix.get("name") for ix in lexical)
        print(f"  ✅ produtos_vector já tem índice lexical ({names}) — nada a fazer.")
        return False

    db["produtos_vector"].create_search_index(
        SearchIndexModel(definition=VECTOR_SEARCH_INDEX_DEF,
                         name=VECTOR_SEARCH_INDEX_NAME, type="search")
    )
    print(f"  🔄 {VECTOR_SEARCH_INDEX_NAME} criado em produtos_vector — build em andamento.")
    return True


def poll(minutes=3):
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        idx = list_indexes("produtos") + list_indexes("produtos_vector")
        pending = [ix for ix in idx if ix.get("status") not in ("READY",)]
        if not pending:
            print("\n  ✅ Todos os índices READY.")
            return
        names = ", ".join(f"{ix.get('name')}={ix.get('status')}" for ix in pending)
        print(f"  ⏳ aguardando: {names}")
        time.sleep(20)
    print(f"\n  ℹ Builds continuam em background (produtos_search em 20M docs pode "
          f"levar bem mais que {minutes} min). O app detecta automaticamente via "
          f"$listSearchIndexes — acompanhe pelo KPI 'Índices Ativos' ou rode:\n"
          f"     python3 setup_search_indexes.py --status")


if __name__ == "__main__":
    print(f"  Cluster: {MONGODB_URI[:42]}…  db: {DB_NAME}")
    if "--status" in sys.argv:
        show_status()
        sys.exit(0)

    print("\n0️⃣  índices B-tree produto_id (lookups exatos e join de avaliações)")
    for _coll in ("produtos", "produtos_vector", "avaliacoes"):
        db[_coll].create_index("produto_id")
        print(f"  ✓ {_coll}.produto_id")

    print("\n1️⃣  produtos_search (filtros dentro do $search)")
    changed_1 = patch_produtos_search()

    print("\n2️⃣  produtos_vector (Vector Search · autoEmbed voyage-4)")
    changed_2 = create_vector_index()

    print("\n3️⃣  produtos_vector_search (hybrid no mesmo corpus + $rankFusion nativo)")
    changed_3 = create_vector_search_index()

    if changed_1 or changed_2 or changed_3:
        print()
        poll(minutes=3)
    else:
        show_status()
