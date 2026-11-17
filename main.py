from src.database import close_database, open_database
from src.parser import Parser
from src.storage import StorageError

try:
    open_database(flush_interval=1.0)
except StorageError as error:
    print("Не удалось открыть базу данных: %s" % error)
    raise SystemExit(1)

parser = Parser()

print("Welcome to KamronSQL!")
print("Type 'help' for commands, 'exit' to quit.")

try:
    while True:
        try:
            command = input("kamronsql > ")
        except EOFError:
            break

        if command.lower() == "exit":
            break

        parser.parse(command)
except KeyboardInterrupt:
    print()
finally:
    close_database()
