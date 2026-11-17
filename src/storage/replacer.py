from collections import deque


class ClockReplacer:
    def __init__(self, capacity):
        self.capacity = capacity
        self.referenced = [False] * capacity
        self.evictable = [False] * capacity
        self.hand = 0

    def record_access(self, frame_id):
        self.referenced[frame_id] = True

    def set_evictable(self, frame_id, flag):
        self.evictable[frame_id] = flag

    def evict(self):
        for _ in range(2 * self.capacity):
            frame_id = self.hand
            self.hand = (self.hand + 1) % self.capacity
            if not self.evictable[frame_id]:
                continue
            if self.referenced[frame_id]:
                self.referenced[frame_id] = False
                continue
            self.evictable[frame_id] = False
            return frame_id
        return None

    def remove(self, frame_id):
        self.referenced[frame_id] = False
        self.evictable[frame_id] = False


class LRUKReplacer:
    def __init__(self, capacity, k=2):
        if k < 1:
            raise ValueError("k должно быть не меньше 1")
        self.capacity = capacity
        self.k = k
        self.history = [deque(maxlen=k) for _ in range(capacity)]
        self.evictable = [False] * capacity
        self.clock = 0

    def record_access(self, frame_id):
        self.clock += 1
        self.history[frame_id].append(self.clock)

    def set_evictable(self, frame_id, flag):
        self.evictable[frame_id] = flag

    def evict(self):
        best = None
        best_key = None
        for frame_id in range(self.capacity):
            if not self.evictable[frame_id]:
                continue
            history = self.history[frame_id]
            if len(history) < self.k:
                key = (0, history[0] if history else 0)
            else:
                key = (1, history[0])
            if best_key is None or key < best_key:
                best = frame_id
                best_key = key
        if best is None:
            return None
        self.history[best].clear()
        self.evictable[best] = False
        return best

    def remove(self, frame_id):
        self.history[frame_id].clear()
        self.evictable[frame_id] = False


def make_replacer(policy, capacity, k=2):
    if policy == "clock":
        return ClockReplacer(capacity)
    if policy in ("lru-k", "lruk"):
        return LRUKReplacer(capacity, k)
    raise ValueError("Неизвестная политика вытеснения: %s" % policy)
