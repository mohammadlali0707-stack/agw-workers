"""Concurrent offline tests using separate SQLite connections and synthetic data."""
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import tempfile
import threading
import unittest
from hq.cluster_intake import Intake

AUTH = dict(trusted_source=True, owner_verified=True, room_verified=True)
TEXT = '@Notion2 synthetic concurrency test'

class ConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'queue.sqlite')
        self.db = sqlite3.connect(self.path, timeout=10)
        self.intake = Intake(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def race(self, workers, action):
        gate = threading.Barrier(workers, timeout=15)
        def run(i):
            db = sqlite3.connect(self.path, timeout=10)
            try:
                queue = Intake(db)
                gate.wait()
                return action(queue, i)
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(run, range(workers)))

    def test_simultaneous_claim_one_item(self):
        self.intake.capture(1, TEXT, **AUTH)
        results = self.race(12, lambda q, _: q.claim(100, 30))
        claims = [x for x in results if x is not None]
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0][0], 1)
        self.assertEqual(self.db.execute('SELECT state FROM intake').fetchone()[0], 'claimed')

    def test_simultaneous_claim_many_items(self):
        for mid in range(1, 9): self.intake.capture(mid, TEXT, **AUTH)
        results = self.race(12, lambda q, _: q.claim(100, 30))
        claims = [x for x in results if x is not None]
        self.assertEqual(len(claims), 8)
        self.assertEqual(len({mid for mid, _ in claims}), 8)
        self.assertEqual(len({token for _, token in claims}), 8)

    def test_simultaneous_identical_capture(self):
        results = self.race(12, lambda q, _: q.capture(1, TEXT, **AUTH))
        self.assertEqual(results.count('captured'), 1)
        self.assertEqual(results.count('duplicate'), 11)
        self.assertEqual(self.db.execute('SELECT count(*) FROM intake').fetchone()[0], 1)

    def test_simultaneous_conflicting_capture(self):
        results = self.race(12, lambda q, i: q.capture(1, TEXT + str(i), **AUTH))
        self.assertEqual(results.count('captured'), 1)
        self.assertEqual(results.count('conflict'), 11)
        self.assertEqual(self.db.execute('SELECT count(*) FROM intake').fetchone()[0], 1)
        self.assertEqual(self.intake.inspect(1)['conflict_observations'], 11)

if __name__ == '__main__': unittest.main()
