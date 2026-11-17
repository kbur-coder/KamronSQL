import unittest

from src.record import Column, Schema, SchemaError, convert_value


class SchemaTests(unittest.TestCase):
    def test_parse_defaults_and_declared_types(self):
        schema = Schema.parse(["id", "name", "price:float", "age:int"])
        self.assertEqual([c.type for c in schema.columns], ["INT", "TEXT", "FLOAT", "INT"])
        self.assertEqual(schema.names, ["id", "name", "price", "age"])
        self.assertEqual(schema.index_of("price"), 2)

    def test_first_column_must_be_an_integer(self):
        with self.assertRaises(SchemaError):
            Schema.parse(["id:text", "name"])
        with self.assertRaises(SchemaError):
            Schema.parse(["id:float"])

    def test_invalid_definitions(self):
        for specs in ([], ["id", "id"], ["id", "bad name"], ["id", "x:blob"], ["", "a"]):
            with self.assertRaises(SchemaError):
                Schema.parse(specs)

    def test_conversion_from_strings(self):
        schema = Schema.parse(["id", "name", "price:float"])
        self.assertEqual(schema.coerce(["7", 42, "3.5"]), [7, "42", 3.5])

    def test_conversion_errors(self):
        schema = Schema.parse(["id", "name"])
        with self.assertRaises(SchemaError):
            schema.coerce(["abc", "x"])
        with self.assertRaises(SchemaError):
            schema.coerce(["1"])
        with self.assertRaises(SchemaError):
            schema.coerce(["99999999999999999999", "x"])
        with self.assertRaises(SchemaError):
            schema.coerce(["1.5", "x"])
        with self.assertRaises(SchemaError):
            convert_value("INT", True)
        with self.assertRaises(SchemaError):
            convert_value("FLOAT", "abc")

    def test_round_trip(self):
        schema = Schema.parse(["id", "name", "price:float", "note"])
        for row in (
            [1, "Aziz", 2.5, ""],
            [-(2 ** 63), "Юникод ✓", -0.125, "x" * 1000],
            [2 ** 63 - 1, "", 1e300, "line\nbreak"],
        ):
            self.assertEqual(schema.decode(schema.encode(row)), row)

    def test_json_round_trip(self):
        schema = Schema.parse(["id", "name:text", "score:float"])
        restored = Schema.from_json(schema.to_json())
        self.assertEqual(restored.columns, schema.columns)

    def test_column_tuple(self):
        self.assertEqual(Column("a", "INT"), ("a", "INT"))


if __name__ == "__main__":
    unittest.main()
