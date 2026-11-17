import struct
from bisect import bisect_left, bisect_right

from .errors import DuplicateKeyError, InvalidPageError
from .layout import (
    COMMON_HEADER_SIZE,
    INVALID_PAGE,
    PAGE_BTREE_INTERNAL,
    PAGE_BTREE_LEAF,
    PAGE_BTREE_META,
    RID,
    get_page_type,
    set_page_type,
)

META = struct.Struct("<iIqII")
LEAF_HEADER = struct.Struct("<Hii")
LEAF_ENTRY = struct.Struct("<qiH")
INTERNAL_HEADER = struct.Struct("<Hi")
INTERNAL_ENTRY = struct.Struct("<qi")
MIN_FANOUT = 3


def leaf_capacity(page_size):
    return (page_size - COMMON_HEADER_SIZE - LEAF_HEADER.size) // LEAF_ENTRY.size


def internal_capacity(page_size):
    return (page_size - COMMON_HEADER_SIZE - INTERNAL_HEADER.size) // INTERNAL_ENTRY.size


class LeafNode:
    __slots__ = ("page_id", "keys", "rids", "prev", "next")

    def __init__(self, page_id, keys, rids, prev=INVALID_PAGE, next=INVALID_PAGE):
        self.page_id = page_id
        self.keys = keys
        self.rids = rids
        self.prev = prev
        self.next = next


class InternalNode:
    __slots__ = ("page_id", "keys", "children")

    def __init__(self, page_id, keys, children):
        self.page_id = page_id
        self.keys = keys
        self.children = children


