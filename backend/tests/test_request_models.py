import unittest

from pydantic import ValidationError

from main import SimilarReq


class TestSimilarReq(unittest.TestCase):
    def test_requires_product_id_or_name(self):
        with self.assertRaises(ValidationError):
            SimilarReq()

    def test_accepts_product_id(self):
        self.assertEqual(SimilarReq(produto_id="PRD-1").produto_id, "PRD-1")
