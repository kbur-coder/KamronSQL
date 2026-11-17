import argparse
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import Database
from src.table import Condition


def timed(function):
    start = time.perf_counter()
    result = function()
    return result, time.perf_counter() - start


def build(path, rows, pool_size, policy):
    database = Database(path, pool_size=pool_size, policy=policy)
    table = database.create_table("items", ["id", "rank:int", "name"])
    keys = list(range(1, rows + 1))
    random.Random(1).shuffle(keys)
    _, seconds = timed(lambda: [table.insert([key, key, "item-%d" % key]) for key in keys])
    return database, table, seconds


def row(label, indexed, scanned, unit):
    speedup = scanned / indexed if indexed else float("inf")
    print("| %s | %.3f %s | %.3f %s | %.0fx |" % (label, indexed, unit, scanned, unit, speedup))


def main():
    parser = argparse.ArgumentParser(description="KamronSQL storage engine benchmark")
    parser.add_argument("--rows", type=int, default=30000)
    parser.add_argument("--pool", type=int, default=256)
    parser.add_argument("--policy", default="clock", choices=["clock", "lru-k"])
    parser.add_argument("--lookups", type=int, default=2000)
    parser.add_argument("--range-size", type=int, default=500)
    parser.add_argument("--ranges", type=int, default=50)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "bench.db")
        database, table, insert_seconds = build(path, args.rows, args.pool, args.policy)
        database.checkpoint()
        stats = database.stats()
        print("rows: %d, page size: %d, pages in file: %d (%.1f MB)" % (
            args.rows, stats["page_size"], stats["pages_in_file"], stats["pages_in_file"] * stats["page_size"] / 1e6))
        print("B+ tree height: %d, heap pages: %d" % (table.index.height, len(table.heap.page_ids())))
        print("insert: %.2f s (%.0f rows/s), pool %d pages, policy %s" % (
            insert_seconds, args.rows / insert_seconds, args.pool, args.policy))
        print()

        rng = random.Random(2)
        targets = [rng.randint(1, args.rows) for _ in range(args.lookups)]
        _, point_index = timed(lambda: [table.select([Condition("id", "=", key)]) for key in targets])
        scan_targets = targets[:10]
        _, point_scan = timed(lambda: [table.select([Condition("rank", "=", key)]) for key in scan_targets])

        starts = [rng.randint(1, args.rows - args.range_size) for _ in range(args.ranges)]
        _, range_index = timed(lambda: [
            table.select([Condition("id", ">=", s), Condition("id", "<", s + args.range_size)]) for s in starts])
        scan_starts = starts[:5]
        _, range_scan = timed(lambda: [
            table.select([Condition("rank", ">=", s), Condition("rank", "<", s + args.range_size)]) for s in scan_starts])

        print("| query (average per query) | index (B+ tree) | full scan | speedup |")
        print("| --- | --- | --- | --- |")
        row("point lookup, id = ?", point_index / len(targets) * 1000, point_scan / len(scan_targets) * 1000, "ms")
        row("range, %d keys" % args.range_size, range_index / len(starts) * 1000, range_scan / len(scan_starts) * 1000, "ms")
        database.close()

        print()
        print("| buffer pool pages | hit ratio | disk reads | time for %d random lookups |" % args.lookups)
        print("| --- | --- | --- | --- |")
        for pool_size in (8, 32, 128, 512):
            database = Database(path, pool_size=pool_size, policy=args.policy)
            table = database.table("items")
            before = database.engine.disk.reads
            _, seconds = timed(lambda: [table.get(key) for key in targets])
            pool = database.engine.pool
            total = pool.hits + pool.misses
            print("| %d | %.1f%% | %d | %.2f s |" % (
                pool_size, 100.0 * pool.hits / total, database.engine.disk.reads - before, seconds))
            database.close()


main()
