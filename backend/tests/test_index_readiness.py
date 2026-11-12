"""A missing or not-yet-queryable search index must never look like "no results".

Measured on Atlas 9.0.7 (2026-11-12): $search, $searchMeta and $vectorSearch
against an index that does not exist return an EMPTY result with no error, and
while a fresh vector index is PENDING/BUILDING /compare answered vector n=0,
error=None, degraded=None. safe_aggregate now checks $listSearchIndexes before
running and again on an empty answer. Offline: Atlas is mocked.
"""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGODB_URI", "mongodb://localhost/test")

import atlas  # noqa: E402

READY_VECTOR = {"name": "produtos_vector", "type": "vectorSearch", "status": "READY", "queryable": True}
READY_LEXICAL = {"name": "produtos_vector_search", "type": "search", "status": "READY", "queryable": True}
PENDING_VECTOR = {**READY_VECTOR, "status": "PENDING", "queryable": False}
VECTOR_PIPE = [{"$vectorSearch": {"index": "produtos_vector", "path": "descricao", "query": "fone",
                                  "numCandidates": 100, "limit": 10}}]


def fake_db(listings, results):
    """listings: successive $listSearchIndexes answers; results: answers of the query itself."""
    listings, results = list(listings), list(results)
    calls = {"list": 0, "query": 0}

    def aggregate(pipeline, **_kw):
        if pipeline and "$listSearchIndexes" in pipeline[0]:
            calls["list"] += 1
            nxt = listings.pop(0)
            if isinstance(nxt, Exception):
                raise nxt
            return iter(nxt)
        calls["query"] += 1
        return iter(results.pop(0))

    coll = mock.MagicMock()
    coll.aggregate.side_effect = aggregate
    db = mock.MagicMock()
    db.__getitem__.return_value = coll
    return db, calls


class SearchIndexesUsed(unittest.TestCase):
    def test_plain_stages_and_default_name(self):
        pipe = [{"$search": {"index": "a", "text": {}}}, {"$limit": 1}]
        self.assertEqual(atlas.search_indexes_used(pipe), ["a"])
        self.assertEqual(atlas.search_indexes_used([{"$searchMeta": {"text": {}}}]), ["default"])
        self.assertEqual(atlas.search_indexes_used([{"$match": {}}]), [])

    def test_fusion_sub_pipelines(self):
        pipe = [{"$rankFusion": {"input": {"pipelines": {
            "textual": [{"$search": {"index": "produtos_vector_search"}}, {"$limit": 5}],
            "semantico": VECTOR_PIPE,
        }}}}, {"$limit": 5}]
        self.assertEqual(atlas.search_indexes_used(pipe), ["produtos_vector_search", "produtos_vector"])

    def test_real_builders_are_covered(self):
        h = None
        with mock.patch.object(atlas, "get_search_indexes", return_value=[READY_LEXICAL, READY_VECTOR]), \
                mock.patch.object(atlas, "safe_aggregate", return_value=([], None)):
            h = atlas.hybrid_native("fone")
        self.assertEqual(atlas.search_indexes_used(h["pipeline"]),
                         ["produtos_vector_search", "produtos_vector"])


class UnavailableReason(unittest.TestCase):
    def test_missing_and_pending_are_named(self):
        reason = atlas.index_unavailable_reason("produtos_vector", ["produtos_vector", "x"], [PENDING_VECTOR])
        self.assertTrue(reason.startswith(atlas.INDEX_UNAVAILABLE))
        self.assertIn("produtos_vector está PENDING", reason)
        self.assertIn("x não existe em produtos_vector", reason)

    def test_ready_is_none(self):
        self.assertIsNone(atlas.index_unavailable_reason("produtos_vector", ["produtos_vector"], [READY_VECTOR]))


