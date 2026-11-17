import contextlib
import io
import os
import tempfile
import unittest

from src import database
from src.parser import Parser, parse_conditions
from src.table import Condition


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        database.open_database(os.path.join(self.dir.name, "p.db"), pool_size=32)
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(database.close_database)
        self.parser = Parser()

    def run_command(self, command):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.parser.parse(command)
        return buffer.getvalue().strip().split("\n") if buffer.getvalue().strip() else []

    def seed(self, count=10):
        self.run_command("CREATE TABLE users id name age")
        for i in range(1, count + 1):
            self.run_command("INSERT users %d user%d %d" % (i, i, 20 + i))

    def test_create_insert_select(self):
        self.assertEqual(self.run_command("CREATE TABLE users id name age"), ["Table users created"])
        self.assertEqual(self.run_command("INSERT users 1 Ann 30"), ["1 row inserted"])
        self.assertEqual(self.run_command("SELECT * FROM users"), ["id | name | age", "1 | Ann | 30", "(1 rows)"])
        self.assertEqual(self.run_command("select users"), ["id | name | age", "1 | Ann | 30", "(1 rows)"])

    def test_insert_into_values_and_quotes(self):
        self.run_command("CREATE TABLE people id name")
        self.run_command('INSERT INTO people VALUES 1 "John Smith"')
        self.assertEqual(self.run_command("SELECT name FROM people WHERE id = 1")[1], "John Smith")

    def test_select_columns_and_where(self):
        self.seed()
        self.assertEqual(self.run_command("SELECT name, age FROM users WHERE id = 3"), ["name | age", "user3 | 23", "(1 rows)"])
        self.assertEqual(self.run_command("SELECT id FROM users WHERE id>=9")[1:3], ["9", "10"])
        self.assertEqual(self.run_command("SELECT id FROM users WHERE name = user4")[1], "4")

    def test_between_and_combined_conditions(self):
        self.seed()
        rows = self.run_command("SELECT id FROM users WHERE id BETWEEN 3 AND 6")
        self.assertEqual(rows, ["id", "3", "4", "5", "6", "(4 rows)"])
        rows = self.run_command("SELECT id FROM users WHERE id > 2 AND id < 6 AND age != 24")
        self.assertEqual(rows, ["id", "3", "5", "(2 rows)"])

    def test_explain(self):
        self.seed()
        self.assertIn("INDEX POINT LOOKUP", self.run_command("EXPLAIN SELECT * FROM users WHERE id = 3")[0])
        self.assertIn("INDEX RANGE SCAN", self.run_command("EXPLAIN SELECT * FROM users WHERE id BETWEEN 3 AND 6")[0])
        self.assertEqual(self.run_command("EXPLAIN SELECT * FROM users WHERE name = x"), ["FULL TABLE SCAN + FILTER on 1 condition(s)"])

    def test_delete_forms(self):
        self.seed()
        self.assertEqual(self.run_command("DELETE users 2"), ["1 row(s) deleted"])
        self.assertEqual(self.run_command("DELETE FROM users WHERE id BETWEEN 5 AND 7"), ["3 row(s) deleted"])
        self.assertEqual(self.run_command("DELETE FROM users"), ["Usage: DELETE FROM table_name WHERE ..."])
        self.assertEqual(self.run_command("SELECT id FROM users")[-1], "(6 rows)")

    def test_update(self):
        self.seed(3)
        self.assertEqual(self.run_command("UPDATE users 2 name Zed"), ["1 row updated"])
        self.assertEqual(self.run_command("SELECT name FROM users WHERE id = 2")[1], "Zed")
        self.assertIn("Запись не найдена", self.run_command("UPDATE users 99 name x")[0])

    def test_drop_and_show_tables(self):
        self.seed(2)
        self.assertEqual(self.run_command("SHOW TABLES"), ["users"])
        self.assertEqual(self.run_command("DROP TABLE users"), ["Table users dropped"])
        self.assertEqual(self.run_command("SHOW TABLES"), [])

    def test_usage_and_error_messages(self):
        self.assertEqual(self.run_command("CREATE TABLE users"), ["Usage: CREATE TABLE table_name column1 column2 ..."])
        self.assertEqual(self.run_command("INSERT users"), ["Usage: INSERT table_name value1 value2 ..."])
        self.assertEqual(self.run_command("UPDATE users 1"), ["Usage: UPDATE table_name id column_name new_value"])
        self.assertEqual(self.run_command("FLY away"), ["Unknown command"])
        self.assertEqual(self.run_command(""), [])
        self.assertIn("Таблицы не существует", self.run_command("SELECT * FROM nothing")[0])
        self.seed(2)
        self.assertIn("не является целым числом", self.run_command("SELECT * FROM users WHERE id = abc")[0])
        self.assertIn("Нет такого столбца", self.run_command("SELECT * FROM users WHERE nope = 1")[0])
        self.assertTrue(self.run_command('INSERT users 5 "unclosed'))

    def test_a_damaged_page_is_reported_instead_of_crashing(self):
        self.seed(30)
        first_page = database.get_database().table("users").heap.first_page_id
        path = database.get_database().engine.disk.path
        database.close_database()
        with open(path, "r+b") as handle:
            handle.seek(first_page * 4096 + 200)
            handle.write(b"\xff\xff\xff\xff")
        database.open_database(path, pool_size=32)
        output = self.run_command("SELECT * FROM users")
        self.assertEqual(len(output), 1)
        self.assertIn("Ошибка хранилища", output[0])
        self.assertIn("повреждена", output[0])

    def test_stats_help_and_checkpoint(self):
        self.seed(3)
        stats = self.run_command("STATS")
        self.assertTrue(any(line.startswith("hit_ratio:") for line in stats))
        self.assertTrue(self.run_command("CHECKPOINT")[0].startswith("Flushed"))
        self.assertIn("CREATE TABLE", "\n".join(self.run_command("HELP")))


class ConditionParsingTests(unittest.TestCase):
    def test_operators_without_spaces(self):
        self.assertEqual(parse_conditions("id>=5"), [Condition("id", ">=", "5")])
        self.assertEqual(parse_conditions("id!=5 AND age<3"), [Condition("id", "!=", "5"), Condition("age", "<", "3")])

    def test_between(self):
        self.assertEqual(
            parse_conditions("id BETWEEN 3 AND 9"), [Condition("id", ">=", "3"), Condition("id", "<=", "9")]
        )

    def test_quoted_values(self):
        self.assertEqual(parse_conditions('name = "a b"'), [Condition("name", "=", "a b")])

    def test_malformed_conditions(self):
        for text in ("id", "id =", "id ~ 3", "id = 3 OR id = 4", "id = 3 AND", "id BETWEEN 3 9"):
            with self.assertRaises(ValueError):
                parse_conditions(text)


if __name__ == "__main__":
    unittest.main()
