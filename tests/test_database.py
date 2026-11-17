import contextlib
import io
import os
import tempfile
import unittest

from src import database


def silent(function, *args, **kwargs):
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = function(*args, **kwargs)
    return result, buffer.getvalue()


class DatabaseApiTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "db", "k.db")
        database.open_database(self.path, pool_size=32)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(database.close_database)

    def seed(self):
        database.create_table("users", ["id", "name", "age"])
        for i in range(1, 11):
            database.insert_into_table("users", [i, "user%d" % i, 20 + i])

    def test_create_insert_select(self):
        self.seed()
        table = database.select_from_table("users")
        self.assertEqual(table[0], ["id", "name", "age"])
        self.assertEqual(len(table), 11)
        self.assertEqual(table[1], ["1", "user1", "21"])

    def test_creating_an_existing_table_prints_a_message(self):
        database.create_table("t", ["id"])
        result, output = silent(database.create_table, "t", ["id"])
        self.assertFalse(result)
        self.assertIn("уже существует", output)

    def test_missing_table_messages(self):
        for call in (
            lambda: database.select_from_table("nope"),
            lambda: database.insert_into_table("nope", [1]),
            lambda: database.delete_from_table("nope", 1),
            lambda: database.update_table("nope", 1, "a", "b"),
            lambda: database.drop_table("nope"),
        ):
            _, output = silent(call)
            self.assertIn("Таблицы не существует", output)

    def test_wrong_value_count_and_duplicates(self):
        self.seed()
        result, output = silent(database.insert_into_table, "users", [1, "x"])
        self.assertFalse(result)
        self.assertIn("Неверное количество значений", output)
        result, output = silent(database.insert_into_table, "users", [1, "x", 5])
        self.assertFalse(result)
        self.assertIn("уже существует", output)
        self.assertEqual(len(database.select_from_table("users")), 11)

    def test_select_with_a_dictionary_where_and_columns(self):
        self.seed()
        self.assertEqual(database.select_from_table("users", ["name"], {"id": "3"}), [["name"], ["user3"]])
        self.assertEqual(database.select_from_table("users", None, {"name": "user4"})[1][0], "4")
        _, output = silent(database.select_from_table, "users", ["nope"])
        self.assertIn("Нет такого столбца", output)
        _, output = silent(database.select_from_table, "users", None, {"nope": 1})
        self.assertIn("Нет такого столбца", output)

    def test_delete_accepts_an_id_or_a_condition_dictionary(self):
        self.seed()
        self.assertEqual(database.delete_from_table("users", "2"), 1)
        self.assertEqual(database.delete_from_table("users", {"name": "user5"}), 1)
        self.assertEqual(database.delete_from_table("users", "999"), 0)
        self.assertEqual(len(database.select_from_table("users")), 9)

    def test_update(self):
        self.seed()
        self.assertTrue(database.update_table("users", 3, "name", "Zed"))
        self.assertEqual(database.select_from_table("users", ["name"], {"id": 3})[1], ["Zed"])
        result, output = silent(database.update_table, "users", 999, "name", "x")
        self.assertFalse(result)
        self.assertIn("Запись не найдена", output)
        _, output = silent(database.update_table, "users", 3, "nope", "x")
        self.assertIn("Нет такого столбца", output)

    def test_everything_survives_reopening(self):
        self.seed()
        database.delete_from_table("users", 4)
        database.create_table("other", ["id", "title"])
        database.insert_into_table("other", [1, "a"])
        database.open_database(self.path, pool_size=8)
        self.assertEqual(database.list_tables(), ["other", "users"])
        self.assertEqual(len(database.select_from_table("users")), 10)
        self.assertEqual(database.select_from_table("other")[1], ["1", "a"])

    def test_drop_table_releases_its_pages(self):
        self.seed()
        db = database.get_database()
        used = db.engine.disk.page_count - 1 - db.engine.disk.free_page_count()
        database.drop_table("users")
        self.assertEqual(database.list_tables(), [])
        after = db.engine.disk.page_count - 1 - db.engine.disk.free_page_count()
        self.assertLess(after, used)
        database.create_table("users", ["id", "name"])
        self.assertEqual(database.select_from_table("users"), [["id", "name"]])

    def test_explain(self):
        self.seed()
        self.assertIn("INDEX POINT LOOKUP", database.explain_select("users", {"id": 3}))
        self.assertEqual(database.explain_select("users"), "FULL TABLE SCAN")

    def test_invalid_names_and_schemas(self):
        for name, columns in (("bad name", ["id"]), ("", ["id"]), ("ok", ["id:text"]), ("ok", ["id", "id"])):
            result, output = silent(database.create_table, name, columns)
            self.assertFalse(result)
            self.assertTrue(output)

    def test_stats_report_buffer_pool_activity(self):
        self.seed()
        stats = database.engine_stats()
        for key in ("hits", "misses", "hit_ratio", "dirty", "evictions", "page_size"):
            self.assertIn(key, stats)
        self.assertGreater(database.checkpoint(), 0)
        self.assertEqual(database.engine_stats()["dirty"], 0)


if __name__ == "__main__":
    unittest.main()
