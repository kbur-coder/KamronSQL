import random
import unittest

from src.storage import BPlusTree, DuplicateKeyError, RID

from .helpers import Workspace


def rid_for(key):
    return RID(abs(key) % 100000 + 1, abs(key) % 60000)


class BPlusTreeBase(unittest.TestCase):
    def make(self, leaf_max=4, internal_max=4, capacity=64, policy="clock"):
        self.workspace = Workspace(capacity=capacity, policy=policy)
        self.addCleanup(self.workspace.close)
        self.pool = self.workspace.pool
        return BPlusTree.create(self.pool, leaf_max, internal_max)


class BPlusTreeInsertTests(BPlusTreeBase):
    def test_empty_tree(self):
        tree = self.make()
        self.assertEqual(len(tree), 0)
        self.assertEqual(tree.height, 1)
        self.assertIsNone(tree.search(5))
        tree.check()

    def test_default_fanout_comes_from_the_page_size(self):
        self.workspace = Workspace()
        self.addCleanup(self.workspace.close)
        tree = BPlusTree.create(self.workspace.pool)
        self.assertEqual(tree.leaf_max, 291)
        self.assertEqual(tree.internal_max, 340)

    def test_invalid_fanout_is_rejected(self):
        self.workspace = Workspace()
        self.addCleanup(self.workspace.close)
        for leaf, internal in ((2, 4), (4, 2), (100000, 4), (4, 100000)):
            with self.assertRaises(ValueError):
                BPlusTree.create(self.workspace.pool, leaf, internal)

    def test_sequential_inserts_split_nodes_and_grow_the_height(self):
        tree = self.make()
        for key in range(1, 101):
            tree.insert(key, rid_for(key))
        self.assertEqual(len(tree), 100)
        self.assertGreaterEqual(tree.height, 3)
        stats = tree.check()
        self.assertEqual(stats["keys"], 100)
        for key in range(1, 101):
            self.assertEqual(tree.search(key), rid_for(key))
        self.assertIsNone(tree.search(0))
        self.assertIsNone(tree.search(101))

    def test_descending_inserts(self):
        tree = self.make(3, 3)
        for key in range(200, 0, -1):
            tree.insert(key, rid_for(key))
        tree.check()
        self.assertEqual(len(tree), 200)

    def test_random_inserts_for_several_fanouts(self):
        for leaf, internal in ((3, 3), (4, 5), (7, 4), (16, 16)):
            tree = self.make(leaf, internal)
            keys = random.Random(leaf * 31 + internal).sample(range(-5000, 5000), 600)
            for key in keys:
                tree.insert(key, rid_for(key))
            tree.check()
            for key in keys:
                self.assertEqual(tree.search(key), rid_for(key))
            self.assertEqual(self.pool.pinned_pages(), 0)

    def test_duplicates_are_rejected_without_changes(self):
        tree = self.make()
        for key in range(30):
            tree.insert(key, rid_for(key))
        with self.assertRaises(DuplicateKeyError):
            tree.insert(7, RID(1, 1))
        self.assertEqual(len(tree), 30)
        self.assertEqual(tree.search(7), rid_for(7))
        tree.check()

    def test_extreme_int64_keys(self):
        tree = self.make()
        keys = [-(2 ** 63), -(2 ** 62), -1, 0, 1, 2 ** 62, 2 ** 63 - 1]
        for key in keys:
            tree.insert(key, rid_for(key))
        for key in keys:
            self.assertEqual(tree.search(key), rid_for(key))
        tree.check()

    def test_update_replaces_the_rid_only(self):
        tree = self.make()
        for key in range(50):
            tree.insert(key, rid_for(key))
        self.assertTrue(tree.update(25, RID(9, 9)))
        self.assertEqual(tree.search(25), RID(9, 9))
        self.assertFalse(tree.update(999, RID(1, 1)))
        self.assertEqual(len(tree), 50)
        tree.check()

    def test_tree_survives_reopening(self):
        tree = self.make(5, 6, capacity=8)
        keys = random.Random(5).sample(range(10000), 400)
        for key in keys:
            tree.insert(key, rid_for(key))
        meta = tree.meta_page_id
        self.workspace.reopen(capacity=8)
        reopened = BPlusTree.open(self.workspace.pool, meta)
        self.assertEqual((reopened.leaf_max, reopened.internal_max), (5, 6))
        self.assertEqual(len(reopened), 400)
        reopened.check()
        for key in keys:
            self.assertEqual(reopened.search(key), rid_for(key))

    def test_tiny_buffer_pool_is_enough(self):
        tree = self.make(4, 4, capacity=6)
        for key in random.Random(9).sample(range(5000), 300):
            tree.insert(key, rid_for(key))
        tree.check()
        self.assertGreater(self.pool.evictions, 0)
        self.assertEqual(self.pool.pinned_pages(), 0)

    def test_lru_k_pool_gives_the_same_tree(self):
        tree = self.make(4, 4, capacity=8, policy="lru-k")
        keys = random.Random(2).sample(range(5000), 300)
        for key in keys:
            tree.insert(key, rid_for(key))
        tree.check()
        self.assertTrue(all(tree.search(key) == rid_for(key) for key in keys))

    def test_height_stays_logarithmic_with_the_default_fanout(self):
        self.workspace = Workspace(capacity=128)
        self.addCleanup(self.workspace.close)
        tree = BPlusTree.create(self.workspace.pool)
        for key in random.Random(3).sample(range(10 ** 6), 6000):
            tree.insert(key, rid_for(key))
        self.assertLessEqual(tree.height, 3)
        tree.check()


if __name__ == "__main__":
    unittest.main()
