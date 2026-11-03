import os
import sqlite3
import tempfile
import unittest
from hq.cluster_intake import Intake

AUTH = dict(trusted_source=True, owner_verified=True, room_verified=True)
TEXT = '@Notion2 synthetic request'

class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.temp.name, 'test.sqlite')
        self.db = sqlite3.connect(self.path)
        self.i = Intake(self.db)
    def tearDown(self):
        self.db.close()
        self.temp.cleanup()
    def test_explicit_tag_and_boundaries(self):
        for text in ['hello', '@Notion20 hi', 'x@Notion2 hi', '@Notion3 hi']:
            self.assertEqual(self.i.capture(1, text, **AUTH), 'not_addressed')
        for n, text in enumerate(['@Notion2 hi', '@نوشن2 سلام', '@نوشن۲ سلام'], 1):
            self.assertEqual(self.i.capture(n, text, **AUTH), 'captured')
    def test_requires_independent_checks(self):
        for field in AUTH:
            args = dict(AUTH); args[field] = False
            self.assertEqual(self.i.capture(1, TEXT, **args), 'unauthorized')
        self.assertEqual(self.i.capture(1, TEXT, trusted_source=1, owner_verified=True, room_verified=True), 'unauthorized')
        self.assertEqual(self.db.execute('SELECT count(*) FROM intake').fetchone()[0], 0)
    def test_invalid_data(self):
        for mid, text in [(True, TEXT), (0, TEXT), (2**63, TEXT), (1, None), (1, '@Notion2 \ud800')]:
            self.assertEqual(self.i.capture(mid, text, **AUTH), 'invalid')
    def test_idempotence_conflict_and_digest_only(self):
        self.assertEqual(self.i.capture(1, TEXT, **AUTH), 'captured')
        self.assertEqual(self.i.capture(1, TEXT, **AUTH), 'duplicate')
        self.assertEqual(self.i.capture(1, TEXT+' changed', **AUTH), 'conflict')
        self.assertEqual(self.i.capture(1, 'tag removed', **AUTH), 'conflict')
        row = self.db.execute('SELECT * FROM intake').fetchone()
        self.assertNotIn(TEXT, str(row))
    def test_lease_and_stale_claim(self):
        self.i.capture(1, TEXT, **AUTH)
        mid, first = self.i.claim(10, 5)
        self.assertIsNone(self.i.claim(14, 5))
        _, second = self.i.claim(15, 5)
        self.assertNotEqual(first, second)
        self.assertFalse(self.i.begin_dispatch(mid, first, TEXT, 16))
        self.assertFalse(self.i.begin_dispatch(mid, second, TEXT+'x', 16))
        self.assertTrue(self.i.begin_dispatch(mid, second, TEXT, 16))
    def test_lease_expiry_prevents_dispatch(self):
        self.i.capture(1, TEXT, **AUTH)
        mid, token = self.i.claim(10, 5)
        self.assertFalse(self.i.begin_dispatch(mid, token, TEXT, 15))
    def test_dispatch_ambiguity_is_never_reclaimed(self):
        self.i.capture(1, TEXT, **AUTH)
        mid, token = self.i.claim(10, 5)
        self.assertTrue(self.i.begin_dispatch(mid, token, TEXT, 11))
        self.assertIsNone(self.i.claim(1000, 5))
        self.assertFalse(self.i.begin_dispatch(mid, token, TEXT, 12))
        self.assertFalse(self.i.record_session(mid, 'wrong', 'session'))
        self.assertTrue(self.i.record_session(mid, token, 'synthetic-session'))
        self.assertFalse(self.i.record_session(mid, token, 'session2'))
        self.assertIsNone(self.i.claim(2000, 5))
    def test_invalid_clocks(self):
        for now, lease in [(float('nan'), 1), (1, float('inf')), (1, 0), (True, 1), (1e308, 1e308)]:
            with self.assertRaises(ValueError): self.i.claim(now, lease)
    def test_separate_connection_cannot_claim_same_live_lease(self):
        self.i.capture(1, TEXT, **AUTH)
        otherdb = sqlite3.connect(self.path)
        try:
            other = Intake(otherdb)
            self.assertIsNotNone(self.i.claim(10, 5))
            self.assertIsNone(other.claim(10, 5))
            self.assertIsNotNone(other.claim(15, 5))
        finally: otherdb.close()
    def test_reopen_persists_dispatching_state(self):
        self.i.capture(1, TEXT, **AUTH)
        mid, token = self.i.claim(10, 5)
        self.i.begin_dispatch(mid, token, TEXT, 11)
        otherdb = sqlite3.connect(self.path)
        try:
            self.assertIsNone(Intake(otherdb).claim(100, 5))
        finally: otherdb.close()

if __name__ == '__main__': unittest.main()
