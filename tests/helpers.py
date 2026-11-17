import os
import tempfile

from src.storage import BufferPool, DiskManager


class Workspace:
    def __init__(self, page_size=4096, capacity=64, policy="clock"):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "test.db")
        self.disk = DiskManager(self.path, page_size)
        self.pool = BufferPool(self.disk, capacity, policy)

    def reopen(self, capacity=None, policy=None):
        self.pool.close()
        self.disk.close()
        self.disk = DiskManager(self.path)
        self.pool = BufferPool(
            self.disk,
            self.pool.capacity if capacity is None else capacity,
            self.pool.policy if policy is None else policy,
        )

    def close(self):
        if not self.disk._file.closed:
            self.pool.close()
            self.disk.close()
        self.dir.cleanup()
