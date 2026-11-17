import os
import tempfile
import unittest

from src.storage import CorruptPageError, DiskManager, FileFormatError, InvalidPageError


class DiskManagerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "db")
        self.addCleanup(self.dir.cleanup)

    def test_new_file_has_only_the_header_page(self):
        disk = DiskManager(self.path)
        self.assertEqual(disk.page_size, 4096)
        self.assertEqual(disk.page_count, 1)
        disk.close()
        self.assertEqual(os.path.getsize(self.path), 4096)

    def test_invalid_page_size_is_rejected(self):
        for size in (100, 1000, 70000):
            with self.assertRaises(FileFormatError):
                DiskManager(os.path.join(self.dir.name, "x%d" % size), size)

    def test_pages_survive_reopen(self):
        disk = DiskManager(self.path)
        first = disk.allocate_page()
        second = disk.allocate_page()
        data = bytearray(disk.page_size)
        data[8:13] = b"hello"
        disk.write_page(second, data)
        disk.set_catalog_meta(first)
        disk.close()
        disk = DiskManager(self.path)
        self.assertEqual((first, second), (1, 2))
        self.assertEqual(disk.page_count, 3)
        self.assertEqual(disk.catalog_meta, first)
        self.assertEqual(bytes(disk.read_page(second)[8:13]), b"hello")
        disk.close()

    def test_other_page_size_is_rejected_on_open(self):
        DiskManager(self.path, 4096).close()
        with self.assertRaises(FileFormatError):
            DiskManager(self.path, 8192)

    def test_custom_page_size_is_stored_in_the_file(self):
        DiskManager(self.path, 1024).close()
        disk = DiskManager(self.path)
        self.assertEqual(disk.page_size, 1024)
        disk.close()

    def test_flipped_byte_is_detected_by_the_checksum(self):
        disk = DiskManager(self.path)
        page_id = disk.allocate_page()
        data = bytearray(disk.page_size)
        data[100] = 7
        disk.write_page(page_id, data)
        disk.close()
        with open(self.path, "r+b") as handle:
            handle.seek(page_id * 4096 + 100)
            handle.write(b"\xff")
        disk = DiskManager(self.path)
        with self.assertRaises(CorruptPageError):
            disk.read_page(page_id)
        disk.close()

    def test_corrupt_header_is_detected_on_open(self):
        DiskManager(self.path).close()
        with open(self.path, "r+b") as handle:
            handle.seek(20)
            handle.write(b"\xff\xff")
        with self.assertRaises((CorruptPageError, FileFormatError)):
            DiskManager(self.path)

    def test_foreign_file_is_rejected(self):
        with open(self.path, "wb") as handle:
            handle.write(b"this is not a database" * 400)
        with self.assertRaises(FileFormatError):
            DiskManager(self.path)

    def test_freed_pages_are_reused_in_lifo_order(self):
        disk = DiskManager(self.path)
        pages = [disk.allocate_page() for _ in range(4)]
        disk.deallocate_page(pages[1])
        disk.deallocate_page(pages[2])
        self.assertEqual(disk.free_page_count(), 2)
        self.assertEqual(disk.allocate_page(), pages[2])
        self.assertEqual(disk.allocate_page(), pages[1])
        self.assertEqual(disk.free_page_count(), 0)
        self.assertEqual(disk.page_count, 5)
        disk.close()

    def test_free_list_survives_reopen(self):
        disk = DiskManager(self.path)
        pages = [disk.allocate_page() for _ in range(3)]
        disk.deallocate_page(pages[0])
        disk.close()
        disk = DiskManager(self.path)
        self.assertEqual(disk.free_page_count(), 1)
        self.assertEqual(disk.allocate_page(), pages[0])
        disk.close()

    def test_double_free_is_rejected(self):
        disk = DiskManager(self.path)
        page_id = disk.allocate_page()
        disk.deallocate_page(page_id)
        with self.assertRaises(InvalidPageError):
            disk.deallocate_page(page_id)
        disk.close()

    def test_invalid_page_ids_are_rejected(self):
        disk = DiskManager(self.path)
        for page_id in (-5, 1, 99):
            with self.assertRaises(InvalidPageError):
                disk.read_page(page_id)
        with self.assertRaises(InvalidPageError):
            disk.write_page(0, bytearray(4096))
        with self.assertRaises(InvalidPageError):
            disk.deallocate_page(0)
        disk.close()

    def test_wrong_buffer_size_is_rejected(self):
        disk = DiskManager(self.path)
        page_id = disk.allocate_page()
        with self.assertRaises(InvalidPageError):
            disk.write_page(page_id, bytearray(100))
        disk.close()

    def test_counters_grow_with_io(self):
        disk = DiskManager(self.path)
        page_id = disk.allocate_page()
        reads, writes = disk.reads, disk.writes
        disk.read_page(page_id)
        disk.write_page(page_id, bytearray(4096))
        self.assertEqual((disk.reads, disk.writes), (reads + 1, writes + 1))
        disk.close()


if __name__ == "__main__":
    unittest.main()
