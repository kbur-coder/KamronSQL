import random
import unittest

from src.storage import RID

from .test_bplus_tree import BPlusTreeBase, rid_for


class BPlusTreeDeleteTests(BPlusTreeBase):
    def test_delete_from_a_single_leaf(self):
        tree = self.make()
        for key in (1, 2, 3):
            tree.insert(key, rid_for(key))
        self.assertTrue(tree.delete(2))
        self.assertFalse(tree.delete(2))
        self.assertFalse(tree.delete(99))
        self.assertIsNone(tree.search(2))
        self.assertEqual(len(tree), 2)
        tree.check()

    def test_every_deletion_order_keeps_the_invariants(self):
        for leaf, internal in ((3, 3), (4, 4), (5, 6)):
            for order in ("ascending", "descending", "random"):
                tree = self.make(leaf, internal)
                keys = list(range(120))
                for key in random.Random(7).sample(keys, len(keys)):
                    tree.insert(key, rid_for(key))
                victims = keys[:]
                if order == "descending":
                    victims.reverse()
                elif order == "random":
                    random.Random(leaf + internal).shuffle(victims)
                remaining = set(keys)
                for key in victims:
                    self.assertTrue(tree.delete(key))
                    remaining.discard(key)
                    tree.check()
                    self.assertEqual(len(tree), len(remaining))
                    self.assertIsNone(tree.search(key))
                self.assertEqual(tree.height, 1)
                self.assertEqual(self.pool.pinned_pages(), 0)

    def test_height_shrinks_and_pages_are_freed(self):
        tree = self.make(3, 3)
        for key in range(100):
            tree.insert(key, rid_for(key))
        grown = tree.height
        self.assertGreaterEqual(grown, 4)
        for key in range(100):
            tree.delete(key)
        self.assertEqual(tree.height, 1)
        self.assertGreater(self.workspace.disk.free_page_count(), 10)
        pages = self.workspace.disk.page_count
        for key in range(100):
            tree.insert(key, rid_for(key))
        self.assertEqual(self.workspace.disk.page_count, pages)
        tree.check()

    def test_survivors_are_untouched_after_partial_deletion(self):
        tree = self.make(4, 4)
        for key in range(300):
            tree.insert(key, rid_for(key))
        for key in range(0, 300, 3):
            tree.delete(key)
        tree.check()
        for key in range(300):
            expected = None if key % 3 == 0 else rid_for(key)
            self.assertEqual(tree.search(key), expected)

    def test_reinsertion_after_deletion(self):
        tree = self.make(3, 3)
        for key in range(50):
            tree.insert(key, rid_for(key))
        for key in range(50):
            tree.delete(key)
        for key in range(50, 0, -1):
            tree.insert(key, rid_for(key))
        tree.check()
        self.assertEqual(len(tree), 50)

    def test_two_buffer_frames_are_enough(self):
        tree = self.make(4, 4, capacity=2)
        keys = random.Random(12).sample(range(2000), 250)
        for key in keys:
            tree.insert(key, rid_for(key))
        for key in keys[:200]:
            self.assertTrue(tree.delete(key))
        tree.check()
        self.assertEqual(len(tree), 50)
        self.assertEqual(self.pool.pinned_pages(), 0)

    def test_random_mix_matches_a_dictionary(self):
        for leaf, internal, seed in ((3, 3, 1), (4, 5, 2), (6, 3, 3)):
            tree = self.make(leaf, internal, capacity=16)
            rng = random.Random(seed)
            model = {}
            for step in range(2500):
                key = rng.randrange(400)
                if rng.random() < 0.55:
                    if key in model:
                        with self.assertRaises(Exception):
                            tree.insert(key, RID(1, 1))
                    else:
                        model[key] = rid_for(key)
                        tree.insert(key, model[key])
                else:
                    self.assertEqual(tree.delete(key), key in model)
                    model.pop(key, None)
                if step % 100 == 0:
                    tree.check()
            tree.check()
            self.assertEqual(len(tree), len(model))
            for key in range(400):
                self.assertEqual(tree.search(key), model.get(key))
            self.assertEqual(self.pool.pinned_pages(), 0)


if __name__ == "__main__":
    unittest.main()
