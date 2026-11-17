import random
import unittest

from src.storage.slotted_page import SLOT_ARRAY_OFFSET, SLOT_SIZE, SlottedPage


def fresh(size=4096):
    return SlottedPage.format(bytearray(size))


def check_layout(test, page, model):
    live = {}
    for slot, payload in page.slots():
        live[slot] = payload
    test.assertEqual(live, model)
    ranges = sorted(
        (page._slot(slot)[0], page._slot(slot)[0] + page._slot(slot)[1]) for slot in live
    )
    for (start, end), (next_start, _) in zip(ranges, ranges[1:]):
        test.assertLessEqual(end, next_start)
    if ranges:
        test.assertGreaterEqual(ranges[0][0], page.slot_array_end())
        test.assertLessEqual(ranges[-1][1], page.size)
    test.assertEqual(page.live_count, len(model))
    test.assertEqual(page.used_bytes(), sum(len(value) for value in model.values()))
    test.assertEqual(
        page.total_free(), page.size - page.slot_array_end() - sum(len(v) for v in model.values())
    )


class SlottedPageTests(unittest.TestCase):
    def test_empty_page(self):
        page = fresh()
        self.assertEqual(page.num_slots, 0)
        self.assertEqual(page.live_count, 0)
        self.assertEqual(page.tuple_start, 4096)
        self.assertEqual(page.contiguous_free(), 4096 - SLOT_ARRAY_OFFSET)
        self.assertTrue(page.is_empty())

    def test_insert_and_get(self):
        page = fresh()
        slots = [page.insert(b"row-%d" % i) for i in range(5)]
        self.assertEqual(slots, [0, 1, 2, 3, 4])
        self.assertEqual([page.get(s) for s in slots], [b"row-%d" % i for i in range(5)])
        self.assertEqual(page.live_count, 5)

    def test_tuples_grow_from_the_end_of_the_page(self):
        page = fresh()
        page.insert(b"a" * 10)
        page.insert(b"b" * 20)
        self.assertEqual(page.tuple_start, 4096 - 30)
        self.assertEqual(page._slot(0), (4086, 10))
        self.assertEqual(page._slot(1), (4066, 20))
        self.assertEqual(page.slot_array_end(), SLOT_ARRAY_OFFSET + 2 * SLOT_SIZE)

    def test_delete_keeps_other_slot_numbers_stable(self):
        page = fresh()
        for i in range(4):
            page.insert(b"x%d" % i)
        self.assertTrue(page.delete(1))
        self.assertIsNone(page.get(1))
        self.assertEqual(page.get(2), b"x2")
        self.assertFalse(page.delete(1))
        self.assertFalse(page.delete(99))
        self.assertFalse(page.delete(-1))

    def test_freed_slot_is_reused(self):
        page = fresh()
        for i in range(3):
            page.insert(b"x%d" % i)
        page.delete(0)
        self.assertEqual(page.insert(b"new"), 0)
        self.assertEqual(page.get(0), b"new")

    def test_trailing_empty_slots_are_trimmed(self):
        page = fresh()
        for i in range(4):
            page.insert(b"x%d" % i)
        page.delete(3)
        page.delete(2)
        self.assertEqual(page.num_slots, 2)

    def test_page_resets_when_the_last_tuple_is_deleted(self):
        page = fresh()
        page.insert(b"one")
        page.insert(b"two")
        page.delete(0)
        page.delete(1)
        self.assertEqual(page.num_slots, 0)
        self.assertEqual(page.tuple_start, 4096)
        self.assertEqual(page.contiguous_free(), 4096 - SLOT_ARRAY_OFFSET)

    def test_update_in_place_when_shorter_or_equal(self):
        page = fresh()
        slot = page.insert(b"abcdefgh")
        self.assertTrue(page.update(slot, b"xyz"))
        self.assertEqual(page.get(slot), b"xyz")
        self.assertTrue(page.update(slot, b"123"))
        self.assertEqual(page.get(slot), b"123")

    def test_update_to_longer_keeps_the_slot_number(self):
        page = fresh()
        first = page.insert(b"a" * 10)
        second = page.insert(b"b" * 10)
        self.assertTrue(page.update(first, b"c" * 500))
        self.assertEqual(page.get(first), b"c" * 500)
        self.assertEqual(page.get(second), b"b" * 10)

    def test_update_fails_without_changing_the_page_when_it_does_not_fit(self):
        page = fresh(512)
        slot = page.insert(b"a" * 100)
        other = page.insert(b"b" * 300)
        before = bytes(page.data)
        self.assertFalse(page.update(slot, b"z" * 400))
        self.assertEqual(bytes(page.data), before)
        self.assertEqual(page.get(other), b"b" * 300)

    def test_update_of_missing_slot_returns_false(self):
        page = fresh()
        self.assertFalse(page.update(3, b"x"))
        slot = page.insert(b"x")
        page.insert(b"y")
        page.delete(slot)
        self.assertFalse(page.update(slot, b"z"))

    def test_fragmentation_is_solved_by_compaction(self):
        page = fresh()
        slots = []
        while True:
            slot = page.insert(bytes([len(slots) % 250]) * 100)
            if slot is None:
                break
            slots.append(slot)
        self.assertGreater(len(slots), 30)
        for slot in slots[::2]:
            page.delete(slot)
        self.assertLess(page.contiguous_free(), 150)
        self.assertGreater(page.total_free(), 1500)
        big = page.insert(b"B" * 400)
        self.assertIsNotNone(big)
        self.assertEqual(page.get(big), b"B" * 400)
        for index, slot in enumerate(slots):
            if index % 2 and slot != big:
                self.assertEqual(page.get(slot), bytes([index % 250]) * 100)

    def test_insert_returns_none_when_full(self):
        page = fresh(512)
        count = 0
        while page.insert(b"x" * 40) is not None:
            count += 1
        self.assertGreater(count, 5)
        self.assertFalse(page.can_insert(40))

    def test_size_limits(self):
        page = fresh()
        limit = SlottedPage.max_tuple_size(4096)
        slot = page.insert(b"m" * limit)
        self.assertEqual(page.get(slot), b"m" * limit)
        with self.assertRaises(ValueError):
            fresh().insert(b"m" * (limit + 1))
        with self.assertRaises(ValueError):
            fresh().insert(b"")

    def test_neighbour_pointers_are_stored_in_the_page(self):
        page = SlottedPage.format(bytearray(4096), next_page=7, prev_page=3)
        self.assertEqual((page.next_page, page.prev_page), (7, 3))
        page.next_page = 9
        self.assertEqual(SlottedPage(page.data).next_page, 9)

    def test_random_operations_match_a_dictionary(self):
        rng = random.Random(1)
        for size in (512, 4096):
            page = fresh(size)
            model = {}
            for step in range(3000):
                action = rng.random()
                if action < 0.5:
                    payload = bytes([rng.randrange(256)]) * rng.randint(1, 120)
                    slot = page.insert(payload)
                    if slot is not None:
                        self.assertNotIn(slot, model)
                        model[slot] = payload
                elif action < 0.75 and model:
                    slot = rng.choice(list(model))
                    self.assertTrue(page.delete(slot))
                    del model[slot]
                elif model:
                    slot = rng.choice(list(model))
                    payload = bytes([rng.randrange(256)]) * rng.randint(1, 160)
                    if page.update(slot, payload):
                        model[slot] = payload
                if step % 25 == 0:
                    check_layout(self, page, model)
            check_layout(self, page, model)


if __name__ == "__main__":
    unittest.main()
