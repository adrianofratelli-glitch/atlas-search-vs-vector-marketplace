"""Opt-in $rankFusion -> $rerank cascade (ADR-003). Offline: Atlas is mocked."""

import importlib.util
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGODB_URI", "mongodb://localhost/test")

import atlas  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "run_quality", os.path.join(os.path.dirname(ROOT), "eval", "run_quality.py"))
rq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rq)

INDEXES = [
    {"name": "produtos_vector_search", "type": "search", "queryable": True,
     "latestDefinition": {"mappings": {"fields": {"produto_id": {"type": "token"}}}}},
    {"name": "produtos_vector", "type": "vectorSearch", "queryable": True},
]
DOC = {"nome": "Sony WH-1000XM5 — Preto", "rrf_score": 0.0325, "rerank_score": 0.91,
       "scoreDetails": {"details": [{"inputPipelineName": "textual", "rank": 1},
                                    {"inputPipelineName": "semantico", "rank": 2}]}}


class RerankPipeline(unittest.TestCase):
    def test_stages_keep_rrf_before_rerank(self):
        add, rr = atlas.rerank_stages("fone", 20)
        self.assertEqual(add["$addFields"]["rrf_score"], {"$meta": "score"})
        self.assertEqual(add["$addFields"]["rrf_details"], {"$meta": "scoreDetails"})
        self.assertEqual(rr["$rerank"]["query"], {"text": "fone"})
        self.assertEqual(rr["$rerank"]["path"], "descricao")
        self.assertEqual(rr["$rerank"]["numDocsToRerank"], 20)
        self.assertEqual(atlas.rerank_stages("x", 5000)[1]["$rerank"]["numDocsToRerank"], 1000)

    def run_native(self, side_effect, rerank=True):
        executed = []

        def fake(coll, pipe):
            executed.append(pipe)
            return side_effect(pipe)
        with mock.patch.object(atlas, "get_search_indexes", return_value=INDEXES), \
                mock.patch.object(atlas, "safe_aggregate", side_effect=fake):
            return atlas.hybrid_native("fone", limit=10, rerank=rerank), executed

    def test_applied_rerank_is_traced_and_keeps_ranks(self):
        out, executed = self.run_native(lambda pipe: ([DOC], None))
        stages = [next(iter(s)) for s in out["pipeline"]]
        self.assertEqual(stages, ["$rankFusion", "$addFields", "$rerank", "$limit", "$project"])
        self.assertIs(out["pipeline"], executed[0])  # MQL trace parity
        self.assertEqual(out["pipeline"][2]["$rerank"]["numDocsToRerank"], 20)
        self.assertTrue(out["rerank"]["applied"])
        row = out["fused"][0]
        self.assertEqual((row["rank_search"], row["rank_vector"], row["both"]), (1, 2, True))
        self.assertEqual(row["rrf"], 0.0325)
        self.assertEqual(row["rerank_score"], 0.91)

    def test_rejected_rerank_falls_back_to_plain_fusion(self):
        def side(pipe):
            if any("$rerank" in s for s in pipe):
                return None, "Consulta MongoDB rejeitada. Consulte o request-id nos logs do backend."
            return [{**DOC, "score": 0.0325}], None
        out, executed = self.run_native(side)
        self.assertTrue(out["native"])
        self.assertFalse(out["rerank"]["applied"])
        self.assertTrue(out["rerank"]["requested"])
        self.assertIn("$rerank indisponível", out["rerank"]["reason"])
        self.assertNotIn("$rerank", str(out["pipeline"]))
        self.assertEqual(len(executed), 2)

    def test_rerank_query_is_pii_masked(self):
        q = "fone para joao.silva@example.com cpf 123.456.789-09"
        with mock.patch.object(atlas, "get_search_indexes", return_value=INDEXES), \
                mock.patch.object(atlas, "safe_aggregate", return_value=([DOC], None)):
            out = atlas.hybrid_native(q, limit=10, rerank=True)
        text = next(s for s in out["pipeline"] if "$rerank" in s)["$rerank"]["query"]["text"]
        self.assertNotIn("joao.silva@example.com", text)
        self.assertNotIn("123.456.789-09", text)
        self.assertIn("fone", text)

    def test_mask_failure_skips_rerank(self):
        import llm_gateway
        executed = []

        def fake(coll, pipe):
            executed.append(pipe)
            return [{**DOC, "score": 0.03}], None
        with mock.patch.object(llm_gateway, "_mask_fn", side_effect=RuntimeError("guardrails down")), \
                mock.patch.object(atlas, "get_search_indexes", return_value=INDEXES), \
                mock.patch.object(atlas, "safe_aggregate", side_effect=fake):
            out = atlas.hybrid_native("fone", limit=10, rerank=True)
        # ADR-004: the mask also guards the vector leg, so nothing reaches Voyage
        # (no $vectorSearch, no $rerank) and the cause is named.
        self.assertFalse(out["native"])
        self.assertFalse(out["rerank"]["applied"])
        self.assertIn("Máscara de PII indisponível", out["rerank"]["reason"])
        self.assertIn("Máscara de PII indisponível", out["error"])
        self.assertEqual(executed, [])

    def test_cluster_down_or_timeout_is_not_blamed_on_rerank(self):
        for err in (atlas.ATLAS_UNREACHABLE, atlas.QUERY_TIMEOUT_MSG):
            out, executed = self.run_native(lambda pipe, e=err: (None, e))
            self.assertEqual(len(executed), 1)  # no second attempt without $rerank
            self.assertEqual(out["error"], err)
            self.assertFalse(out["rerank"]["applied"])
            self.assertIsNone(out["rerank"]["reason"])
            self.assertNotIn("$rerank indisponível", out["reason"])

    def test_default_has_no_rerank(self):
        out, _ = self.run_native(lambda pipe: ([{**DOC, "score": 0.03}], None), rerank=False)
        self.assertNotIn("$rerank", str(out["pipeline"]))
        self.assertFalse(out["rerank"]["requested"])
        self.assertNotIn("rerank_score", out["fused"][0])


class EvalAdditions(unittest.TestCase):
    def test_median_ms(self):
        self.assertEqual(rq.median_ms([300, 100, 200]), 200)
        self.assertEqual(rq.median_ms([100, 200]), 150)
        self.assertIsNone(rq.median_ms([]))

    def test_rerank_delta(self):
        hit = rq.score_case(["Alvo — c"], {"Alvo"})
        second = rq.score_case(["X — c", "Alvo — c"], {"Alvo"})
        rows = [{"id": "1", "tipo": "nome", "modes": {"lexical": hit, "vector": hit, "hybrid": second,
                                                      "hybrid_app_rrf": hit, "hybrid_rerank": hit}}]
        d = rq.summarize(rows)["summary"]["deltas"]
        self.assertEqual(d["rerank_minus_hybrid_mrr"], 0.5)
        self.assertEqual(d["rerank_minus_hybrid_recall_at_5"], 0.0)


class RequestModel(unittest.TestCase):
    def test_hybrid_native_rerank_flag(self):
        import main
        self.assertFalse(main.HybridNativeReq(query="fone").rerank)
        self.assertTrue(main.HybridNativeReq(query="fone", rerank=True).rerank)


if __name__ == "__main__":
    unittest.main()
