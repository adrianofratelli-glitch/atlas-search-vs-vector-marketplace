"""Native $rankFusion: the lexical sub-pipeline must produce positional ranks.

$rankFusion gives tied scores the same rank (1, 1, 1, ..., 103). The catalog
has dozens of units per model with the same BM25 score, so without a
tie-breaker every lexical hit came out with rank_search=1 and crowded the
vector results out of the top 5 (ADR-002). Offline: Atlas is mocked.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGODB_URI", "mongodb://localhost/test")

import atlas  # noqa: E402

LEXICAL_INDEX = {
    "name": "produtos_vector_search", "queryable": True,
    "latestDefinition": {"mappings": {"dynamic": False, "fields": {
        "nome": [{"type": "autocomplete"}, {"type": "string"}],
        "descricao": {"type": "string"},
        "produto_id": {"type": "token"},
    }}},
}
VECTOR_INDEX = {"name": "produtos_vector", "type": "vectorSearch", "queryable": True}


def _without_token(index: dict) -> dict:
    fields = dict(index["latestDefinition"]["mappings"]["fields"])
    fields.pop("produto_id")
    return {**index, "latestDefinition": {"mappings": {"dynamic": False, "fields": fields}}}


class RankFusionLexicalTieBreak(unittest.TestCase):
    def run_native(self, indexes):
        executed = []

        def fake_aggregate(collection, pipeline):
            executed.append((collection, pipeline))
            return [], None

        with mock.patch.object(atlas, "get_search_indexes", return_value=indexes), \
                mock.patch.object(atlas, "safe_aggregate", side_effect=fake_aggregate):
            res = atlas.hybrid_native("relógio para treino de corrida", limit=10)
        return res, executed

    def test_lexical_subpipeline_sorted_by_score_then_unique_key(self):
        res, executed = self.run_native([LEXICAL_INDEX, VECTOR_INDEX])
        self.assertTrue(res["native"])
        self.assertIsNone(res["degraded_reason"])
        textual = executed[0][1][0]["$rankFusion"]["input"]["pipelines"]["textual"]
        sort = textual[0]["$search"]["sort"]
        keys = list(sort)
        # Relevance first (descending), then a unique sortable key: ranks 1..N.
        self.assertEqual(keys[0], "score")
        self.assertEqual(sort["score"], {"$meta": "searchScore", "order": -1})
        self.assertEqual(keys[1:], ["produto_id"])
        self.assertEqual(textual[1], {"$limit": 10})

    def test_trace_is_the_executed_pipeline(self):
        res, executed = self.run_native([LEXICAL_INDEX, VECTOR_INDEX])
        self.assertEqual(executed[0][0], "produtos_vector")
        self.assertIs(res["pipeline"], executed[0][1])

    def test_weights_stay_balanced(self):
        # The fix is structural (tie-break), not weight tuning: 1:1.2 collapsed
        # the hybrid into the vector ranking in the eval (lost n02/d02).
        _, executed = self.run_native([LEXICAL_INDEX, VECTOR_INDEX])
        weights = executed[0][1][0]["$rankFusion"]["combination"]["weights"]
        self.assertEqual(weights, {"textual": 1, "semantico": 1})

    def test_index_without_sortable_key_degrades_with_reason(self):
        res, executed = self.run_native([_without_token(LEXICAL_INDEX), VECTOR_INDEX])
        textual = executed[0][1][0]["$rankFusion"]["input"]["pipelines"]["textual"]
        self.assertNotIn("sort", textual[0]["$search"])
        self.assertTrue(res["native"])
        self.assertEqual(res["degraded_reason"], atlas.RANK_FUSION_TIES_REASON)
        self.assertIs(res["pipeline"], executed[0][1])

    def test_server_error_still_falls_back_to_app_rrf(self):
        with mock.patch.object(atlas, "get_search_indexes", return_value=[LEXICAL_INDEX, VECTOR_INDEX]), \
                mock.patch.object(atlas, "safe_aggregate", return_value=(None, "rejeitada")), \
                mock.patch.object(atlas, "hybrid_rrf", return_value={"fused": [], "counts": {}}):
            res = atlas.hybrid_native("tenis", limit=10)
        self.assertFalse(res["native"])
        self.assertIn("$rankFusion", res["reason"])
        self.assertIn("sort", res["pipeline"][0]["$rankFusion"]["input"]["pipelines"]["textual"][0]["$search"])

    def test_seed_index_declares_produto_id_as_token(self):
        # Precondition of the tie-break lives in the documented index definition.
        path = os.path.join(os.path.dirname(ROOT), "scripts", "populate_marketplace.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        section = text.split("Name: produtos_vector_search", 1)[1]
        self.assertIn('"produto_id":  {{ "type": "token" }}', section.split("Name:", 1)[0])


if __name__ == "__main__":
    unittest.main()
