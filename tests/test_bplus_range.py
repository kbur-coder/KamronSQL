import random
import unittest

from .test_bplus_tree import BPlusTreeBase, rid_for


class BPlusTreeRangeTests(BPlusTreeBase):
    def filled(self, count=300, leaf=4, internal=4, seed=1, sparse=True):
        tree = self.make(leaf, internal)
        keys = random.Random(seed).sample(range(count * 4), count) if sparse else list(range(count))
        for key in keys:
            tree.insert(key, rid_for(key))
        return tree, sorted(keys)

    @staticmethod
    def expected(keys, low, high, low_inclusive, high_inclusive):
        result = []
        for key in keys:
            if low is not None and (key < low or (key == low and not low_inclusive)):
                continue
            if high is not None and (key > high or (key == high and not high_inclusive)):
                continue
            result.append(key)
        return result

    def test_full_scan_is_sorted(self):
        tree, keys = self.filled()
        self.assertEqual([key for key, _ in tree.items()], keys)
        self.assertEqual([rid for _, rid in tree.items()], [rid_for(key) for key in keys])

    def test_random_ranges_match_a_sorted_list(self):
        tree, keys = self.filled()
        rng = random.Random(11)
        for _ in range(300):
            low = rng.choice([None, rng.randrange(-20, 1250)])
            high = rng.choice([None, rng.randrange(-20, 1250)])
            low_inclusive = rng.random() < 0.5
            high_inclusive = rng.random() < 0.5
            want = self.expected(keys, low, high, low_inclusive, high_inclusive)
            forward = [k for k, _ in tree.range(low, high, low_inclusive, high_inclusive)]
            backward = [k for k, _ in tree.range(low, high, low_inclusive, high_inclusive, reverse=True)]
            self.assertEqual(forward, want, (low, high, low_inclusive, high_inclusive))
            self.assertEqual(backward, want[::-1], (low, high, low_inclusive, high_inclusive))

    def test_bounds_that_hit_existing_keys(self):
        tree, keys = self.filled(100, sparse=False)
        self.assertEqual([k for k, _ in tree.range(10, 20)], list(range(10, 21)))
        self.assertEqual([k for k, _ in tree.range(10, 20, False, False)], list(range(11, 20)))
        self.assertEqual([k for k, _ in tree.range(10, 10)], [10])
        self.assertEqual([k for k, _ in tree.range(10, 10, False, True)], [])

    def test_inverted_and_out_of_range_queries_are_empty(self):
        tree, _ = self.filled(100, sparse=False)
        self.assertEqual(list(tree.range(50, 40)), [])
        self.assertEqual(list(tree.range(1000, 2000)), [])
        self.assertEqual(list(tree.range(-50, -10)), [])
        self.assertEqual(list(tree.range(50, 40, reverse=True)), [])

    def test_open_ended_ranges(self):
        tree, keys = self.filled(100, sparse=False)
        self.assertEqual([k for k, _ in tree.range(low=95)], [95, 96, 97, 98, 99])
        self.assertEqual([k for k, _ in tree.range(high=3)], [0, 1, 2, 3])

    def test_empty_tree(self):
        tree = self.make()
        self.assertEqual(list(tree.items()), [])
        self.assertEqual(list(tree.range(reverse=True)), [])
        self.assertIsNone(tree.first_key())
        self.assertIsNone(tree.last_key())

    def test_first_and_last_key(self):
        tree, keys = self.filled()
        self.assertEqual(tree.first_key(), keys[0])
        self.assertEqual(tree.last_key(), keys[-1])

    def test_ranges_after_deletions(self):
        tree, keys = self.filled(400, 3, 3, seed=4)
        rng = random.Random(8)
        for key in rng.sample(keys, 250):
            tree.delete(key)
            keys.remove(key)
        tree.check()
        self.assertEqual([k for k, _ in tree.items()], keys)
        self.assertEqual([k for k, _ in tree.range(reverse=True)], keys[::-1])
        for _ in range(100):
            low, high = sorted(rng.sample(range(0, 1600), 2))
            self.assertEqual(
                [k for k, _ in tree.range(low, high)], self.expected(keys, low, high, True, True)
            )

    def test_iteration_does_not_hold_pins(self):
        tree, _ = self.filled()
        iterator = tree.range()
        next(iterator)
        self.assertEqual(self.pool.pinned_pages(), 0)
        reverse = tree.range(reverse=True)
        next(reverse)
        self.assertEqual(self.pool.pinned_pages(), 0)

    def test_range_costs_few_page_reads_compared_with_a_full_scan(self):
        tree = self.make(50, 50, capacity=256)
        for key in range(5000):
            tree.insert(key, rid_for(key))
        self.pool.flush_all()
        before = self.pool.hits + self.pool.misses
        list(tree.range(2000, 2049))
        narrow = self.pool.hits + self.pool.misses - before
        before = self.pool.hits + self.pool.misses
        list(tree.items())
        wide = self.pool.hits + self.pool.misses - before
        self.assertLess(narrow * 10, wide)

    def test_destroy_returns_every_page(self):
        tree, _ = self.filled(200)
        tree.destroy()
        disk = self.workspace.disk
        self.assertEqual(disk.free_page_count(), disk.page_count - 1)


if __name__ == "__main__":
    unittest.main()
