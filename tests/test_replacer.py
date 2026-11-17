import unittest

from src.storage import ClockReplacer, LRUKReplacer, make_replacer


class ClockReplacerTests(unittest.TestCase):
    def test_nothing_is_evicted_when_nothing_is_evictable(self):
        replacer = ClockReplacer(3)
        self.assertIsNone(replacer.evict())

    def test_pinned_frames_are_skipped(self):
        replacer = ClockReplacer(3)
        for frame in range(3):
            replacer.record_access(frame)
        replacer.set_evictable(1, True)
        self.assertEqual(replacer.evict(), 1)
        self.assertIsNone(replacer.evict())

    def test_referenced_frames_get_a_second_chance(self):
        replacer = ClockReplacer(3)
        for frame in range(3):
            replacer.record_access(frame)
            replacer.set_evictable(frame, True)
        replacer.referenced[1] = False
        replacer.referenced[2] = False
        self.assertEqual(replacer.evict(), 1)
        replacer.set_evictable(1, True)
        self.assertEqual(replacer.evict(), 2)

    def test_all_referenced_frames_still_yield_a_victim(self):
        replacer = ClockReplacer(4)
        for frame in range(4):
            replacer.record_access(frame)
            replacer.set_evictable(frame, True)
        self.assertIsNotNone(replacer.evict())

    def test_remove_clears_state(self):
        replacer = ClockReplacer(2)
        replacer.record_access(0)
        replacer.set_evictable(0, True)
        replacer.remove(0)
        self.assertIsNone(replacer.evict())


class LRUKReplacerTests(unittest.TestCase):
    def test_frames_with_fewer_than_k_accesses_go_first_in_fifo_order(self):
        replacer = LRUKReplacer(3, k=2)
        for frame in (0, 0, 1, 2):
            replacer.record_access(frame)
        for frame in range(3):
            replacer.set_evictable(frame, True)
        self.assertEqual(replacer.evict(), 1)
        self.assertEqual(replacer.evict(), 2)
        self.assertEqual(replacer.evict(), 0)

    def test_among_full_histories_the_oldest_kth_access_loses(self):
        replacer = LRUKReplacer(2, k=2)
        for frame in (0, 1, 1, 0):
            replacer.record_access(frame)
        for frame in range(2):
            replacer.set_evictable(frame, True)
        self.assertEqual(replacer.evict(), 0)

    def test_a_long_scan_does_not_push_out_a_hot_frame(self):
        replacer = LRUKReplacer(3, k=2)
        replacer.record_access(0)
        replacer.record_access(0)
        replacer.set_evictable(0, True)
        for turn in range(10):
            replacer.record_access(1)
            replacer.set_evictable(1, True)
            self.assertEqual(replacer.evict(), 1)

    def test_non_evictable_frames_are_never_chosen(self):
        replacer = LRUKReplacer(2, k=2)
        replacer.record_access(0)
        self.assertIsNone(replacer.evict())

    def test_remove_resets_history(self):
        replacer = LRUKReplacer(2, k=2)
        replacer.record_access(0)
        replacer.record_access(0)
        replacer.remove(0)
        self.assertEqual(len(replacer.history[0]), 0)

    def test_invalid_k(self):
        with self.assertRaises(ValueError):
            LRUKReplacer(2, k=0)


class FactoryTests(unittest.TestCase):
    def test_known_policies(self):
        self.assertIsInstance(make_replacer("clock", 4), ClockReplacer)
        self.assertIsInstance(make_replacer("lru-k", 4), LRUKReplacer)

    def test_unknown_policy(self):
        with self.assertRaises(ValueError):
            make_replacer("fifo", 4)


if __name__ == "__main__":
    unittest.main()
