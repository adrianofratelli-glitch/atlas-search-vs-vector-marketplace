#!/usr/bin/env python3
"""Quality eval of the repo thesis: lexical x vector x hybrid on the same corpus.

For each case in eval/golden.json (query + known target products from the seed
catalog), runs the backend's own search functions (backend/atlas.py, no
reimplemented pipeline) and scores the top 10 of each mode:

  lexical   compare_search_vector(mode="compound") -> "search" leg ($search on produtos_vector)
  vector    compare_search_vector(mode="compound") -> "vector" leg ($vectorSearch autoEmbed)
  hybrid    hybrid_native(limit=10)                -> native $rankFusion (falls back to app RRF, flagged)
  hybrid_app_rrf  the RRF column of compare_search_vector (informative)
  hybrid_rerank   hybrid_native(limit=10, rerank=True)  -> $rankFusion + $rerank (opt-in cascade, ADR-003)

Metrics per mode: recall_at_5 (share of queries with a relevant product in the
top 5), mrr (reciprocal rank of the first relevant product within the top 10)
and precision_at_5. Also by query type and the deltas hybrid - lexical / hybrid -
vector that protect the thesis.

Read-only (aggregations only) on the database in DB_NAME (.env; demo default).
Each query spends one Voyage query embedding per vector leg (autoEmbed) and one
Voyage rerank call (hybrid_rerank). No LLM. Latency per mode is reported too.
Always exits 0: the weekly runner's floors decide (eval_pisos.json).

    .venv/bin/python eval/run_quality.py --out /tmp/search_quality.json [--db marketplace_test]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "eval" / "golden.json"
MODES = ("lexical", "vector", "hybrid", "hybrid_app_rrf", "hybrid_rerank")
TOP_K = 10


def catalog_index() -> dict[str, list[str]]:
    """subcategoria -> ['marca modelo', ...] from the seed generator (pure import)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import populate_marketplace as seed  # noqa: E402  (module only defines data/functions)

    out: dict[str, list[str]] = {}
    for subs in seed.CATALOGO.values():
        for sub, items in subs.items():
            out.setdefault(sub, []).extend(f"{marca} {modelo}" for marca, modelo, _ in items)
    return out


def target_set(case: dict, catalog: dict[str, list[str]]) -> set[str]:
    targets = set(case.get("alvo", []))
    for sub in case.get("subcategorias", []):
        if sub not in catalog:
            raise ValueError(f"{case['id']}: subcategoria desconhecida {sub!r}")
        targets.update(catalog[sub])
    if not targets:
        raise ValueError(f"{case['id']}: caso sem alvo")
    return targets


def product_key(nome: str | None) -> str:
    """'Sony WH-1000XM5 — Preto' -> 'Sony WH-1000XM5' (the seed appends ' — cor')."""
    return (nome or "").rsplit(" — ", 1)[0].strip()


def first_relevant_rank(names: list[str], targets: set[str], k: int = TOP_K) -> int | None:
    for i, nome in enumerate(names[:k], 1):
        if product_key(nome) in targets:
            return i
    return None


def score_case(names: list[str], targets: set[str]) -> dict:
    rank = first_relevant_rank(names, targets)
    rel5 = sum(1 for n in names[:5] if product_key(n) in targets)
    return {"rank": rank, "hit_at_5": bool(rank and rank <= 5),
            "rr": round(1 / rank, 4) if rank else 0.0, "precision_at_5": rel5 / 5}


def aggregate(scores: list[dict]) -> dict:
    n = len(scores)
    if not n:
        return {"recall_at_5": None, "mrr": None, "precision_at_5": None, "n": 0}
    return {"recall_at_5": round(sum(s["hit_at_5"] for s in scores) / n, 4),
            "mrr": round(sum(s["rr"] for s in scores) / n, 4),
            "precision_at_5": round(sum(s["precision_at_5"] for s in scores) / n, 4), "n": n}


def median_ms(values: list) -> int | None:
    vals = sorted(v for v in values if isinstance(v, (int, float)))
    if not vals:
        return None
    mid = len(vals) // 2
    return round(vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2)


def summarize(rows: list[dict]) -> dict:
    """rows: [{'id','tipo','modes': {mode: score_case(...)}}] -> summary + by_type."""
    summary = {m: aggregate([r["modes"][m] for r in rows if m in r["modes"]]) for m in MODES}
    by_type: dict[str, dict] = {}
    for tipo in sorted({r["tipo"] for r in rows}):
        sub = [r for r in rows if r["tipo"] == tipo]
        by_type[tipo] = {m: aggregate([r["modes"][m] for r in sub if m in r["modes"]]) for m in MODES}
    h, lx, vc, app = summary["hybrid"], summary["lexical"], summary["vector"], summary["hybrid_app_rrf"]

    def delta(a, b, key):
        return round(a[key] - b[key], 4) if a[key] is not None and b[key] is not None else None

    summary["deltas"] = {
        "hybrid_minus_lexical_recall_at_5": delta(h, lx, "recall_at_5"),
        "hybrid_minus_vector_recall_at_5": delta(h, vc, "recall_at_5"),
        "hybrid_minus_lexical_mrr": delta(h, lx, "mrr"),
        "hybrid_minus_vector_mrr": delta(h, vc, "mrr"),
        "app_rrf_minus_lexical_recall_at_5": delta(app, lx, "recall_at_5"),
        "app_rrf_minus_vector_recall_at_5": delta(app, vc, "recall_at_5"),
        "rerank_minus_hybrid_recall_at_5": delta(summary["hybrid_rerank"], h, "recall_at_5"),
        "rerank_minus_hybrid_mrr": delta(summary["hybrid_rerank"], h, "mrr"),
    }
    return {"summary": summary, "by_type": by_type}


