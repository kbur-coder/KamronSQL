import random
import unittest

from src.storage import HeapFile, TupleTooLargeError

from .helpers import Workspace


class HeapFileTests(unittest.TestCase):
    def make(self, capacity=32, page_size=4096):
        self.workspace = Workspace(page_size=page_size, capacity=capacity)
        self.addCleanup(self.workspace.close)
        return HeapFile.create(self.workspace.pool)

    def test_insert_get_and_scan(self):
        heap = self.make()
        rids = [heap.insert(b"row-%d" % i) for i in range(20)]
        self.assertEqual(len(heap), 20)
        self.assertEqual([heap.get(rid) for rid in rids], [b"row-%d" % i for i in range(20)])
        self.assertEqual([payload for _, payload in heap.scan()], [b"row-%d" % i for i in range(20)])

    def test_rows_spill_into_new_pages(self):
        heap = self.make()
        rids = [heap.insert(bytes([i % 250]) * 500) for i in range(100)]
        pages = heap.page_ids()
        self.assertGreater(len(pages), 5)
        self.assertEqual(len({rid.page_id for rid in rids}), len(pages))
        self.assertEqual(sum(1 for _ in heap.scan()), 100)
        self.assertEqual(self.workspace.pool.pinned_pages(), 0)

    def test_delete(self):
        heap = self.make()
        rids = [heap.insert(b"r%d" % i) for i in range(10)]
        self.assertTrue(heap.delete(rids[3]))
        self.assertFalse(heap.delete(rids[3]))
        self.assertIsNone(heap.get(rids[3]))
        self.assertEqual(len(heap), 9)
        self.assertEqual(sum(1 for _ in heap.scan()), 9)

    def test_empty_middle_page_is_unlinked_and_freed(self):
        heap = self.make()
        rids = [heap.insert(bytes([i]) * 1000) for i in range(12)]
        pages = heap.page_ids()
        self.assertGreaterEqual(len(pages), 3)
        middle = pages[1]
        for rid in rids:
            if rid.page_id == middle:
                heap.delete(rid)
        self.assertNotIn(middle, heap.page_ids())
        self.assertEqual(self.workspace.disk.free_page_count(), 1)
        remaining = [rid for rid in rids if rid.page_id != middle]
        self.assertEqual(sum(1 for _ in heap.scan()), len(remaining))
        for rid in remaining:
            self.assertIsNotNone(heap.get(rid))

    def test_deleting_the_last_page_moves_the_tail_pointer(self):
        heap = self.make()
        rids = [heap.insert(bytes([i]) * 1000) for i in range(10)]
        last = heap.page_ids()[-1]
        for rid in rids:
            if rid.page_id == last:
                heap.delete(rid)
        self.assertEqual(heap.last_page_id, heap.page_ids()[-1])
        new_rid = heap.insert(b"after")
        self.assertEqual(heap.get(new_rid), b"after")
        self.assertEqual(sum(1 for _ in heap.scan()), len(heap))

    def test_first_page_is_kept_even_when_empty(self):
        heap = self.make()
        rid = heap.insert(b"only")
        heap.delete(rid)
        self.assertEqual(heap.page_ids(), [heap.first_page_id])
        heap.insert(b"again")
        self.assertEqual(len(heap), 1)

    def test_space_freed_by_deletes_is_reused(self):
        heap = self.make()
        rids = [heap.insert(bytes([i]) * 1000) for i in range(12)]
        first_page = heap.page_ids()[0]
        victims = [rid for rid in rids if rid.page_id == first_page][:2]
        pages_before = len(heap.page_ids())
        for rid in victims:
            heap.delete(rid)
        new_rid = heap.insert(b"z" * 900)
        self.assertEqual(new_rid.page_id, first_page)
        self.assertEqual(len(heap.page_ids()), pages_before)

    def test_update_in_place_keeps_the_rid(self):
        heap = self.make()
        rid = heap.insert(b"a" * 100)
        self.assertEqual(heap.update(rid, b"b" * 50), rid)
        self.assertEqual(heap.get(rid), b"b" * 50)

    def test_update_that_does_not_fit_moves_the_row(self):
        heap = self.make()
        rids = [heap.insert(bytes([i]) * 1000) for i in range(3)]
        moved = heap.update(rids[0], b"n" * 3000)
        self.assertNotEqual(moved, rids[0])
        self.assertIsNone(heap.get(rids[0]))
        self.assertEqual(heap.get(moved), b"n" * 3000)
        self.assertEqual(len(heap), 3)
        self.assertEqual(sum(1 for _ in heap.scan()), 3)

    def test_update_of_a_missing_row(self):
        heap = self.make()
        rid = heap.insert(b"x")
        heap.delete(rid)
        with self.assertRaises(KeyError):
            heap.update(rid, b"y")

    def test_too_large_rows_are_rejected(self):
        heap = self.make()
        with self.assertRaises(TupleTooLargeError):
            heap.insert(b"x" * 5000)
        rid = heap.insert(b"x")
        with self.assertRaises(TupleTooLargeError):
            heap.update(rid, b"x" * 5000)
        self.assertEqual(len(heap), 1)

    def test_heap_survives_reopening(self):
        heap = self.make(capacity=8)
        expected = {}
        for i in range(200):
            rid = heap.insert(b"value-%d" % i * 5)
            expected[rid] = b"value-%d" % i * 5
        meta = heap.meta_page_id
        self.workspace.reopen(capacity=8)
        reopened = HeapFile.open(self.workspace.pool, meta)
        self.assertEqual(len(reopened), 200)
        self.assertEqual(dict(reopened.scan()), expected)

    def test_destroy_frees_all_pages(self):
        heap = self.make()
        for i in range(50):
            heap.insert(bytes([i]) * 800)
        heap.destroy()
        disk = self.workspace.disk
        self.assertEqual(disk.free_page_count(), disk.page_count - 1)

    def test_two_buffer_frames_are_enough(self):
        heap = self.make(capacity=2)
        rids = [heap.insert(bytes([i % 250]) * 700) for i in range(60)]
        for rid in rids[::2]:
            heap.delete(rid)
        self.assertEqual(sum(1 for _ in heap.scan()), 30)
        self.assertEqual(self.workspace.pool.pinned_pages(), 0)

    def test_random_operations_match_a_dictionary(self):
        heap = self.make(capacity=12)
        rng = random.Random(21)
        model = {}
        for step in range(3000):
            action = rng.random()
            if action < 0.5 or not model:
                payload = bytes([rng.randrange(256)]) * rng.randint(1, 1500)
                model[heap.insert(payload)] = payload
            elif action < 0.75:
                rid = rng.choice(list(model))
                self.assertTrue(heap.delete(rid))
                del model[rid]
            else:
                rid = rng.choice(list(model))
                payload = bytes([rng.randrange(256)]) * rng.randint(1, 1800)
                new_rid = heap.update(rid, payload)
                del model[rid]
                model[new_rid] = payload
            if step % 300 == 0:
                self.assertEqual(dict(heap.scan()), model)
        self.assertEqual(dict(heap.scan()), model)
        self.assertEqual(len(heap), len(model))
        for rid, payload in model.items():
            self.assertEqual(heap.get(rid), payload)
        self.assertEqual(self.workspace.pool.pinned_pages(), 0)


if __name__ == "__main__":
    unittest.main()
