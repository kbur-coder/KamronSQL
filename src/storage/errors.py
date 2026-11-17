class StorageError(Exception):
    pass


class FileFormatError(StorageError):
    pass


class CorruptPageError(StorageError):
    pass


class InvalidPageError(StorageError):
    pass


class BufferPoolFullError(StorageError):
    pass


class TupleTooLargeError(StorageError):
    pass


class DuplicateKeyError(StorageError):
    pass
