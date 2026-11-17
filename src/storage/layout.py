import struct
from typing import NamedTuple

DEFAULT_PAGE_SIZE = 4096
MIN_PAGE_SIZE = 512
MAX_PAGE_SIZE = 32768
INVALID_PAGE = -1

PAGE_FREE = 0
PAGE_HEADER = 1
PAGE_HEAP = 2
PAGE_HEAP_META = 3
PAGE_BTREE_META = 4
PAGE_BTREE_INTERNAL = 5
PAGE_BTREE_LEAF = 6
PAGE_RESERVED = 7

COMMON_HEADER = struct.Struct("<BBHI")
COMMON_HEADER_SIZE = COMMON_HEADER.size


class RID(NamedTuple):
    page_id: int
    slot: int


def is_valid_page_size(size):
    return MIN_PAGE_SIZE <= size <= MAX_PAGE_SIZE and size & (size - 1) == 0


def get_page_type(data):
    return data[0]


def set_page_type(data, page_type):
    data[0] = page_type
