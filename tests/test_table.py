import os
import random
import tempfile
import unittest

from src.record import Schema, SchemaError, convert_value
from src.storage import DuplicateKeyError, StorageEngine
from src.table import Condition, Table, UnknownColumnError


class TableTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.engine = StorageEngine(os.path.join(self.dir.name, "t.db"), pool_size=32)
        self.addCleanup(self.engine.close)
        self.table = Table(
            "people",
            Schema.parse(["id", "name", "age:int", "score:float"]),
            self.engine.new_heap(),
            self.engine.new_index(4, 4),
        )

    def fill(self, count=100, seed=1):
        rng = random.Random(seed)
        keys = rng.sample(range(1, 1000), count)
        rows = {}
        for key in keys:
            row = [key, "name%d" % (key % 7), key % 40, key / 10]
            rows[key] = row
            self.table.insert(row)
        return rows

    def select(self, *conditions):
        rows, plan = self.table.select(list(conditions))
        return rows, plan

    def test_insert_and_get(self):
        self.table.insert(["1", "Ann", "30", "1.5"])
        self.assertEqual(self.table.get(1), [1, "Ann", 30, 1.5])
        self.assertEqual(self.table.get("1"), [1, "Ann", 30, 1.5])
        self.assertIsNone(self.table.get(2))
        self.assertEqual(len(self.table), 1)

    def test_duplicate_key_is_rejected_and_nothing_is_stored(self):
        self.table.insert([1, "a", 1, 1.0])
        with self.assertRaises(DuplicateKeyError):
            self.table.insert([1, "b", 2, 2.0])
        self.assertEqual(len(self.table), 1)
        self.assertEqual(len(self.table.heap), 1)
        self.assertEqual(self.table.get(1)[1], "a")

    def test_invalid_rows_are_rejected(self):
        with self.assertRaises(SchemaError):
            self.table.insert([1, "a"])
        with self.assertRaises(SchemaError):
            self.table.insert(["x", "a", 1, 1.0])
        self.assertEqual(len(self.table), 0)

    def test_plans(self):
        self.fill()
        cases = [
            ([Condition("id", "=", 5)], "point"),
            ([Condition("id", ">=", 5), Condition("id", "<=", 50)], "range"),
            ([Condition("id", ">", 5)], "range"),
            ([Condition("id", "<", 5)], "range"),
            ([Condition("name", "=", "x")], "scan"),
            ([Condition("id", "!=", 5)], "scan"),
            ([], "scan"),
            ([Condition("id", ">", 10), Condition("id", "<", 5)], "empty"),
            ([Condition("id", ">", 5), Condition("id", "<=", 5)], "empty"),
            ([Condition("id", "=", 5), Condition("id", "=", 6)], "empty"),
            ([Condition("id", ">=", 5), Condition("id", "<=", 5)], "point"),
        ]
        for conditions, kind in cases:
            self.assertEqual(self.table.plan(conditions).kind, kind, conditions)

    def test_plan_descriptions(self):
        self.fill(10)
        self.assertIn("INDEX POINT LOOKUP", self.table.plan([Condition("id", "=", 3)]).describe())
        text = self.table.plan([Condition("id", ">=", 3), Condition("id", "<", 9)]).describe()
        self.assertIn("INDEX RANGE SCAN", text)
        self.assertIn("[3", text)
        self.assertIn("9)", text)
        self.assertEqual(self.table.plan([]).describe(), "FULL TABLE SCAN")
        mixed = self.table.plan([Condition("id", ">", 3), Condition("age", "<", 10)]).describe()
        self.assertIn("FILTER", mixed)

    def test_results_match_brute_force(self):
        rows = self.fill(200)
        rng = random.Random(4)
        operators = ["=", "!=", "<", "<=", ">", ">="]
        for _ in range(250):
            conditions = []
            for _ in range(rng.randint(0, 3)):
                if rng.random() < 0.5:
                    conditions.append(Condition("id", rng.choice(operators), rng.randrange(0, 1000)))
                else:
                    column = rng.choice(["age", "name", "score"])
                    value = {"age": rng.randrange(40), "name": "name%d" % rng.randrange(7), "score": rng.randrange(100)}[column]
                    conditions.append(Condition(column, rng.choice(operators), value))
            selected, _ = self.table.select(conditions)
            expected = [row for key, row in sorted(rows.items()) if all(self.holds(row, c) for c in conditions)]
            if self.table.plan(conditions).kind == "scan":
                selected = sorted(selected)
            self.assertEqual(selected, expected, conditions)

    def holds(self, row, condition):
        position = self.table.schema.index_of(condition.column)
        left = row[position]
        right = convert_value(self.table.schema.columns[position].type, condition.value)
        return {"=": left == right, "!=": left != right, "<": left < right, "<=": left <= right, ">": left > right, ">=": left >= right}[condition.op]

    def test_index_range_results_come_in_key_order(self):
        self.fill(150)
        rows, plan = self.select(Condition("id", ">=", 100), Condition("id", "<", 600))
        self.assertEqual(plan.kind, "range")
        keys = [row[0] for row in rows]
        self.assertEqual(keys, sorted(keys))

    def test_unknown_column_and_operator(self):
        with self.assertRaises(UnknownColumnError):
            self.table.plan([Condition("nope", "=", 1)])
        with self.assertRaises(SchemaError):
            self.table.plan([Condition("id", "~", 1)])
        with self.assertRaises(SchemaError):
            self.table.plan([Condition("id", "=", "abc")])

    def test_delete_where(self):
        rows = self.fill(120)
        removed = self.table.delete_where([Condition("id", ">=", 300), Condition("id", "<", 700)])
        gone = [k for k in rows if 300 <= k < 700]
        self.assertEqual(removed, len(gone))
        self.assertEqual(len(self.table), len(rows) - len(gone))
        self.assertEqual(len(self.table.heap), len(self.table))
        for key in rows:
            self.assertEqual(self.table.get(key), None if key in gone else rows[key])
        self.table.index.check()

    def test_delete_with_a_residual_filter_and_full_scan(self):
        rows = self.fill(100)
        removed = self.table.delete_where([Condition("name", "=", "name3")])
        self.assertEqual(removed, sum(1 for r in rows.values() if r[1] == "name3"))
        self.assertEqual(self.table.delete_where([Condition("id", "=", 123456)]), 0)
        self.assertEqual(self.table.delete_where([]), len(rows) - removed)
        self.assertEqual(len(self.table), 0)

    def test_update_regular_column(self):
        self.fill(30)
        key = next(iter(self.table.index.items()))[0]
        self.assertTrue(self.table.update(key, "name", "changed"))
        self.assertEqual(self.table.get(key)[1], "changed")
        self.assertFalse(self.table.update(999999, "name", "x"))

    def test_update_that_moves_the_row_keeps_the_index_correct(self):
        for key in range(1, 9):
            self.table.insert([key, "x" * 900, key, 0.0])
        before = self.table.index.search(1)
        self.assertTrue(self.table.update(1, "name", "y" * 3500))
        after = self.table.index.search(1)
        self.assertNotEqual(before, after)
        self.assertEqual(self.table.get(1)[1], "y" * 3500)
        self.assertEqual(len(self.table.heap), 8)
        for key in range(2, 9):
            self.assertEqual(self.table.get(key)[1], "x" * 900)

    def test_update_primary_key(self):
        self.fill(20)
        key = next(iter(self.table.index.items()))[0]
        free = 5000
        self.assertTrue(self.table.update(key, "id", free))
        self.assertIsNone(self.table.get(key))
        self.assertEqual(self.table.get(free)[0], free)
        other = [k for k, _ in self.table.index.items() if k not in (free,)][0]
        with self.assertRaises(DuplicateKeyError):
            self.table.update(free, "id", other)
        self.assertTrue(self.table.update(free, "id", free))
        self.table.index.check()

    def test_update_unknown_column_and_bad_value(self):
        self.fill(5)
        key = next(iter(self.table.index.items()))[0]
        with self.assertRaises(UnknownColumnError):
            self.table.update(key, "nope", 1)
        with self.assertRaises(SchemaError):
            self.table.update(key, "age", "old")

    def test_drop_releases_pages(self):
        self.fill(80)
        self.table.drop()
        disk = self.engine.disk
        catalog_pages = len(self.engine._catalog_heap.page_ids()) + 1
        self.assertEqual(disk.page_count - 1 - disk.free_page_count(), catalog_pages)


if __name__ == "__main__":
    unittest.main()
