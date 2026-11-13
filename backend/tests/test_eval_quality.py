"""Unit tests for eval/run_quality.py scoring (offline: no Atlas, no Voyage)."""

import importlib.util
import json
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_spec = importlib.util.spec_from_file_location("run_quality", os.path.join(ROOT, "eval", "run_quality.py"))
rq = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rq)


class TestScoring(unittest.TestCase):
    def test_product_key_strips_color_suffix(self):
        self.assertEqual(rq.product_key("Sony WH-1000XM5 — Preto"), "Sony WH-1000XM5")
        self.assertEqual(rq.product_key("Samsung QLED 55\" — Azul"), "Samsung QLED 55\"")
        self.assertEqual(rq.product_key(None), "")

    def test_score_case_rank_and_hit(self):
        names = ["A — x", "B — y", "Alvo — z", "C — w"]
        s = rq.score_case(names, {"Alvo"})
        self.assertEqual(s["rank"], 3)
        self.assertTrue(s["hit_at_5"])
        self.assertAlmostEqual(s["rr"], 0.3333, places=4)
        self.assertAlmostEqual(s["precision_at_5"], 0.2)

    def test_miss_beyond_top5_counts_for_mrr_only(self):
        names = [f"X{i} — c" for i in range(5)] + ["Alvo — c"]
        s = rq.score_case(names, {"Alvo"})
        self.assertEqual(s["rank"], 6)
        self.assertFalse(s["hit_at_5"])
        self.assertGreater(s["rr"], 0)

    def test_empty_result_is_zero_not_error(self):
        s = rq.score_case([], {"Alvo"})
        self.assertEqual((s["rank"], s["hit_at_5"], s["rr"]), (None, False, 0.0))

    def test_summary_deltas(self):
        hit = rq.score_case(["Alvo — c"], {"Alvo"})
        miss = rq.score_case(["X — c"], {"Alvo"})
        rows = [{"id": "1", "tipo": "nome", "modes": {"lexical": hit, "vector": miss, "hybrid": hit, "hybrid_app_rrf": hit}},
                {"id": "2", "tipo": "intencao", "modes": {"lexical": miss, "vector": hit, "hybrid": hit, "hybrid_app_rrf": hit}}]
        out = rq.summarize(rows)
        self.assertEqual(out["summary"]["hybrid"]["recall_at_5"], 1.0)
        self.assertEqual(out["summary"]["deltas"]["hybrid_minus_lexical_recall_at_5"], 0.5)
        self.assertEqual(out["summary"]["deltas"]["hybrid_minus_vector_recall_at_5"], 0.5)
        self.assertEqual(out["by_type"]["intencao"]["lexical"]["recall_at_5"], 0.0)


class TestGolden(unittest.TestCase):
    def test_every_case_resolves_to_catalog_products(self):
        golden = json.load(open(os.path.join(ROOT, "eval", "golden.json"), encoding="utf-8"))
        catalog = rq.catalog_index()
        known = {p for prods in catalog.values() for p in prods}
        self.assertGreaterEqual(len(golden["casos"]), 15)
        self.assertRegex(golden["_status"], "RASCUNHO|VALIDADO")
        ids = [c["id"] for c in golden["casos"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in golden["casos"]:
            targets = rq.target_set(case, catalog)
            self.assertTrue(targets <= known, f"{case['id']}: alvo fora do catálogo seed")

    def test_validated_set_untouched_and_draft_kept_apart(self):
        validated = json.load(open(rq.GOLDENS["validado"], encoding="utf-8"))
        draft = json.load(open(rq.GOLDENS["rascunho"], encoding="utf-8"))
        self.assertTrue(validated["_status"].startswith("VALIDADO"))
        self.assertEqual(len(validated["casos"]), 26)
        # The draft is never presented as human-labelled.
        self.assertTrue(draft["_status"].startswith("RASCUNHO"))
        self.assertEqual(len(draft["casos"]), 26)
        self.assertEqual({c["tipo"] for c in draft["casos"]},
                         {"sinonimo", "digitacao", "atributo", "ingles", "unidade", "pii"})
        ids_v = {c["id"] for c in validated["casos"]}
        ids_d = [c["id"] for c in draft["casos"]]
        self.assertEqual(len(ids_d), len(set(ids_d)))
        self.assertFalse(ids_v & set(ids_d))
        catalog = rq.catalog_index()
        for case in draft["casos"]:
            rq.target_set(case, catalog)  # raises on an unknown target

    def test_unknown_target_is_an_error(self):
        with self.assertRaises(ValueError):
            rq.target_set({"id": "x", "alvo": ["Marca Inexistente 9000"]}, rq.catalog_index())


if __name__ == "__main__":
    unittest.main()
