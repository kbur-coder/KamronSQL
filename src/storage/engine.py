import struct

from .bplus_tree import BPlusTree
from .buffer_pool import BufferPool
from .disk_manager import DiskManager
from .heap_file import HeapFile
from .layout import INVALID_PAGE

NAME_LENGTH = struct.Struct("<H")


class StorageEngine:
    def __init__(self, path, page_size=None, pool_size=128, policy="clock", flush_interval=None):
        self.disk = DiskManager(path, page_size)
        self.pool = BufferPool(self.disk, pool_size, policy)
        if self.disk.catalog_meta == INVALID_PAGE:
            self._catalog_heap = HeapFile.create(self.pool)
            self.disk.set_catalog_meta(self._catalog_heap.meta_page_id)
        else:
            self._catalog_heap = HeapFile.open(self.pool, self.disk.catalog_meta)
        self._catalog = {}
        for rid, raw in self._catalog_heap.scan():
            name, payload = self._decode(raw)
            self._catalog[name] = (rid, payload)
        if flush_interval:
            self.pool.start_flusher(flush_interval)

    @staticmethod
    def _encode(name, payload):
        encoded = name.encode("utf-8")
        return NAME_LENGTH.pack(len(encoded)) + encoded + payload

    @staticmethod
    def _decode(raw):
        length = NAME_LENGTH.unpack_from(raw, 0)[0]
        name = raw[NAME_LENGTH.size:NAME_LENGTH.size + length].decode("utf-8")
        return name, raw[NAME_LENGTH.size + length:]

    def new_heap(self):
        return HeapFile.create(self.pool)

    def open_heap(self, meta_page_id):
        return HeapFile.open(self.pool, meta_page_id)

    def new_index(self, leaf_max=None, internal_max=None):
        return BPlusTree.create(self.pool, leaf_max, internal_max)

    def open_index(self, meta_page_id):
        return BPlusTree.open(self.pool, meta_page_id)

    def catalog_names(self):
        return sorted(self._catalog)

    def catalog_get(self, name):
        entry = self._catalog.get(name)
        return None if entry is None else entry[1]

    def catalog_put(self, name, payload):
        raw = self._encode(name, payload)
        entry = self._catalog.get(name)
        if entry is None:
            rid = self._catalog_heap.insert(raw)
        else:
            rid = self._catalog_heap.update(entry[0], raw)
        self._catalog[name] = (rid, payload)

    def catalog_delete(self, name):
        entry = self._catalog.pop(name, None)
        if entry is None:
            return False
        self._catalog_heap.delete(entry[0])
        return True

    def flush(self):
        return self.pool.flush_all()

    def checkpoint(self):
        count = self.pool.flush_all()
        self.disk.sync()
        return count

    def stats(self):
        result = self.pool.stats()
        result["page_size"] = self.disk.page_size
        result["pages_in_file"] = self.disk.page_count
        result["free_pages"] = self.disk.free_page_count()
        result["disk_reads"] = self.disk.reads
        result["disk_writes"] = self.disk.writes
        return result

    def close(self):
        self.pool.close()
        self.disk.close()