class BPlusTree:
    def __init__(self, pool, meta_page_id):
        self.pool = pool
        self.meta_page_id = meta_page_id
        with pool.fetch(meta_page_id) as page:
            if get_page_type(page.data) != PAGE_BTREE_META:
                raise InvalidPageError("Страница %d не является метой B+ дерева" % meta_page_id)
            root, height, count, leaf_max, internal_max = META.unpack_from(page.data, COMMON_HEADER_SIZE)
        self._root = root
        self._height = height
        self._count = count
        self.leaf_max = leaf_max
        self.internal_max = internal_max
        self.min_leaf = leaf_max // 2
        self.min_internal = internal_max // 2

    @classmethod
    def create(cls, pool, leaf_max=None, internal_max=None):
        page_size = pool.disk.page_size
        leaf_limit = leaf_capacity(page_size)
        internal_limit = internal_capacity(page_size)
        leaf_max = leaf_limit if leaf_max is None else leaf_max
        internal_max = internal_limit if internal_max is None else internal_max
        if not MIN_FANOUT <= leaf_max <= leaf_limit:
            raise ValueError("leaf_max должен быть от %d до %d" % (MIN_FANOUT, leaf_limit))
        if not MIN_FANOUT <= internal_max <= internal_limit:
            raise ValueError("internal_max должен быть от %d до %d" % (MIN_FANOUT, internal_limit))
        with pool.new_page() as root_page:
            root_id = root_page.page_id
            set_page_type(root_page.data, PAGE_BTREE_LEAF)
            LEAF_HEADER.pack_into(root_page.data, COMMON_HEADER_SIZE, 0, INVALID_PAGE, INVALID_PAGE)
            root_page.mark_dirty()
        with pool.new_page() as meta_page:
            meta_id = meta_page.page_id
            set_page_type(meta_page.data, PAGE_BTREE_META)
            META.pack_into(meta_page.data, COMMON_HEADER_SIZE, root_id, 1, 0, leaf_max, internal_max)
            meta_page.mark_dirty()
        return cls(pool, meta_id)

    @classmethod
    def open(cls, pool, meta_page_id):
        return cls(pool, meta_page_id)

    @property
    def height(self):
        return self._height

    @property
    def root_page_id(self):
        return self._root

    def __len__(self):
        return self._count

    def _write_meta(self):
        with self.pool.fetch(self.meta_page_id) as page:
            META.pack_into(
                page.data, COMMON_HEADER_SIZE, self._root, self._height,
                self._count, self.leaf_max, self.internal_max,
            )
            page.mark_dirty()

    def _load(self, page_id):
        with self.pool.fetch(page_id) as page:
            data = page.data
            page_type = get_page_type(data)
            if page_type == PAGE_BTREE_LEAF:
                count, prev, nxt = LEAF_HEADER.unpack_from(data, COMMON_HEADER_SIZE)
                offset = COMMON_HEADER_SIZE + LEAF_HEADER.size
                keys = []
                rids = []
                for _ in range(count):
                    key, rid_page, rid_slot = LEAF_ENTRY.unpack_from(data, offset)
                    keys.append(key)
                    rids.append(RID(rid_page, rid_slot))
                    offset += LEAF_ENTRY.size
                return LeafNode(page_id, keys, rids, prev, nxt)
            if page_type == PAGE_BTREE_INTERNAL:
                count, first_child = INTERNAL_HEADER.unpack_from(data, COMMON_HEADER_SIZE)
                offset = COMMON_HEADER_SIZE + INTERNAL_HEADER.size
                keys = []
                children = [first_child]
                for _ in range(count):
                    key, child = INTERNAL_ENTRY.unpack_from(data, offset)
                    keys.append(key)
                    children.append(child)
                    offset += INTERNAL_ENTRY.size
                return InternalNode(page_id, keys, children)
            raise InvalidPageError("Страница %d не является узлом B+ дерева" % page_id)

    def _store(self, node):
        with self.pool.fetch(node.page_id) as page:
            data = page.data
            data[COMMON_HEADER_SIZE:] = bytes(len(data) - COMMON_HEADER_SIZE)
            if isinstance(node, LeafNode):
                set_page_type(data, PAGE_BTREE_LEAF)
                LEAF_HEADER.pack_into(data, COMMON_HEADER_SIZE, len(node.keys), node.prev, node.next)
                offset = COMMON_HEADER_SIZE + LEAF_HEADER.size
                for key, rid in zip(node.keys, node.rids):
                    LEAF_ENTRY.pack_into(data, offset, key, rid.page_id, rid.slot)
                    offset += LEAF_ENTRY.size
            else:
                set_page_type(data, PAGE_BTREE_INTERNAL)
                INTERNAL_HEADER.pack_into(data, COMMON_HEADER_SIZE, len(node.keys), node.children[0])
                offset = COMMON_HEADER_SIZE + INTERNAL_HEADER.size
                for key, child in zip(node.keys, node.children[1:]):
                    INTERNAL_ENTRY.pack_into(data, offset, key, child)
                    offset += INTERNAL_ENTRY.size
            page.mark_dirty()

    def _new_page_id(self):
        with self.pool.new_page() as page:
            return page.page_id

    def _descend(self, key):
        path = []
        node = self._load(self._root)
        while isinstance(node, InternalNode):
            index = bisect_right(node.keys, key)
            path.append((node, index))
            node = self._load(node.children[index])
        return path, node

    def search(self, key):
        _, leaf = self._descend(key)
        index = bisect_left(leaf.keys, key)
        if index < len(leaf.keys) and leaf.keys[index] == key:
            return leaf.rids[index]
        return None

    def update(self, key, rid):
        _, leaf = self._descend(key)
        index = bisect_left(leaf.keys, key)
        if index >= len(leaf.keys) or leaf.keys[index] != key:
            return False
        leaf.rids[index] = rid
        self._store(leaf)
        return True

    def insert(self, key, rid):
        path, leaf = self._descend(key)
        index = bisect_left(leaf.keys, key)
        if index < len(leaf.keys) and leaf.keys[index] == key:
            raise DuplicateKeyError("Ключ %d уже есть в индексе" % key)
        leaf.keys.insert(index, key)
        leaf.rids.insert(index, rid)
        self._count += 1
        if len(leaf.keys) <= self.leaf_max:
            self._store(leaf)
            self._write_meta()
            return
        middle = len(leaf.keys) // 2
        right = LeafNode(
            self._new_page_id(), leaf.keys[middle:], leaf.rids[middle:], leaf.page_id, leaf.next
        )
        leaf.keys = leaf.keys[:middle]
        leaf.rids = leaf.rids[:middle]
        leaf.next = right.page_id
        if right.next != INVALID_PAGE:
            after = self._load(right.next)
            after.prev = right.page_id
            self._store(after)
        self._store(leaf)
        self._store(right)
        separator = right.keys[0]
        new_child = right.page_id
        while path:
            parent, child_index = path.pop()
            parent.keys.insert(child_index, separator)
            parent.children.insert(child_index + 1, new_child)
            if len(parent.keys) <= self.internal_max:
                self._store(parent)
                self._write_meta()
                return
            middle = len(parent.keys) // 2
            promoted = parent.keys[middle]
            sibling = InternalNode(
                self._new_page_id(), parent.keys[middle + 1:], parent.children[middle + 1:]
            )
            parent.keys = parent.keys[:middle]
            parent.children = parent.children[:middle + 1]
            self._store(parent)
            self._store(sibling)
            separator = promoted
            new_child = sibling.page_id
        new_root = InternalNode(self._new_page_id(), [separator], [self._root, new_child])
        self._store(new_root)
        self._root = new_root.page_id
        self._height += 1
        self._write_meta()
    def check(self):
        leaves = []
        depths = set()
        self._check_node(self._root, None, None, 1, True, leaves, depths)
        assert depths == {self._height}, "листья на разной глубине или неверная высота: %s и %d" % (depths, self._height)
        total = 0
        previous = INVALID_PAGE
        for page_id in leaves:
            leaf = self._load(page_id)
            assert leaf.prev == previous, "сломана обратная ссылка листа %d" % page_id
            previous = page_id
            total += len(leaf.keys)
        last = self._load(leaves[-1])
        assert last.next == INVALID_PAGE, "у последнего листа есть next"
        forward = []
        current = leaves[0]
        while current != INVALID_PAGE:
            forward.append(current)
            current = self._load(current).next
        assert forward == leaves, "цепочка листьев по next не совпадает с обходом дерева"
        assert total == self._count, "счётчик %d не равен числу ключей %d" % (self._count, total)
        return {"height": self._height, "leaves": len(leaves), "keys": total}

    def _check_node(self, page_id, low, high, depth, is_root, leaves, depths):
        node = self._load(page_id)
        keys = node.keys
        assert all(keys[i] < keys[i + 1] for i in range(len(keys) - 1)), "ключи не упорядочены в узле %d" % page_id
        for key in keys:
            assert (low is None or key >= low) and (high is None or key < high), "ключ %d вне границ узла %d" % (key, page_id)
        if isinstance(node, LeafNode):
            assert len(keys) <= self.leaf_max, "лист %d переполнен" % page_id
            assert is_root or len(keys) >= self.min_leaf, "лист %d недозаполнен" % page_id
            leaves.append(page_id)
            depths.add(depth)
            return
        assert len(node.children) == len(keys) + 1, "неверное число детей в узле %d" % page_id
        assert len(keys) <= self.internal_max, "узел %d переполнен" % page_id
        assert len(keys) >= (1 if is_root else self.min_internal), "узел %d недозаполнен" % page_id
        bounds = [low] + keys + [high]
        for index, child in enumerate(node.children):
            self._check_node(child, bounds[index], bounds[index + 1], depth + 1, False, leaves, depths)