import struct

from .errors import InvalidPageError, TupleTooLargeError
from .layout import (
    COMMON_HEADER_SIZE,
    INVALID_PAGE,
    PAGE_HEAP,
    PAGE_HEAP_META,
    RID,
    get_page_type,
    set_page_type,
)
from .slotted_page import SlottedPage

META = struct.Struct("<iiq")
MAX_HINTS = 64


class HeapFile:
    def __init__(self, pool, meta_page_id):
        self.pool = pool
        self.meta_page_id = meta_page_id
        with pool.fetch(meta_page_id) as page:
            if get_page_type(page.data) != PAGE_HEAP_META:
                raise InvalidPageError("Страница %d не является метой кучи" % meta_page_id)
            self._first, self._last, self._rows = META.unpack_from(page.data, COMMON_HEADER_SIZE)
        self._hints = []

    @classmethod
    def create(cls, pool):
        with pool.new_page() as first:
            first_id = first.page_id
            SlottedPage.format(first.data)
            first.mark_dirty()
        with pool.new_page() as meta:
            meta_id = meta.page_id
            set_page_type(meta.data, PAGE_HEAP_META)
            META.pack_into(meta.data, COMMON_HEADER_SIZE, first_id, first_id, 0)
            meta.mark_dirty()
        return cls(pool, meta_id)

    @classmethod
    def open(cls, pool, meta_page_id):
        return cls(pool, meta_page_id)

    def __len__(self):
        return self._rows

    @property
    def first_page_id(self):
        return self._first

    @property
    def last_page_id(self):
        return self._last

    def _write_meta(self):
        with self.pool.fetch(self.meta_page_id) as page:
            META.pack_into(page.data, COMMON_HEADER_SIZE, self._first, self._last, self._rows)
            page.mark_dirty()

    def _check_size(self, payload):
        limit = SlottedPage.max_tuple_size(self.pool.disk.page_size)
        if len(payload) > limit:
            raise TupleTooLargeError("Строка занимает %d байт, а в странице помещается не больше %d" % (len(payload), limit))

    def _remember(self, page_id):
        if page_id not in self._hints:
            self._hints.append(page_id)
            if len(self._hints) > MAX_HINTS:
                self._hints.pop(0)

    def _try_page(self, page_id, payload):
        with self.pool.fetch(page_id) as page:
            slotted = SlottedPage(page.data)
            if not slotted.can_insert(len(payload)):
                return None
            slot = slotted.insert(payload)
            page.mark_dirty()
            return RID(page_id, slot)

    def _insert_new_page(self, payload):
        with self.pool.new_page() as page:
            new_id = page.page_id
            slotted = SlottedPage.format(page.data, prev_page=self._last)
            slot = slotted.insert(payload)
            page.mark_dirty()
        with self.pool.fetch(self._last) as last:
            SlottedPage(last.data).next_page = new_id
            last.mark_dirty()
        self._last = new_id
        return RID(new_id, slot)

    def insert(self, payload):
        self._check_size(payload)
        rid = None
        while self._hints and rid is None:
            candidate = self._hints[-1]
            rid = self._try_page(candidate, payload)
            if rid is None:
                self._hints.pop()
        if rid is None:
            rid = self._try_page(self._last, payload)
        if rid is None:
            rid = self._insert_new_page(payload)
        self._rows += 1
        self._write_meta()
        return rid

    def get(self, rid):
        with self.pool.fetch(rid.page_id) as page:
            if get_page_type(page.data) != PAGE_HEAP:
                raise InvalidPageError("Страница %d не является страницей кучи" % rid.page_id)
            return SlottedPage(page.data).get(rid.slot)

    def delete(self, rid):
        with self.pool.fetch(rid.page_id) as page:
            if get_page_type(page.data) != PAGE_HEAP:
                raise InvalidPageError("Страница %d не является страницей кучи" % rid.page_id)
            slotted = SlottedPage(page.data)
            if not slotted.delete(rid.slot):
                return False
            page.mark_dirty()
            empty = slotted.is_empty()
            previous = slotted.prev_page
            following = slotted.next_page
        self._rows -= 1
        if empty and rid.page_id != self._first:
            self._unlink(rid.page_id, previous, following)
        else:
            self._remember(rid.page_id)
        self._write_meta()
        return True

    def _unlink(self, page_id, previous, following):
        with self.pool.fetch(previous) as page:
            SlottedPage(page.data).next_page = following
            page.mark_dirty()
        if following != INVALID_PAGE:
            with self.pool.fetch(following) as page:
                SlottedPage(page.data).prev_page = previous
                page.mark_dirty()
        if self._last == page_id:
            self._last = previous
        if page_id in self._hints:
            self._hints.remove(page_id)
        self.pool.delete_page(page_id)

    def update(self, rid, payload):
        self._check_size(payload)
        with self.pool.fetch(rid.page_id) as page:
            if get_page_type(page.data) != PAGE_HEAP:
                raise InvalidPageError("Страница %d не является страницей кучи" % rid.page_id)
            slotted = SlottedPage(page.data)
            if slotted.get(rid.slot) is None:
                raise KeyError(rid)
            if slotted.update(rid.slot, payload):
                page.mark_dirty()
                return rid
        new_rid = self.insert(payload)
        self.delete(rid)
        return new_rid

    def scan(self):
        page_id = self._first
        while page_id != INVALID_PAGE:
            with self.pool.fetch(page_id) as page:
                slotted = SlottedPage(page.data)
                items = list(slotted.slots())
                following = slotted.next_page
            for slot, payload in items:
                yield RID(page_id, slot), payload
            page_id = following

    def page_ids(self):
        result = []
        page_id = self._first
        while page_id != INVALID_PAGE:
            result.append(page_id)
            with self.pool.fetch(page_id) as page:
                page_id = SlottedPage(page.data).next_page
        return result

    def destroy(self):
        for page_id in self.page_ids():
            self.pool.delete_page(page_id)
        self.pool.delete_page(self.meta_page_id)