class SafeAggregateGate(unittest.TestCase):
    def setUp(self):
        atlas.invalidate_index_cache()
        self.addCleanup(atlas.invalidate_index_cache)
        # Re-read window 0: every negative/empty answer re-lists (the window
        # itself is covered by RecheckWindow below).
        patcher = mock.patch.object(atlas, "_INDEX_RECHECK_TTL", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_pending_index_is_an_error_not_an_empty_list(self):
        db, calls = fake_db([[PENDING_VECTOR], [PENDING_VECTOR]], [])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos_vector", VECTOR_PIPE)
        self.assertIsNone(res)
        self.assertIn("PENDING", err)
        self.assertEqual(calls["query"], 0)  # never ran the silent-empty query
        self.assertEqual(calls["list"], 2)   # cached "not ready" was re-read once

    def test_stale_negative_cache_does_not_block_a_ready_index(self):
        db, calls = fake_db([[PENDING_VECTOR], [READY_VECTOR]], [[{"nome": "x"}]])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos_vector", VECTOR_PIPE)
        self.assertEqual(res, [{"nome": "x"}])
        self.assertIsNone(err)

    def test_index_dropped_after_cache_detected_on_empty_answer(self):
        db, calls = fake_db([[READY_VECTOR], []], [[]])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos_vector", VECTOR_PIPE)
        self.assertIsNone(res)
        self.assertIn("produtos_vector não existe", err)

    def test_genuinely_empty_answer_with_ready_index_stays_empty(self):
        db, calls = fake_db([[READY_VECTOR], [READY_VECTOR]], [[]])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos_vector", VECTOR_PIPE)
        self.assertEqual(res, [])
        self.assertIsNone(err)

    def test_listing_failure_fails_open(self):
        db, calls = fake_db([RuntimeError("no $listSearchIndexes")], [[{"nome": "x"}]])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos_vector", VECTOR_PIPE)
        self.assertEqual(res, [{"nome": "x"}])
        self.assertIsNone(err)
        self.assertEqual(atlas.get_search_indexes("produtos_vector"), [])  # unknown, not "absent"

    def test_pipeline_without_search_stage_skips_listing(self):
        db, calls = fake_db([], [[]])
        with mock.patch.object(atlas, "db", db):
            res, err = atlas.safe_aggregate("produtos", [{"$match": {"x": 1}}])
        self.assertEqual((res, err), ([], None))
        self.assertEqual(calls["list"], 0)


class RecheckWindow(unittest.TestCase):
    """Zero lexical hits is a normal demo answer: a burst of empty searches
    must not hit $listSearchIndexes every time (re-read at most every 5 s)."""

    def setUp(self):
        atlas.invalidate_index_cache()
        self.addCleanup(atlas.invalidate_index_cache)

    def test_empty_answers_within_window_list_once(self):
        db, calls = fake_db([[READY_VECTOR]], [[], [], []])
        with mock.patch.object(atlas, "db", db):
            for _ in range(3):
                self.assertEqual(atlas.safe_aggregate("produtos_vector", VECTOR_PIPE), ([], None))
        self.assertEqual(calls["list"], 1)
        self.assertEqual(calls["query"], 3)

    def test_empty_answer_after_window_relists(self):
        db, calls = fake_db([[READY_VECTOR], [READY_VECTOR]], [[]])
        clock = iter([1000.0, 1000.0 + atlas._INDEX_RECHECK_TTL + 1])
        with mock.patch.object(atlas, "db", db), \
                mock.patch.object(atlas.time, "time", side_effect=lambda: next(clock)):
            self.assertEqual(atlas.safe_aggregate("produtos_vector", VECTOR_PIPE), ([], None))
        self.assertEqual(calls["list"], 2)


class CompareSurfacesTheGap(unittest.TestCase):
    def test_vector_leg_pending_sets_degraded(self):
        def fake(coll, pipe):
            if "$vectorSearch" in pipe[0]:
                return None, atlas.index_unavailable_reason(coll, ["produtos_vector"], [PENDING_VECTOR])
            return [{"produto_id": "1", "nome": "a"}], None
        with mock.patch.object(atlas, "get_search_indexes", return_value=[READY_LEXICAL, PENDING_VECTOR]), \
                mock.patch.object(atlas, "safe_aggregate", side_effect=fake):
            out = atlas.compare_search_vector("fone", mode="compound")
        self.assertIsNotNone(out["degraded"])
        self.assertIn("PENDING", out["degraded"]["vector"])
        self.assertIsNone(out["degraded"]["search"])


class FusionFallbackReason(unittest.TestCase):
    def test_lexical_index_building_is_named(self):
        building = {**READY_LEXICAL, "status": "BUILDING", "queryable": False}
        with mock.patch.object(atlas, "get_search_indexes", return_value=[building, READY_VECTOR]), \
                mock.patch.object(atlas, "hybrid_rrf", return_value={"fused": [], "counts": {}}):
            out = atlas.hybrid_native("fone")
        self.assertFalse(out["native"])
        self.assertIn("BUILDING", out["reason"])
        self.assertNotIn("crie um", out["reason"])

    def test_index_error_passes_through_with_fallback_error(self):
        err = atlas.index_unavailable_reason("produtos_vector", ["produtos_vector"], [PENDING_VECTOR])
        fb = {"error": "busca vetorial: " + err, "fused": [], "counts": {}}
        with mock.patch.object(atlas, "get_search_indexes", return_value=[READY_LEXICAL, PENDING_VECTOR]), \
                mock.patch.object(atlas, "safe_aggregate", return_value=(None, err)), \
                mock.patch.object(atlas, "hybrid_rrf", return_value=fb):
            for fn in (atlas.hybrid_native, atlas.hybrid_score_fusion):
                out = fn("fone")
                self.assertFalse(out["native"])
                self.assertTrue(out["reason"].startswith(atlas.INDEX_UNAVAILABLE))
                self.assertEqual(out["error"], fb["error"])


if __name__ == "__main__":
    unittest.main()
