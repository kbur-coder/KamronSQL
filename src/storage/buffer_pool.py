import threading

from .errors import BufferPoolFullError, InvalidPageError
from .layout import INVALID_PAGE
from .replacer import make_replacer


class Frame:
    __slots__ = ("index", "page_id", "data", "pin_count", "dirty")

    def __init__(self, index):
        self.index = index
        self.page_id = INVALID_PAGE
        self.data = None
        self.pin_count = 0
        self.dirty = False


class PageGuard:
    def __init__(self, pool, frame):
        self._pool = pool
        self._frame = frame
        self._released = False

    @property
    def data(self):
        return self._frame.data

    @property
    def page_id(self):
        return self._frame.page_id

    def mark_dirty(self):
        self._frame.dirty = True

    def release(self):
        if not self._released:
            self._released = True
            self._pool._unpin(self._frame)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
        return False


class BufferPool:
    def __init__(self, disk, capacity=128, policy="clock", k=2):
        if capacity < 1:
            raise ValueError("Размер буферного пула должен быть положительным")
        self.disk = disk
        self.capacity = capacity
        self.policy = policy
        self._lock = threading.RLock()
        self._frames = [Frame(index) for index in range(capacity)]
        self._page_table = {}
        self._free_frames = list(range(capacity - 1, -1, -1))
        self._replacer = make_replacer(policy, capacity, k)
        self._flusher = None
        self._flusher_stop = None
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.writebacks = 0

    def _reset(self, frame):
        frame.page_id = INVALID_PAGE
        frame.data = None
        frame.pin_count = 0
        frame.dirty = False

    def _claim_frame(self):
        if self._free_frames:
            return self._free_frames.pop()
        index = self._replacer.evict()
        if index is None:
            raise BufferPoolFullError("Все страницы буферного пула закреплены")
        frame = self._frames[index]
        if frame.dirty:
            try:
                self.disk.write_page(frame.page_id, frame.data)
            except Exception:
                self._replacer.set_evictable(index, True)
                raise
            self.writebacks += 1
        del self._page_table[frame.page_id]
        self._reset(frame)
        self.evictions += 1
        return index

    def _pin(self, frame):
        frame.pin_count += 1
        self._replacer.record_access(frame.index)
        self._replacer.set_evictable(frame.index, False)

    def fetch(self, page_id):
        with self._lock:
            index = self._page_table.get(page_id)
            if index is not None:
                self.hits += 1
            else:
                self.misses += 1
                index = self._claim_frame()
                try:
                    data = self.disk.read_page(page_id)
                except Exception:
                    self._free_frames.append(index)
                    raise
                frame = self._frames[index]
                frame.page_id = page_id
                frame.data = data
                frame.dirty = False
                frame.pin_count = 0
                self._page_table[page_id] = index
            frame = self._frames[index]
            self._pin(frame)
            return PageGuard(self, frame)

    def new_page(self):
        with self._lock:
            index = self._claim_frame()
            try:
                page_id = self.disk.allocate_page()
            except Exception:
                self._free_frames.append(index)
                raise
            frame = self._frames[index]
            frame.page_id = page_id
            frame.data = bytearray(self.disk.page_size)
            frame.dirty = True
            frame.pin_count = 0
            self._page_table[page_id] = index
            self._pin(frame)
            return PageGuard(self, frame)

    def _unpin(self, frame):
        with self._lock:
            if frame.pin_count <= 0:
                raise InvalidPageError("Страница %d не закреплена" % frame.page_id)
            frame.pin_count -= 1
            if frame.pin_count == 0:
                self._replacer.set_evictable(frame.index, True)

    def delete_page(self, page_id):
        with self._lock:
            index = self._page_table.get(page_id)
            if index is not None:
                frame = self._frames[index]
                if frame.pin_count > 0:
                    raise InvalidPageError("Нельзя удалить закреплённую страницу %d" % page_id)
                del self._page_table[page_id]
                self._replacer.remove(index)
                self._reset(frame)
                self._free_frames.append(index)
            self.disk.deallocate_page(page_id)

    def flush_page(self, page_id):
        with self._lock:
            index = self._page_table.get(page_id)
            if index is None:
                return False
            frame = self._frames[index]
            if not frame.dirty:
                return False
            self.disk.write_page(frame.page_id, frame.data)
            frame.dirty = False
            self.writebacks += 1
            return True

    def flush_all(self):
        with self._lock:
            count = 0
            for frame in self._frames:
                if frame.page_id != INVALID_PAGE and frame.dirty:
                    self.disk.write_page(frame.page_id, frame.data)
                    frame.dirty = False
                    self.writebacks += 1
                    count += 1
            return count

    def pinned_pages(self):
        with self._lock:
            return sum(1 for frame in self._frames if frame.pin_count > 0)

    def dirty_pages(self):
        with self._lock:
            return sum(1 for frame in self._frames if frame.page_id != INVALID_PAGE and frame.dirty)

    def cached_pages(self):
        with self._lock:
            return len(self._page_table)

    def stats(self):
        with self._lock:
            total = self.hits + self.misses
            return {
                "capacity": self.capacity,
                "cached": len(self._page_table),
                "dirty": self.dirty_pages(),
                "pinned": self.pinned_pages(),
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio": (self.hits / total) if total else 0.0,
                "evictions": self.evictions,
                "writebacks": self.writebacks,
                "policy": self.policy,
            }

    def close(self):
        self.flush_all()
