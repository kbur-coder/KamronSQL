from typing import NamedTuple

from .record import SchemaError, convert_value
from .storage.errors import DuplicateKeyError

OPERATORS = ("=", "!=", "<", "<=", ">", ">=")


class UnknownColumnError(SchemaError):
    pass


class Condition(NamedTuple):
    column: str
    op: str
    value: object


def tighten_low(current, current_inclusive, value, inclusive):
    if current is None or value > current or (value == current and not inclusive):
        return value, inclusive
    return current, current_inclusive


def tighten_high(current, current_inclusive, value, inclusive):
    if current is None or value < current or (value == current and not inclusive):
        return value, inclusive
    return current, current_inclusive


def compare(left, op, right):
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


class Plan:
    def __init__(self, kind, key_name, low=None, high=None, low_inclusive=True, high_inclusive=True, residual=()):
        self.kind = kind
        self.key_name = key_name
        self.low = low
        self.high = high
        self.low_inclusive = low_inclusive
        self.high_inclusive = high_inclusive
        self.residual = list(residual)

    def describe(self):
        if self.kind == "empty":
            text = "EMPTY RESULT (contradicting conditions on %s)" % self.key_name
        elif self.kind == "point":
            text = "INDEX POINT LOOKUP on %s = %s" % (self.key_name, self.low)
        elif self.kind == "range":
            low = "-inf" if self.low is None else str(self.low)
            high = "+inf" if self.high is None else str(self.high)
            text = "INDEX RANGE SCAN on %s %s%s, %s%s" % (
                self.key_name,
                "[" if self.low_inclusive and self.low is not None else "(",
                low,
                high,
                "]" if self.high_inclusive and self.high is not None else ")",
            )
        else:
            text = "FULL TABLE SCAN"
        if self.residual:
            text += " + FILTER on %d condition(s)" % len(self.residual)
        return text


class Table:
    def __init__(self, name, schema, heap, index):
        self.name = name
        self.schema = schema
        self.heap = heap
        self.index = index

    def __len__(self):
        return len(self.index)

    def insert(self, values):
        row = self.schema.coerce(values)
        key = row[0]
        if self.index.search(key) is not None:
            raise DuplicateKeyError("Запись с id %d уже существует" % key)
        rid = self.heap.insert(self.schema.encode(row))
        try:
            self.index.insert(key, rid)
        except Exception:
            self.heap.delete(rid)
            raise
        return rid

    def get(self, key):
        rid = self.index.search(convert_value("INT", key))
        if rid is None:
            return None
        raw = self.heap.get(rid)
        return None if raw is None else self.schema.decode(raw)

    def plan(self, conditions=None):
        typed = []
        for condition in conditions or []:
            if condition.op not in OPERATORS:
                raise SchemaError("Неизвестный оператор: %s" % condition.op)
            if not self.schema.has_column(condition.column):
                raise UnknownColumnError(condition.column)
            position = self.schema.index_of(condition.column)
            value = convert_value(self.schema.columns[position].type, condition.value)
            typed.append((position, condition.op, value))
        low = high = None
        low_inclusive = high_inclusive = True
        residual = []
        for position, op, value in typed:
            if position == 0 and op != "!=":
                if op in ("=", ">", ">="):
                    low, low_inclusive = tighten_low(low, low_inclusive, value, op != ">")
                if op in ("=", "<", "<="):
                    high, high_inclusive = tighten_high(high, high_inclusive, value, op != "<")
            else:
                residual.append((position, op, value))
        key_name = self.schema.names[0]
        if low is not None and high is not None:
            if low > high or (low == high and not (low_inclusive and high_inclusive)):
                return Plan("empty", key_name)
            if low == high:
                return Plan("point", key_name, low, high, True, True, residual)
        if low is not None or high is not None:
            return Plan("range", key_name, low, high, low_inclusive, high_inclusive, residual)
        return Plan("scan", key_name, residual=residual)

    @staticmethod
    def _matches(row, residual):
        return all(compare(row[position], op, value) for position, op, value in residual)

    def _read(self, rid):
        raw = self.heap.get(rid)
        return None if raw is None else self.schema.decode(raw)

    def _iterate(self, plan):
        if plan.kind == "empty":
            return
        if plan.kind == "point":
            rid = self.index.search(plan.low)
            if rid is not None:
                row = self._read(rid)
                if row is not None and self._matches(row, plan.residual):
                    yield rid, row
        elif plan.kind == "range":
            for _, rid in self.index.range(plan.low, plan.high, plan.low_inclusive, plan.high_inclusive):
                row = self._read(rid)
                if row is not None and self._matches(row, plan.residual):
                    yield rid, row
        else:
            for rid, raw in self.heap.scan():
                row = self.schema.decode(raw)
                if self._matches(row, plan.residual):
                    yield rid, row

    def select(self, conditions=None):
        plan = self.plan(conditions)
        return [row for _, row in self._iterate(plan)], plan

    def delete_where(self, conditions=None):
        plan = self.plan(conditions)
        targets = [(row[0], rid) for rid, row in self._iterate(plan)]
        for key, rid in targets:
            self.heap.delete(rid)
            self.index.delete(key)
        return len(targets)

    def update(self, key, column, value):
        if not self.schema.has_column(column):
            raise UnknownColumnError(column)
        key = convert_value("INT", key)
        rid = self.index.search(key)
        if rid is None:
            return False
        row = self._read(rid)
        position = self.schema.index_of(column)
        new_value = convert_value(self.schema.columns[position].type, value)
        if position == 0:
            if new_value == key:
                return True
            if self.index.search(new_value) is not None:
                raise DuplicateKeyError("Запись с id %d уже существует" % new_value)
            row[0] = new_value
            new_rid = self.heap.update(rid, self.schema.encode(row))
            self.index.delete(key)
            self.index.insert(new_value, new_rid)
            return True
        row[position] = new_value
        new_rid = self.heap.update(rid, self.schema.encode(row))
        if new_rid != rid:
            self.index.update(key, new_rid)
        return True

    def drop(self):
        self.index.destroy()
        self.heap.destroy()
