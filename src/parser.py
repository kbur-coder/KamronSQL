import re
import shlex

from src.database import (
    checkpoint,
    create_table,
    delete_from_table,
    drop_table,
    engine_stats,
    explain_select,
    insert_into_table,
    list_tables,
    select_from_table,
    update_table,
)
from src.storage import StorageError
from src.table import Condition

OPERATORS = {"=", "!=", "<", "<=", ">", ">="}
TOKEN = re.compile(r"\"[^\"]*\"|'[^']*'|<=|>=|!=|=|<|>|[^\s<>=!\"']+")

HELP = """Commands:
  CREATE TABLE name column[:INT|FLOAT|TEXT] ...    first column is the INT primary key
  INSERT name value ...                            or INSERT INTO name VALUES value ...
  SELECT [* | column ...] FROM name [WHERE ...]    or SELECT name
  UPDATE name id column value
  DELETE name id                                   or DELETE FROM name WHERE ...
  DROP TABLE name
  EXPLAIN SELECT ...                               show how the query will be executed
  SHOW TABLES | STATS | CHECKPOINT | HELP | EXIT
WHERE accepts: column = != < <= > >= value, column BETWEEN a AND b, joined with AND."""


def split_where(command):
    parts = re.split(r"\s+WHERE\s+", command.strip(), maxsplit=1, flags=re.IGNORECASE)
    return parts[0], (parts[1] if len(parts) == 2 else None)


def unquote(token):
    if len(token) >= 2 and token[0] in "\"'" and token[-1] == token[0]:
        return token[1:-1]
    return token


def parse_conditions(text):
    tokens = [unquote(token) for token in TOKEN.findall(text)]
    conditions = []
    index = 0
    while index < len(tokens):
        if len(tokens) - index < 3:
            raise ValueError("Не удалось разобрать условие WHERE")
        column = tokens[index]
        operator = tokens[index + 1].upper()
        if operator == "BETWEEN":
            if len(tokens) - index < 5 or tokens[index + 3].upper() != "AND":
                raise ValueError("Ожидалось: column BETWEEN a AND b")
            conditions.append(Condition(column, ">=", tokens[index + 2]))
            conditions.append(Condition(column, "<=", tokens[index + 4]))
            index += 5
        elif operator in OPERATORS:
            conditions.append(Condition(column, operator, tokens[index + 2]))
            index += 3
        else:
            raise ValueError("Неизвестный оператор в WHERE: %s" % tokens[index + 1])
        if index < len(tokens):
            if tokens[index].upper() != "AND":
                raise ValueError("Условия нужно соединять через AND")
            index += 1
            if index >= len(tokens):
                raise ValueError("После AND нет условия")
    return conditions


class Parser:

    def parse(self, command):
        command = command.strip()
        if not command:
            return
        try:
            self._dispatch(command)
        except ValueError as error:
            print(error)
        except StorageError as error:
            print("Ошибка хранилища: %s" % error)

    def _dispatch(self, command):
        head, where_text = split_where(command)
        parts = shlex.split(head)
        if not parts:
            return
        keyword = parts[0].upper()

        if keyword == "CREATE":
            self._create(parts)
        elif keyword == "INSERT":
            self._insert(parts)
        elif keyword == "SELECT":
            self._select(parts, where_text, explain=False)
        elif keyword == "EXPLAIN":
            if len(parts) < 2 or parts[1].upper() != "SELECT":
                print("Usage: EXPLAIN SELECT ...")
                return
            self._select(parts[1:], where_text, explain=True)
        elif keyword == "DELETE":
            self._delete(parts, where_text)
        elif keyword == "UPDATE":
            self._update(parts)
        elif keyword == "DROP":
            self._drop(parts)
        elif keyword == "SHOW":
            if len(parts) == 2 and parts[1].upper() == "TABLES":
                for name in list_tables():
                    print(name)
            else:
                print("Usage: SHOW TABLES")
        elif keyword == "STATS":
            for key, value in engine_stats().items():
                print("%s: %s" % (key, "%.3f" % value if isinstance(value, float) else value))
        elif keyword == "CHECKPOINT":
            print("Flushed %d page(s) to disk" % checkpoint())
        elif keyword == "HELP":
            print(HELP)
        else:
            print("Unknown command")

    def _create(self, parts):
        if len(parts) < 4:
            print("Usage: CREATE TABLE table_name column1 column2 ...")
            return

        if parts[1].upper() != "TABLE":
            print("Unknown command")
            return

        if create_table(parts[2], parts[3:]):
            print("Table %s created" % parts[2])

    def _insert(self, parts):
        if len(parts) >= 3 and parts[1].upper() == "INTO":
            parts = [parts[0]] + parts[2:]
            if len(parts) >= 3 and parts[2].upper() == "VALUES":
                parts = parts[:2] + parts[3:]

        if len(parts) < 3:
            print("Usage: INSERT table_name value1 value2 ...")
            return

        if insert_into_table(parts[1], parts[2:]):
            print("1 row inserted")

    def _select(self, parts, where_text, explain):
        usage = "Usage: SELECT * FROM table_name [WHERE ...]"
        conditions = parse_conditions(where_text) if where_text else []

        if len(parts) == 2:
            table_name = parts[1]
            columns = "*"
        else:
            upper = [part.upper() for part in parts]
            if "FROM" not in upper:
                print(usage)
                return
            position = upper.index("FROM")
            if position == 1 or len(parts) != position + 2:
                print(usage)
                return
            table_name = parts[position + 1]
            names = [name for part in parts[1:position] for name in part.split(",") if name]
            columns = "*" if names == ["*"] else names

        if explain:
            plan = explain_select(table_name, conditions)
            if plan is not None:
                print(plan)
            return

        table = select_from_table(table_name, columns, conditions)

        if table is None:
            return

        for row in table:
            print(" | ".join(row))
        print("(%d rows)" % (len(table) - 1))

    def _delete(self, parts, where_text):
        if len(parts) >= 3 and parts[1].upper() == "FROM":
            if len(parts) != 3 or where_text is None:
                print("Usage: DELETE FROM table_name WHERE ...")
                return
            count = delete_from_table(parts[2], parse_conditions(where_text))
            print("%d row(s) deleted" % count)
            return

        if len(parts) != 3:
            print("Usage: DELETE table_name id")
            return

        count = delete_from_table(parts[1], parts[2])
        print("%d row(s) deleted" % count)

    def _update(self, parts):
        if len(parts) != 5:
            print("Usage: UPDATE table_name id column_name new_value")
            return

        if update_table(parts[1], parts[2], parts[3], parts[4]):
            print("1 row updated")

    def _drop(self, parts):
        if len(parts) != 3 or parts[1].upper() != "TABLE":
            print("Usage: DROP TABLE table_name")
            return

        if drop_table(parts[2]):
            print("Table %s dropped" % parts[2])
