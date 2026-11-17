import os
import random
import tempfile
import unittest

from src.database import Database
from src.table import Condition


class StressTests(unittest.TestCase):
    def test_many_rows_with_a_tiny_buffer_pool(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "stress.db")
            database = Database(path, pool_size=24, policy="lru-k")
            table = database.create_table("items", ["id", "label", "weight:float"])
            rng = random.Random(2024)
            keys = rng.sample(range(1, 200000), 6000)
            model = {}
            for key in keys:
                row = [key, "label-%d" % (key % 97) * rng.randint(1, 6), key / 7]
                table.insert(row)
                model[key] = row
            self.assertGreater(database.engine.pool.evictions, 300)
            for key in rng.sample(keys, 2500):
                self.assertEqual(table.delete_where([Condition("id", "=", key)]), 1)
                del model[key]
            for key in rng.sample(sorted(model), 800):
                table.update(key, "label", "updated-%d" % key * rng.randint(1, 20))
                model[key][1] = "updated-%d" % key * 1
            table.index.check()
            self.assertEqual(len(table), len(model))
            self.assertEqual(len(table.heap), len(model))
            self.assertEqual(database.engine.pool.pinned_pages(), 0)
            database.close()

            database = Database(path, pool_size=16)
            table = database.table("items")
            table.index.check()
            self.assertEqual(len(table), len(model))
            for key in rng.sample(sorted(model), 300):
                self.assertEqual(table.get(key)[0], key)
            for _ in range(50):
                low, high = sorted(rng.sample(range(1, 200000), 2))
                rows, plan = table.select([Condition("id", ">=", low), Condition("id", "<=", high)])
                self.assertEqual(plan.kind, "range")
                self.assertEqual([row[0] for row in rows], [k for k in sorted(model) if low <= k <= high])
            self.assertEqual(database.engine.pool.pinned_pages(), 0)
            database.close()


if __name__ == "__main__":
    unittest.main()
