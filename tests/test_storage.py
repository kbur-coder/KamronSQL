import os
import tempfile
import unittest

from src.storage import RID, StorageEngine


class StorageEngineTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "engine.db")
        self.addCleanup(self.dir.cleanup)

    def test_catalog_survives_reopening(self):
        engine = StorageEngine(self.path)
        engine.catalog_put("users", b'{"a": 1}')
        engine.catalog_put("orders", b"payload")
        engine.close()
        engine = StorageEngine(self.path)
        self.assertEqual(engine.catalog_names(), ["orders", "users"])
        self.assertEqual(engine.catalog_get("users"), b'{"a": 1}')
        self.assertIsNone(engine.catalog_get("missing"))
        engine.close()

    def test_catalog_entry_can_be_replaced_with_a_bigger_payload(self):
        engine = StorageEngine(self.path)
        engine.catalog_put("t", b"small")
        engine.catalog_put("t", b"x" * 2000)
        engine.close()
        engine = StorageEngine(self.path)
        self.assertEqual(engine.catalog_get("t"), b"x" * 2000)
        self.assertEqual(engine.catalog_names(), ["t"])
        engine.close()

    def test_catalog_delete(self):
        engine = StorageEngine(self.path)
        engine.catalog_put("a", b"1")
        engine.catalog_put("b", b"2")
        self.assertTrue(engine.catalog_delete("a"))
        self.assertFalse(engine.catalog_delete("a"))
        engine.close()
        engine = StorageEngine(self.path)
        self.assertEqual(engine.catalog_names(), ["b"])
        engine.close()

    def test_unicode_names(self):
        engine = StorageEngine(self.path)
        engine.catalog_put("таблица", b"1")
        engine.close()
        engine = StorageEngine(self.path)
        self.assertEqual(engine.catalog_get("таблица"), b"1")
        engine.close()

    def test_heap_and_index_can_be_reopened_from_their_meta_pages(self):
        engine = StorageEngine(self.path, pool_size=8)
        heap = engine.new_heap()
        index = engine.new_index(4, 4)
        for key in range(100):
            rid = heap.insert(b"value-%d" % key)
            index.insert(key, rid)
        heap_meta, index_meta = heap.meta_page_id, index.meta_page_id
        engine.close()
        engine = StorageEngine(self.path, pool_size=8)
        heap = engine.open_heap(heap_meta)
        index = engine.open_index(index_meta)
        index.check()
        for key in range(100):
            self.assertEqual(heap.get(index.search(key)), b"value-%d" % key)
        engine.close()

    def test_small_page_size_and_lru_k_policy(self):
        engine = StorageEngine(self.path, page_size=1024, pool_size=6, policy="lru-k")
        index = engine.new_index()
        heap = engine.new_heap()
        for key in range(500):
            index.insert(key, heap.insert(b"x" * 50))
        index.check()
        self.assertEqual(engine.stats()["policy"], "lru-k")
        self.assertEqual(engine.stats()["page_size"], 1024)
        engine.close()

    def test_stats_and_checkpoint(self):
        engine = StorageEngine(self.path)
        engine.catalog_put("t", b"1")
        self.assertGreater(engine.stats()["dirty"], 0)
        self.assertGreater(engine.checkpoint(), 0)
        self.assertEqual(engine.stats()["dirty"], 0)
        engine.close()

    def test_background_flusher_option(self):
        engine = StorageEngine(self.path, flush_interval=0.05)
        engine.catalog_put("t", b"1")
        engine.close()
        engine = StorageEngine(self.path)
        self.assertEqual(engine.catalog_get("t"), b"1")
        engine.close()


if __name__ == "__main__":
    unittest.main()
