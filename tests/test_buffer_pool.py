import unittest

from src.storage import BufferPoolFullError, InvalidPageError

from .helpers import Workspace


class BufferPoolTests(unittest.TestCase):
    def make(self, capacity=3, policy="clock"):
        workspace = Workspace(capacity=capacity, policy=policy)
        self.addCleanup(workspace.close)
        return workspace

    def write(self, pool, text):
        with pool.new_page() as page:
            page.data[8:8 + len(text)] = text
            page.mark_dirty()
            return page.page_id

    def read(self, pool, page_id, length):
        with pool.fetch(page_id) as page:
            return bytes(page.data[8:8 + length])

    def test_new_pages_are_zeroed_and_numbered_from_one(self):
        pool = self.make().pool
        with pool.new_page() as first:
            self.assertEqual(first.page_id, 1)
            self.assertEqual(bytes(first.data), bytes(4096))
        with pool.new_page() as second:
            self.assertEqual(second.page_id, 2)

    def test_hits_and_misses_are_counted(self):
        pool = self.make().pool
        page_id = self.write(pool, b"abc")
        pool.fetch(page_id).release()
        pool.fetch(page_id).release()
        self.assertEqual(pool.hits, 2)
        self.assertEqual(pool.misses, 0)
        stats = pool.stats()
        self.assertEqual(stats["hit_ratio"], 1.0)

    def test_dirty_pages_are_written_back_on_eviction(self):
        workspace = self.make(capacity=2)
        pool = workspace.pool
        ids = [self.write(pool, b"page%d" % i) for i in range(6)]
        self.assertGreater(pool.evictions, 0)
        self.assertGreater(pool.writebacks, 0)
        for index, page_id in enumerate(ids):
            self.assertEqual(self.read(pool, page_id, 5), b"page%d" % index)

    def test_clean_pages_are_not_written_on_eviction(self):
        workspace = self.make(capacity=2)
        pool = workspace.pool
        ids = [self.write(pool, b"x") for _ in range(6)]
        pool.flush_all()
        writebacks = pool.writebacks
        disk_writes = workspace.disk.writes
        for page_id in ids * 3:
            self.read(pool, page_id, 1)
        self.assertGreater(pool.evictions, 0)
        self.assertEqual(pool.writebacks, writebacks)
        self.assertEqual(workspace.disk.writes, disk_writes)

    def test_pinned_pages_are_never_evicted(self):
        pool = self.make(capacity=3).pool
        guards = [pool.new_page() for _ in range(3)]
        with self.assertRaises(BufferPoolFullError):
            pool.new_page()
        guards[0].release()
        with pool.new_page():
            pass
        for guard in guards[1:]:
            guard.release()
        self.assertEqual(pool.pinned_pages(), 0)

    def test_release_twice_is_harmless_but_extra_unpin_is_an_error(self):
        pool = self.make().pool
        guard = pool.new_page()
        guard.release()
        guard.release()
        with self.assertRaises(InvalidPageError):
            pool._unpin(guard._frame)

    def test_pin_count_allows_the_same_page_to_be_held_twice(self):
        pool = self.make().pool
        page_id = self.write(pool, b"q")
        first = pool.fetch(page_id)
        second = pool.fetch(page_id)
        self.assertEqual(pool.stats()["pinned"], 1)
        first.release()
        self.assertEqual(pool.stats()["pinned"], 1)
        second.release()
        self.assertEqual(pool.stats()["pinned"], 0)

    def test_flush_all_makes_data_visible_on_disk(self):
        workspace = self.make(capacity=8)
        page_id = self.write(workspace.pool, b"durable")
        self.assertEqual(workspace.pool.dirty_pages(), 1)
        self.assertEqual(workspace.pool.flush_all(), 1)
        self.assertEqual(workspace.pool.dirty_pages(), 0)
        self.assertEqual(bytes(workspace.disk.read_page(page_id)[8:15]), b"durable")

    def test_data_survives_reopening_the_file(self):
        workspace = self.make(capacity=8)
        page_id = self.write(workspace.pool, b"persist")
        workspace.reopen()
        self.assertEqual(self.read(workspace.pool, page_id, 7), b"persist")

    def test_delete_page_frees_it_for_reuse(self):
        workspace = self.make()
        pool = workspace.pool
        page_id = self.write(pool, b"gone")
        pool.delete_page(page_id)
        self.assertEqual(workspace.disk.free_page_count(), 1)
        with pool.new_page() as page:
            self.assertEqual(page.page_id, page_id)
            self.assertEqual(bytes(page.data), bytes(4096))

    def test_pinned_page_cannot_be_deleted(self):
        pool = self.make().pool
        guard = pool.new_page()
        with self.assertRaises(InvalidPageError):
            pool.delete_page(guard.page_id)
        guard.release()

    def test_fetching_a_missing_page_does_not_leak_a_frame(self):
        pool = self.make(capacity=2).pool
        for _ in range(5):
            with self.assertRaises(InvalidPageError):
                pool.fetch(500)
        self.write(pool, b"a")
        self.write(pool, b"b")

    def test_lru_k_keeps_a_hot_page_during_a_scan(self):
        workspace = self.make(capacity=3, policy="lru-k")
        pool = workspace.pool
        hot = self.write(pool, b"hot")
        for _ in range(3):
            self.read(pool, hot, 3)
        cold = [self.write(pool, b"c%d" % i) for i in range(10)]
        hits = pool.hits
        self.read(pool, hot, 3)
        self.assertEqual(pool.hits, hits + 1)
        self.assertEqual(len(cold), 10)

    def test_both_policies_return_the_same_data(self):
        for policy in ("clock", "lru-k"):
            workspace = self.make(capacity=4, policy=policy)
            ids = [self.write(workspace.pool, b"v%02d" % i) for i in range(40)]
            for index, page_id in enumerate(ids):
                self.assertEqual(self.read(workspace.pool, page_id, 3), b"v%02d" % index)
            self.assertEqual(workspace.pool.pinned_pages(), 0)

    def test_invalid_capacity(self):
        workspace = self.make()
        from src.storage import BufferPool
        with self.assertRaises(ValueError):
            BufferPool(workspace.disk, 0)


if __name__ == "__main__":
    unittest.main()
