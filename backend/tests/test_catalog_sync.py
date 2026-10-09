"""Catalog mirror (produtos -> produtos_vector): pure logic, offline."""

import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("MONGODB_URI", "mongodb://localhost/test")

from bson import ObjectId, Timestamp  # noqa: E402

import catalog_sync as cs  # noqa: E402


class PlanEvent(unittest.TestCase):
    def test_insert_upserts_full_document_keyed_by_produto_id(self):
        oid = ObjectId()
        doc = {"_id": oid, "produto_id": "P-1", "nome": "x", "descricao": "y"}
        action, flt, out = cs.plan_event({"operationType": "insert", "documentKey": {"_id": oid},
                                          "fullDocument": doc})
        self.assertEqual(action, "upsert")
        self.assertEqual(flt, {"produto_id": "P-1"})
        self.assertIs(out, doc)

    def test_update_without_post_image_is_skipped(self):
        action, _, _ = cs.plan_event({"operationType": "update", "documentKey": {"_id": 1}, "fullDocument": None})
        self.assertEqual(action, "skip")

    def test_delete_uses_id_and_pre_image_produto_id(self):
        oid = ObjectId()
        action, flt, _ = cs.plan_event({"operationType": "delete", "documentKey": {"_id": oid},
                                        "fullDocumentBeforeChange": {"produto_id": "P-9"}})
        self.assertEqual(action, "delete")
        self.assertEqual(flt, {"$or": [{"_id": oid}, {"produto_id": "P-9"}]})

    def test_delete_without_pre_image_matches_by_id(self):
        oid = ObjectId()
        _, flt, _ = cs.plan_event({"operationType": "delete", "documentKey": {"_id": oid}})
        self.assertEqual(flt, {"_id": oid})

    def test_other_operations_are_ignored(self):
        for op in ("drop", "invalidate", "rename", "dropDatabase"):
            self.assertEqual(cs.plan_event({"operationType": op})[0], "skip")


class SkipRules(unittest.TestCase):
    def test_paused_skips_everything(self):
        self.assertTrue(cs.should_skip({"clusterTime": Timestamp(10, 1)}, {"paused": True}))

    def test_events_up_to_skip_before_are_the_seed(self):
        state = {"paused": False, "skip_before": Timestamp(100, 5)}
        self.assertTrue(cs.should_skip({"clusterTime": Timestamp(100, 5)}, state))
        self.assertTrue(cs.should_skip({"clusterTime": Timestamp(99, 9)}, state))
        self.assertFalse(cs.should_skip({"clusterTime": Timestamp(100, 6)}, state))

    def test_no_state_means_mirror(self):
        self.assertFalse(cs.should_skip({"clusterTime": Timestamp(1, 1)}, None))


class ApplyEvent(unittest.TestCase):
    def test_existing_copy_is_replaced_in_place(self):
        target = mock.Mock()
        target.replace_one.return_value = mock.Mock(matched_count=1)
        doc = {"_id": 7, "produto_id": "P-1", "nome": "novo"}
        self.assertEqual(cs.apply_event(target, "upsert", {"produto_id": "P-1"}, doc), "updated")
        target.replace_one.assert_called_once_with({"produto_id": "P-1"}, {"produto_id": "P-1", "nome": "novo"})

    def test_new_product_is_inserted_with_source_id(self):
        target = mock.Mock()
        target.replace_one.side_effect = [mock.Mock(matched_count=0), mock.Mock(matched_count=0)]
        doc = {"_id": 7, "produto_id": "P-2"}
        self.assertEqual(cs.apply_event(target, "upsert", {"produto_id": "P-2"}, doc), "inserted")
        target.replace_one.assert_called_with({"_id": 7}, doc, upsert=True)

    def test_delete(self):
        target = mock.Mock()
        target.delete_many.return_value = mock.Mock(deleted_count=1)
        self.assertEqual(cs.apply_event(target, "delete", {"_id": 7}, None), "deleted:1")


class Switch(unittest.TestCase):
    def test_default_on_and_flag_only_reverts(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CATALOG_SYNC", None)
            self.assertTrue(cs.enabled())
        with mock.patch.dict(os.environ, {"CATALOG_SYNC": "0"}):
            self.assertFalse(cs.enabled())
            self.assertIsNone(cs.start(mock.Mock()))
            self.assertEqual(cs.status(), {"enabled": False})


if __name__ == "__main__":
    unittest.main()
