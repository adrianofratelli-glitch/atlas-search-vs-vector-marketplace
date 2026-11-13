"""PII mask on every text that leaves Atlas for Voyage (ADR-004).

autoEmbed ($vectorSearch "query") and $rerank send the text to Voyage AI. The
backend masks it with the same mask_pii the LLM path uses, the pipeline carries
the masked text (MQL shown == what ran == what Voyage saw), and a mask failure
skips the vector leg instead of sending the raw text. Offline: Atlas is mocked.

The identity tests over every eval query (validated and draft answer keys) are
the proof that the mask cannot move recall on queries without PII: the text
the vector leg receives is byte-for-byte the same.
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("MONGODB_URI", "mongodb://localhost/test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")

import atlas  # noqa: E402
import llm_gateway  # noqa: E402

EVAL = BACKEND.parent / "eval"
EMAIL, CPF = "joao.silva@example.com", "123.456.789-09"
PII_QUERY = f"fone para viagem, meu email {EMAIL} cpf {CPF}"
INDEXES = [
    {"name": "produtos_vector_search", "type": "search", "queryable": True,
     "latestDefinition": {"mappings": {"fields": {"produto_id": {"type": "token"}}}}},
    {"name": "produtos_vector", "type": "vectorSearch", "queryable": True},
]


def _cases(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["casos"]


def _vector_queries(pipelines) -> list[str]:
    """Every $vectorSearch query text in the pipelines (including inside fusion)."""
    found = []

    def walk(node):
        if isinstance(node, dict):
            if "$vectorSearch" in node:
                found.append(node["$vectorSearch"]["query"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(pipelines)
    return found


class MaskIsIdentityWithoutPII(unittest.TestCase):
    def test_every_validated_query_reaches_voyage_unchanged(self):
        cases = _cases(EVAL / "golden.json")
        self.assertEqual(len(cases), 26)
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(llm_gateway.mask_pii(case["consulta"]), case["consulta"])
                self.assertEqual(atlas.embed_query_text(case["consulta"]), case["consulta"])

    def test_every_draft_query_without_pii_is_unchanged(self):
        for case in _cases(EVAL / "golden_rascunho.json"):
            if case["tipo"] == "pii":
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(atlas.embed_query_text(case["consulta"]), case["consulta"])

    def test_draft_pii_cases_are_masked(self):
        pii = [c for c in _cases(EVAL / "golden_rascunho.json") if c["tipo"] == "pii"]
        self.assertGreaterEqual(len(pii), 2)
        for case in pii:
            with self.subTest(case=case["id"]):
                masked = atlas.embed_query_text(case["consulta"])
                self.assertNotEqual(masked, case["consulta"])
                self.assertNotIn("@exemplo.com", masked)
                self.assertNotIn("123.456.789-09", masked)

    def test_numbers_and_units_survive(self):
        for q in ("tv 55 polegadas", "geladeira 431 litros", "whey 2kg", "lavadora 11 kg",
                  "creatina 300g", "Steam Deck OLED 1TB", "G-Shock GA-2100", "kettlebel 16kg"):
            self.assertEqual(atlas.embed_query_text(q), q)


class VectorLegCarriesMaskedText(unittest.TestCase):
    def setUp(self):
        self.executed = []

        def fake(coll, pipe):
            self.executed.append(pipe)
            return [], None
        self.patches = [mock.patch.object(atlas, "get_search_indexes", return_value=INDEXES),
                        mock.patch.object(atlas, "safe_aggregate", side_effect=fake)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def assert_masked(self, shown):
        sent = _vector_queries(self.executed)
        self.assertTrue(sent, "no $vectorSearch executed")
        for text in sent:
            self.assertNotIn(EMAIL, text)
            self.assertNotIn(CPF, text)
            self.assertIn("fone para viagem", text)
        # MQL parity: the pipeline shown carries the same masked text
        self.assertEqual(_vector_queries(shown), sent[: len(_vector_queries(shown))])

    def test_compare(self):
        out = atlas.compare_search_vector(PII_QUERY, mode="compound")
        self.assert_masked(out["vector"]["pipeline"])
        self.assertTrue(out["embed_query_masked"])
        self.assertIs(out["vector"]["pipeline"], self.executed[1])
        # the lexical $search never leaves Atlas: raw query kept
        self.assertIn(EMAIL, json.dumps(out["search"]["pipeline"]))

    def test_hybrid_app_rrf(self):
        out = atlas.hybrid_rrf(PII_QUERY)
        self.assert_masked([])
        self.assertTrue(out["embed_query_masked"])

    def test_hybrid_native_with_and_without_rerank(self):
        for rerank in (False, True):
            self.executed.clear()
            out = atlas.hybrid_native(PII_QUERY, limit=10, rerank=rerank)
            self.assertIs(out["pipeline"], self.executed[0])
            self.assert_masked(out["pipeline"])
            self.assertTrue(out["embed_query_masked"])
            if rerank:
                text = next(s for s in out["pipeline"] if "$rerank" in s)["$rerank"]["query"]["text"]
                self.assertEqual(text, _vector_queries(out["pipeline"])[0])

    def test_score_fusion(self):
        out = atlas.hybrid_score_fusion(PII_QUERY, limit=10)
        self.assertIs(out["pipeline"], self.executed[0])
        self.assert_masked(out["pipeline"])

    def test_similar_masks_the_base_description(self):
        base = {"produto_id": "p1", "nome": "Fone", "categoria": "Eletrônicos",
                "descricao": f"fone para viagem, contato {EMAIL}"}

        def fake(coll, pipe):
            self.executed.append(pipe)
            return ([base], None) if len(self.executed) == 1 else ([], None)
        with mock.patch.object(atlas, "safe_aggregate", side_effect=fake):
            out = atlas.find_similar(produto_id="p1")
        self.assertIs(out["pipeline"], self.executed[1])
        self.assertNotIn(EMAIL, _vector_queries(out["pipeline"])[0])

    def test_clean_query_reports_not_masked(self):
        out = atlas.hybrid_native("Gel-Kayano 30", limit=10)
        self.assertFalse(out["embed_query_masked"])
        self.assertEqual(_vector_queries(out["pipeline"]), ["Gel-Kayano 30"])


class MaskFailureSkipsVoyage(unittest.TestCase):
    """If the mask itself fails, nothing is embedded or reranked (fail-closed)."""

    def setUp(self):
        self.executed = []

        def fake(coll, pipe):
            self.executed.append(pipe)
            return [{"produto_id": "p1", "nome": "Fone", "categoria": "x", "descricao": "fone"}], None
        self.patches = [
            mock.patch.object(llm_gateway, "_mask_fn", side_effect=RuntimeError("guardrails down")),
            mock.patch.object(atlas, "get_search_indexes", return_value=INDEXES),
            mock.patch.object(atlas, "safe_aggregate", side_effect=fake)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def assert_no_voyage(self):
        self.assertEqual(_vector_queries(self.executed), [])
        self.assertNotIn("$rerank", json.dumps(self.executed))

    def test_compare_runs_lexical_only_and_names_the_cause(self):
        out = atlas.compare_search_vector(PII_QUERY, mode="compound")
        self.assert_no_voyage()
        self.assertEqual(out["vector"]["error"], atlas.PII_MASK_UNAVAILABLE)
        self.assertIsNone(out["vector"]["pipeline"])
        self.assertEqual(out["degraded"]["vector"], atlas.PII_MASK_UNAVAILABLE)

    def test_hybrids_and_similar_fail_closed(self):
        for call in (lambda: atlas.hybrid_rrf(PII_QUERY),
                     lambda: atlas.hybrid_native(PII_QUERY, limit=10),
                     lambda: atlas.hybrid_native(PII_QUERY, limit=10, rerank=True),
                     lambda: atlas.hybrid_score_fusion(PII_QUERY, limit=10),
                     lambda: atlas.find_similar(produto_id="p1")):
            self.executed.clear()
            out = call()
            self.assert_no_voyage()
            self.assertIn("Máscara de PII indisponível", out["error"])


class AgentSemanticTool(unittest.TestCase):
    def test_pipeline_and_trace_carry_masked_text(self):
        import agent
        pipe = agent._pipe_busca_semantica(PII_QUERY)
        self.assertNotIn(EMAIL, pipe[0]["$vectorSearch"]["query"])
        self.assertEqual(agent.build_tool_pipeline("busca_semantica", {"consulta": PII_QUERY}), pipe)

    def test_mask_failure_blocks_the_tool(self):
        import agent
        with mock.patch.object(llm_gateway, "_mask_fn", side_effect=RuntimeError("down")), \
                mock.patch.object(agent, "safe_aggregate") as agg:
            out = agent.busca_semantica.invoke({"consulta": "fone"})
        self.assertTrue(out.startswith("Erro"))
        self.assertIn("Máscara de PII indisponível", out)
        agg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
