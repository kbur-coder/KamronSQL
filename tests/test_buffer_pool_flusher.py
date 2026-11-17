import threading
import time
import unittest

from .helpers import Workspace


def wait_for(condition, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class BackgroundFlusherTests(unittest.TestCase):
    def make(self, capacity=16):
        workspace = Workspace(capacity=capacity)
        self.addCleanup(workspace.close)
        return workspace

    def test_dirty_unpinned_pages_are_written_in_the_background(self):
        workspace = self.make()
        pool = workspace.pool
        with pool.new_page() as page:
            page.data[8:11] = b"bgw"
            page.mark_dirty()
            page_id = page.page_id
        self.assertEqual(pool.dirty_pages(), 1)
        pool.start_flusher(0.02)
        self.assertTrue(wait_for(lambda: pool.dirty_pages() == 0))
        self.assertEqual(bytes(workspace.disk.read_page(page_id)[8:11]), b"bgw")
        pool.stop_flusher()

    def test_pinned_pages_are_left_alone(self):
        workspace = self.make()
        pool = workspace.pool
        guard = pool.new_page()
        guard.data[8:9] = b"p"
        guard.mark_dirty()
        pool.start_flusher(0.02)
        time.sleep(0.2)
        self.assertEqual(pool.dirty_pages(), 1)
        guard.release()
        self.assertTrue(wait_for(lambda: pool.dirty_pages() == 0))
        pool.stop_flusher()

    def test_stop_is_idempotent_and_close_flushes_everything(self):
        workspace = self.make()
        pool = workspace.pool
        pool.start_flusher(0.5)
        pool.start_flusher(0.5)
        with pool.new_page() as page:
            page.mark_dirty()
        pool.close()
        self.assertIsNone(pool._flusher)
        self.assertEqual(pool.dirty_pages(), 0)
        pool.stop_flusher()

    def test_flusher_thread_is_a_daemon(self):
        pool = self.make().pool
        pool.start_flusher(0.5)
        self.assertTrue(pool._flusher.daemon)
        pool.stop_flusher()

    def test_concurrent_writers_with_a_running_flusher(self):
        workspace = self.make(capacity=32)
        pool = workspace.pool
        ids = []
        for _ in range(4):
            with pool.new_page() as page:
                page.mark_dirty()
                ids.append(page.page_id)
        pool.start_flusher(0.005)
        errors = []

        def work(page_id):
            try:
                for _ in range(200):
                    with pool.fetch(page_id) as page:
                        page.data[8] = (page.data[8] + 1) % 256
                        page.mark_dirty()
            except Exception as error:
                errors.append(error)

        threads = [threading.Thread(target=work, args=(page_id,)) for page_id in ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        pool.stop_flusher()
        pool.flush_all()
        self.assertEqual(errors, [])
        for page_id in ids:
            self.assertEqual(workspace.disk.read_page(page_id)[8], 200)
        self.assertEqual(pool.pinned_pages(), 0)


if __name__ == "__main__":
    unittest.main()