def run(db_name: str | None) -> dict:
    if db_name:
        os.environ["DB_NAME"] = db_name
    sys.path.insert(0, str(ROOT / "backend"))
    import atlas  # noqa: E402  (lazy client; first query connects)

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    catalog = catalog_index()
    rows, errors, fallbacks, rerank_fallbacks, latencies = [], [], 0, 0, {}
    t0 = time.time()
    for case in golden["casos"]:
        targets = target_set(case, catalog)
        cmp = atlas.compare_search_vector(case["consulta"], mode="compound")
        hyb = atlas.hybrid_native(case["consulta"], limit=TOP_K)
        rr = atlas.hybrid_native(case["consulta"], limit=TOP_K, rerank=True)
        # A missing / building index now comes back as an error instead of an
        # empty list, so it counts here instead of passing as "no relevant hit".
        err = [e for e in (cmp["search"]["error"], cmp["vector"]["error"], hyb.get("error"),
                           rr.get("error")) if e]
        if err:
            errors.append({"id": case["id"], "error": str(err[0])[:200]})
        if not hyb.get("native"):
            fallbacks += 1
        if not (rr.get("rerank") or {}).get("applied"):
            rerank_fallbacks += 1
        for mode, ms in (("lexical", cmp["search"].get("elapsed_ms")), ("vector", cmp["vector"].get("elapsed_ms")),
                         ("hybrid", hyb.get("elapsed_ms")), ("hybrid_rerank", rr.get("elapsed_ms"))):
            latencies.setdefault(mode, []).append(ms or 0)
        ranked = {
            "lexical": [d.get("nome") for d in cmp["search"]["results"]],
            "vector": [d.get("nome") for d in cmp["vector"]["results"]],
            "hybrid": [d.get("nome") for d in hyb.get("fused", [])],
            "hybrid_app_rrf": [d.get("nome") for d in cmp.get("hybrid", [])],
            "hybrid_rerank": [d.get("nome") for d in rr.get("fused", [])],
        }
        rows.append({"id": case["id"], "tipo": case["tipo"], "consulta": case["consulta"],
                     "modes": {m: score_case(ranked[m], targets) for m in MODES},
                     "top5": {m: [product_key(n) for n in ranked[m][:5]]
                              for m in ("lexical", "vector", "hybrid", "hybrid_rerank")}})
    out = summarize(rows)
    out["summary"].update(cases=len(rows), errors=len(errors), hybrid_native_fallbacks=fallbacks,
                          rerank_fallbacks=rerank_fallbacks,
                          latency_ms_p50={m: median_ms(v) for m, v in latencies.items()})
    out.update(db=atlas.DB_NAME, same_corpus=bool(atlas.vector_collection_search_index()),
               golden_status=golden.get("_status"), elapsed_s=round(time.time() - t0, 1),
               errors=errors, cases=rows)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "eval" / "reports" / "quality.json"))
    ap.add_argument("--db", help="override DB_NAME (read-only; default: .env)")
    args = ap.parse_args(argv)
    try:
        result = run(args.db)
    except Exception as exc:  # noqa: BLE001 - floors decide; report the failure in the JSON
        result = {"summary": {"cases": 0, "errors": 1}, "fatal": f"{type(exc).__name__}: {exc}"[:300]}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    s = result["summary"]
    if "fatal" in result:
        print(f"eval de qualidade falhou: {result['fatal']}")
    else:
        print(f"{s['cases']} consultas em {result['db']} ({result['elapsed_s']} s), erros {s['errors']}, "
              f"fallback do $rankFusion {s['hybrid_native_fallbacks']}, do $rerank {s['rerank_fallbacks']}")
        for m in MODES:
            print(f"  {m:<15} recall@5 {s[m]['recall_at_5']:.3f}  MRR {s[m]['mrr']:.3f}  P@5 {s[m]['precision_at_5']:.3f}")
        for tipo, by in result["by_type"].items():
            print(f"  [{tipo:<9}] " + "  ".join(f"{m} r@5 {by[m]['recall_at_5']:.2f}"
                                               for m in ("lexical", "vector", "hybrid", "hybrid_rerank")))
        print(f"  latência p50 (ms): {s['latency_ms_p50']}")
        print(f"  deltas: {s['deltas']}")
    print(f"json: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
