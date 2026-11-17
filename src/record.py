import json
import struct
from typing import NamedTuple

INT = "INT"
FLOAT = "FLOAT"
TEXT = "TEXT"
TYPES = (INT, FLOAT, TEXT)
INT_MIN = -(2 ** 63)
INT_MAX = 2 ** 63 - 1

INT_CODEC = struct.Struct("<q")
FLOAT_CODEC = struct.Struct("<d")
LENGTH_CODEC = struct.Struct("<I")


class SchemaError(ValueError):
    pass


class Column(NamedTuple):
    name: str
    type: str


def convert_value(column_type, value):
    if column_type == INT:
        if isinstance(value, bool):
            raise SchemaError("Значение %r не является целым числом" % value)
        if isinstance(value, int):
            result = value
        elif isinstance(value, str):
            try:
                result = int(value.strip())
            except ValueError:
                raise SchemaError("Значение %r не является целым числом" % value)
        else:
            raise SchemaError("Значение %r не является целым числом" % (value,))
        if not INT_MIN <= result <= INT_MAX:
            raise SchemaError("Число %d не помещается в 64 бита" % result)
        return result
    if column_type == FLOAT:
        if isinstance(value, bool):
            raise SchemaError("Значение %r не является числом" % value)
        try:
            return float(value)
        except (TypeError, ValueError):
            raise SchemaError("Значение %r не является числом" % (value,))
    return value if isinstance(value, str) else str(value)


class Schema:
    def __init__(self, columns):
        columns = [Column(*column) for column in columns]
        if not columns:
            raise SchemaError("Нужен хотя бы один столбец")
        names = [column.name for column in columns]
        for name in names:
            if not name or not name.isidentifier():
                raise SchemaError("Недопустимое имя столбца: %r" % name)
        if len(set(names)) != len(names):
            raise SchemaError("Имена столбцов повторяются")
        for column in columns:
            if column.type not in TYPES:
                raise SchemaError("Неизвестный тип столбца: %s" % column.type)
        if columns[0].type != INT:
            raise SchemaError("Первый столбец (ключ) должен иметь тип INT")
        self.columns = columns
        self.names = names
        self._positions = {name: index for index, name in enumerate(names)}

    @classmethod
    def parse(cls, specs):
        columns = []
        for index, spec in enumerate(specs):
            name, _, declared = spec.partition(":")
            column_type = declared.upper() if declared else (INT if index == 0 else TEXT)
            columns.append(Column(name, column_type))
        return cls(columns)

    def __len__(self):
        return len(self.columns)

    def index_of(self, name):
        return self._positions[name]

    def has_column(self, name):
        return name in self._positions

    def coerce(self, values):
        if len(values) != len(self.columns):
            raise SchemaError("Неверное количество значений")
        return [convert_value(column.type, value) for column, value in zip(self.columns, values)]

    def encode(self, values):
        parts = []
        for column, value in zip(self.columns, values):
            if column.type == INT:
                parts.append(INT_CODEC.pack(value))
            elif column.type == FLOAT:
                parts.append(FLOAT_CODEC.pack(value))
            else:
                encoded = value.encode("utf-8")
                parts.append(LENGTH_CODEC.pack(len(encoded)))
                parts.append(encoded)
        return b"".join(parts)

    def decode(self, raw):
        values = []
        offset = 0
        for column in self.columns:
            if column.type == INT:
                values.append(INT_CODEC.unpack_from(raw, offset)[0])
                offset += INT_CODEC.size
            elif column.type == FLOAT:
                values.append(FLOAT_CODEC.unpack_from(raw, offset)[0])
                offset += FLOAT_CODEC.size
            else:
                length = LENGTH_CODEC.unpack_from(raw, offset)[0]
                offset += LENGTH_CODEC.size
                values.append(raw[offset:offset + length].decode("utf-8"))
                offset += length
        return values

    def to_json(self):
        return [[column.name, column.type] for column in self.columns]

    @classmethod
    def from_json(cls, data):
        return cls([Column(name, column_type) for name, column_type in data])


def format_value(value):
    return str(value)
