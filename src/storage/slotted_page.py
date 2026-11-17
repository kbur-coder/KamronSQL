import struct

from .layout import COMMON_HEADER_SIZE, INVALID_PAGE, PAGE_HEAP, set_page_type

U16 = struct.Struct("<H")
I32 = struct.Struct("<i")
SLOT = struct.Struct("<HH")
SLOT_SIZE = SLOT.size

OFFSET_SLOTS = COMMON_HEADER_SIZE
OFFSET_TUPLE_START = COMMON_HEADER_SIZE + 2
OFFSET_LIVE = COMMON_HEADER_SIZE + 4
OFFSET_NEXT = COMMON_HEADER_SIZE + 8
OFFSET_PREV = COMMON_HEADER_SIZE + 12
SLOT_ARRAY_OFFSET = COMMON_HEADER_SIZE + 16


class SlottedPage:
    def __init__(self, data):
        self.data = data
        self.size = len(data)

    @classmethod
    def format(cls, data, next_page=INVALID_PAGE, prev_page=INVALID_PAGE):
        data[:] = bytes(len(data))
        set_page_type(data, PAGE_HEAP)
        page = cls(data)
        page.tuple_start = len(data)
        page.next_page = next_page
        page.prev_page = prev_page
        return page

    @staticmethod
    def max_tuple_size(page_size):
        return page_size - SLOT_ARRAY_OFFSET - SLOT_SIZE

    @property
    def num_slots(self):
        return U16.unpack_from(self.data, OFFSET_SLOTS)[0]

    @num_slots.setter
    def num_slots(self, value):
        U16.pack_into(self.data, OFFSET_SLOTS, value)

    @property
    def tuple_start(self):
        return U16.unpack_from(self.data, OFFSET_TUPLE_START)[0]

    @tuple_start.setter
    def tuple_start(self, value):
        U16.pack_into(self.data, OFFSET_TUPLE_START, value)

    @property
    def live_count(self):
        return U16.unpack_from(self.data, OFFSET_LIVE)[0]

    @live_count.setter
    def live_count(self, value):
        U16.pack_into(self.data, OFFSET_LIVE, value)

    @property
    def next_page(self):
        return I32.unpack_from(self.data, OFFSET_NEXT)[0]

    @next_page.setter
    def next_page(self, value):
        I32.pack_into(self.data, OFFSET_NEXT, value)

    @property
    def prev_page(self):
        return I32.unpack_from(self.data, OFFSET_PREV)[0]

    @prev_page.setter
    def prev_page(self, value):
        I32.pack_into(self.data, OFFSET_PREV, value)

    def _slot(self, index):
        return SLOT.unpack_from(self.data, SLOT_ARRAY_OFFSET + index * SLOT_SIZE)

    def _set_slot(self, index, offset, length):
        SLOT.pack_into(self.data, SLOT_ARRAY_OFFSET + index * SLOT_SIZE, offset, length)

    def slot_array_end(self):
        return SLOT_ARRAY_OFFSET + self.num_slots * SLOT_SIZE

    def used_bytes(self):
        total = 0
        for index in range(self.num_slots):
            offset, length = self._slot(index)
            if offset:
                total += length
        return total

    def contiguous_free(self):
        return self.tuple_start - self.slot_array_end()

    def total_free(self):
        return self.size - self.slot_array_end() - self.used_bytes()

    def is_empty(self):
        return self.live_count == 0

    def _first_empty_slot(self):
        for index in range(self.num_slots):
            if self._slot(index)[0] == 0:
                return index
        return None

    def _check_payload(self, payload):
        if len(payload) == 0:
            raise ValueError("Пустой кортеж нельзя хранить")
        if len(payload) > self.max_tuple_size(self.size):
            raise ValueError("Кортеж не помещается в страницу")

    def can_insert(self, length):
        extra = 0 if self._first_empty_slot() is not None else SLOT_SIZE
        return length + extra <= self.total_free()

    def insert(self, payload):
        self._check_payload(payload)
        length = len(payload)
        slot = self._first_empty_slot()
        extra = 0 if slot is not None else SLOT_SIZE
        if length + extra > self.contiguous_free():
            if length + extra > self.total_free():
                return None
            self.compact()
        start = self.tuple_start - length
        self.data[start:start + length] = payload
        if slot is None:
            slot = self.num_slots
            self.num_slots = slot + 1
        self._set_slot(slot, start, length)
        self.tuple_start = start
        self.live_count = self.live_count + 1
        return slot

    def get(self, slot):
        if not 0 <= slot < self.num_slots:
            return None
        offset, length = self._slot(slot)
        if offset == 0:
            return None
        return bytes(self.data[offset:offset + length])

    def delete(self, slot):
        if not 0 <= slot < self.num_slots:
            return False
        offset, _ = self._slot(slot)
        if offset == 0:
            return False
        self._set_slot(slot, 0, 0)
        self.live_count = self.live_count - 1
        count = self.num_slots
        while count > 0 and self._slot(count - 1)[0] == 0:
            count -= 1
        self.num_slots = count
        if self.live_count == 0:
            self.num_slots = 0
            self.tuple_start = self.size
        return True

    def update(self, slot, payload):
        self._check_payload(payload)
        if not 0 <= slot < self.num_slots:
            return False
        offset, length = self._slot(slot)
        if offset == 0:
            return False
        new_length = len(payload)
        if new_length <= length:
            self.data[offset:offset + new_length] = payload
            self._set_slot(slot, offset, new_length)
            return True
        available = self.size - self.slot_array_end() - (self.used_bytes() - length)
        if new_length > available:
            return False
        self._set_slot(slot, 0, 0)
        if new_length > self.contiguous_free():
            self.compact()
        start = self.tuple_start - new_length
        self.data[start:start + new_length] = payload
        self._set_slot(slot, start, new_length)
        self.tuple_start = start
        return True

    def compact(self):
        items = []
        for index in range(self.num_slots):
            offset, length = self._slot(index)
            if offset:
                items.append((index, bytes(self.data[offset:offset + length])))
        position = self.size
        for index, payload in items:
            position -= len(payload)
            self.data[position:position + len(payload)] = payload
            self._set_slot(index, position, len(payload))
        self.tuple_start = position

    def slots(self):
        for index in range(self.num_slots):
            offset, length = self._slot(index)
            if offset:
                yield index, bytes(self.data[offset:offset + length])
