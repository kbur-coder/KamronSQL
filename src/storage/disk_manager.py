import os
import struct
import threading
import zlib

from .errors import CorruptPageError, FileFormatError, InvalidPageError
from .layout import (
    COMMON_HEADER_SIZE,
    DEFAULT_PAGE_SIZE,
    INVALID_PAGE,
    PAGE_FREE,
    PAGE_HEADER,
    PAGE_RESERVED,
    get_page_type,
    is_valid_page_size,
    set_page_type,
)

MAGIC = b"KAMRONDB"
VERSION = 1
HEADER = struct.Struct("<8sHIIii")
FREE_NEXT = struct.Struct("<i")
CHECKSUM_OFFSET = 4


def compute_checksum(data):
    return zlib.crc32(memoryview(data)[COMMON_HEADER_SIZE:]) & 0xFFFFFFFF


def stamp_checksum(data):
    struct.pack_into("<I", data, CHECKSUM_OFFSET, compute_checksum(data))


class DiskManager:
    def __init__(self, path, page_size=None):
        self.path = path
        self._lock = threading.RLock()
        self.reads = 0
        self.writes = 0
        existing = os.path.exists(path) and os.path.getsize(path) > 0
        if existing:
            self._file = open(path, "r+b")
            try:
                self._load_header(page_size)
            except Exception:
                self._file.close()
                raise
        else:
            size = DEFAULT_PAGE_SIZE if page_size is None else page_size
            if not is_valid_page_size(size):
                raise FileFormatError("Недопустимый размер страницы: %d" % size)
            self._file = open(path, "w+b")
            self.page_size = size
            self.page_count = 1
            self.free_head = INVALID_PAGE
            self.catalog_meta = INVALID_PAGE
            self._write_header()

    def _load_header(self, expected_page_size):
        raw = self._file.read(COMMON_HEADER_SIZE + HEADER.size)
        if len(raw) < COMMON_HEADER_SIZE + HEADER.size:
            raise FileFormatError("Файл базы данных слишком короткий")
        magic, version, page_size, page_count, free_head, catalog_meta = HEADER.unpack_from(raw, COMMON_HEADER_SIZE)
        if magic != MAGIC:
            raise FileFormatError("Это не файл базы KamronSQL")
        if version != VERSION:
            raise FileFormatError("Неподдерживаемая версия формата: %d" % version)
        if not is_valid_page_size(page_size):
            raise FileFormatError("Повреждённый заголовок: размер страницы %d" % page_size)
        if expected_page_size is not None and expected_page_size != page_size:
            raise FileFormatError("Размер страницы файла %d не совпадает с запрошенным %d" % (page_size, expected_page_size))
        self.page_size = page_size
        self.page_count = page_count
        self.free_head = free_head
        self.catalog_meta = catalog_meta
        header = self._read_raw(0)
        self._verify(0, header)
        expected_size = page_count * page_size
        if os.path.getsize(self.path) < expected_size:
            raise FileFormatError("Файл короче, чем записано в заголовке")

    def _read_raw(self, page_id):
        self._file.seek(page_id * self.page_size)
        data = self._file.read(self.page_size)
        if len(data) != self.page_size:
            raise CorruptPageError("Страница %d прочитана не полностью" % page_id)
        return bytearray(data)

    def _verify(self, page_id, data):
        stored = struct.unpack_from("<I", data, CHECKSUM_OFFSET)[0]
        if stored != compute_checksum(data):
            raise CorruptPageError("Страница %d повреждена: не сходится контрольная сумма" % page_id)

    def _write_header(self):
        data = bytearray(self.page_size)
        set_page_type(data, PAGE_HEADER)
        HEADER.pack_into(
            data, COMMON_HEADER_SIZE, MAGIC, VERSION, self.page_size,
            self.page_count, self.free_head, self.catalog_meta,
        )
        self._write_raw(0, data)

    def _write_raw(self, page_id, data):
        stamp_checksum(data)
        self._file.seek(page_id * self.page_size)
        self._file.write(data)
        self.writes += 1

    def _check_id(self, page_id):
        if not 0 <= page_id < self.page_count:
            raise InvalidPageError("Нет страницы с номером %d" % page_id)

    def read_page(self, page_id):
        with self._lock:
            self._check_id(page_id)
            data = self._read_raw(page_id)
            self._verify(page_id, data)
            self.reads += 1
            return data

    def write_page(self, page_id, data):
        with self._lock:
            self._check_id(page_id)
            if len(data) != self.page_size:
                raise InvalidPageError("Размер буфера %d не равен размеру страницы %d" % (len(data), self.page_size))
            if page_id == 0:
                raise InvalidPageError("Страница 0 служебная")
            self._write_raw(page_id, data)

    def allocate_page(self):
        with self._lock:
            if self.free_head != INVALID_PAGE:
                page_id = self.free_head
                data = self.read_page(page_id)
                if get_page_type(data) != PAGE_FREE:
                    raise CorruptPageError("Список свободных страниц повреждён на странице %d" % page_id)
                self.free_head = FREE_NEXT.unpack_from(data, COMMON_HEADER_SIZE)[0]
            else:
                page_id = self.page_count
                self.page_count += 1
            reserved = bytearray(self.page_size)
            set_page_type(reserved, PAGE_RESERVED)
            self._write_raw(page_id, reserved)
            self._write_header()
            return page_id

    def deallocate_page(self, page_id):
        with self._lock:
            self._check_id(page_id)
            if page_id == 0:
                raise InvalidPageError("Страницу 0 нельзя освободить")
            current = self.read_page(page_id)
            if get_page_type(current) == PAGE_FREE:
                raise InvalidPageError("Страница %d уже свободна" % page_id)
            data = bytearray(self.page_size)
            set_page_type(data, PAGE_FREE)
            FREE_NEXT.pack_into(data, COMMON_HEADER_SIZE, self.free_head)
            self._write_raw(page_id, data)
            self.free_head = page_id
            self._write_header()

    def set_catalog_meta(self, page_id):
        with self._lock:
            self.catalog_meta = page_id
            self._write_header()

    def free_page_count(self):
        with self._lock:
            count = 0
            current = self.free_head
            while current != INVALID_PAGE:
                data = self.read_page(current)
                current = FREE_NEXT.unpack_from(data, COMMON_HEADER_SIZE)[0]
                count += 1
            return count

    def sync(self):
        with self._lock:
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self):
        with self._lock:
            if self._file.closed:
                return
            self.sync()
            self._file.close()
