import json
import os

from src.record import Column, Schema, SchemaError, format_value
from src.storage import DuplicateKeyError, StorageEngine, TupleTooLargeError
from src.table import Condition, Table, UnknownColumnError

DEFAULT_PATH = os.path.join("data", "kamronsql.db")


class DatabaseError(Exception):
    pass


class TableExistsError(DatabaseError):
    pass


class TableNotFoundError(DatabaseError):
    pass


class Database:
    def __init__(self, path, **engine_options):
        self.engine = StorageEngine(path, **engine_options)
        self.tables = {}
        for name in self.engine.catalog_names():
            entry = json.loads(self.engine.catalog_get(name).decode("utf-8"))
            self.tables[name] = Table(
                name,
                Schema.from_json(entry["columns"]),
                self.engine.open_heap(entry["heap"]),
                self.engine.open_index(entry["index"]),
            )

    def create_table(self, name, specs):
        if not name or not name.isidentifier():
            raise DatabaseError("Недопустимое имя таблицы")
        if name in self.tables:
            raise TableExistsError(name)
        schema = Schema.parse(specs)
        heap = self.engine.new_heap()
        index = self.engine.new_index()
        entry = {"columns": schema.to_json(), "heap": heap.meta_page_id, "index": index.meta_page_id}
        self.engine.catalog_put(name, json.dumps(entry).encode("utf-8"))
        table = Table(name, schema, heap, index)
        self.tables[name] = table
        return table

    def table(self, name):
        table = self.tables.get(name)
        if table is None:
            raise TableNotFoundError(name)
        return table

    def drop_table(self, name):
        table = self.table(name)
        table.drop()
        self.engine.catalog_delete(name)
        del self.tables[name]

    def table_names(self):
        return sorted(self.tables)

    def stats(self):
        return self.engine.stats()

    def checkpoint(self):
        return self.engine.checkpoint()

    def close(self):
        self.engine.close()


_database = None


def open_database(path=DEFAULT_PATH, **options):
    global _database
    close_database()
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    options.setdefault("pool_size", 256)
    _database = Database(path, **options)
    return _database


def get_database():
    if _database is None:
        open_database(flush_interval=1.0)
    return _database


def close_database():
    global _database
    if _database is not None:
        _database.close()
        _database = None


def to_conditions(where, table=None):
    if where is None:
        return []
    if isinstance(where, dict):
        return [Condition(column, "=", value) for column, value in where.items()]
    if isinstance(where, (list, tuple)) and all(isinstance(item, Condition) for item in where):
        return list(where)
    if table is None:
        raise SchemaError("Не удалось разобрать условие")
    return [Condition(table.schema.names[0], "=", where)]


def find_table(table_name):
    try:
        return get_database().table(table_name)
    except TableNotFoundError:
        print("Таблицы не существует")
        return None


def create_table(table_name, columns):
    try:
        get_database().create_table(table_name, list(columns))
    except TableExistsError:
        print("Таблица уже существует")
        return False
    except (DatabaseError, SchemaError) as error:
        print(error)
        return False
    return True


def drop_table(table_name):
    try:
        get_database().drop_table(table_name)
    except TableNotFoundError:
        print("Таблицы не существует")
        return False
    return True


def insert_into_table(table_name, values):
    table = find_table(table_name)
    if table is None:
        return False
    try:
        table.insert(list(values))
    except (SchemaError, DuplicateKeyError, TupleTooLargeError) as error:
        print(error)
        return False
    return True


def select_from_table(table_name, columns=None, where=None):
    table = find_table(table_name)
    if table is None:
        return None
    try:
        if columns is None or columns == "*":
            positions = list(range(len(table.schema)))
        else:
            positions = []
            for column in columns:
                if not table.schema.has_column(column):
                    print("Нет такого столбца")
                    return None
                positions.append(table.schema.index_of(column))
        rows, _ = table.select(to_conditions(where, table))
    except UnknownColumnError:
        print("Нет такого столбца")
        return None
    except SchemaError as error:
        print(error)
        return None
    result = [[table.schema.names[position] for position in positions]]
    for row in rows:
        result.append([format_value(row[position]) for position in positions])
    return result


def explain_select(table_name, where=None):
    table = find_table(table_name)
    if table is None:
        return None
    try:
        return table.plan(to_conditions(where, table)).describe()
    except UnknownColumnError:
        print("Нет такого столбца")
    except SchemaError as error:
        print(error)
    return None


def delete_from_table(table_name, where=None):
    table = find_table(table_name)
    if table is None:
        return 0
    try:
        return table.delete_where(to_conditions(where, table))
    except UnknownColumnError:
        print("Нет такого столбца")
    except SchemaError as error:
        print(error)
    return 0


def update_table(table_name, record_id, column_name, new_value):
    table = find_table(table_name)
    if table is None:
        return False
    try:
        updated = table.update(record_id, column_name, new_value)
    except UnknownColumnError:
        print("Нет такого столбца")
        return False
    except (SchemaError, DuplicateKeyError, TupleTooLargeError) as error:
        print(error)
        return False
    if not updated:
        print("Запись не найдена")
    return updated


def list_tables():
    return get_database().table_names()


def engine_stats():
    return get_database().stats()


def checkpoint():
    return get_database().checkpoint()
