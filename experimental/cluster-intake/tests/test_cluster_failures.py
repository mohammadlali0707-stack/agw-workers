"""Offline failure and concurrency tests; no external processes or APIs involved."""
from concurrent.futures import ThreadPoolExecutor
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from hq.cluster_intake import Intake

AUTH = dict(trusted_source=True, owner_verified=True, room_verified=True)
TEXT = '@Notion2 synthetic failure test'

class FailureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'queue.sqlite')
        self.db = sqlite3.connect(self.path, timeout=10)
        self.queue = Intake(self.db)
        self.queue.capture(1, TEXT, **AUTH)
    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()
    def child(self, body):
        script = 'import os,sqlite3,sys\nfrom hq.cluster_intake import Intake\ndb=sqlite3.connect(sys.argv[1],timeout=10)\nq=Intake(db)\n'+body+'\nos._exit(23)\n'
        result = subprocess.run([sys.executable, '-c', script, self.path], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 23, result.stderr.decode(errors='replace'))
    def test_process_exit_after_claim_allows_reclaim_only_after_lease(self):
        self.child('q.claim(10,5)')
        self.assertIsNone(self.queue.claim(14,5))
        self.assertEqual(self.queue.claim(15,5)[0], 1)
    def test_process_exit_after_dispatch_never_automatically_reclaims(self):
        self.child("mid,token=q.claim(10,5)\nassert q.begin_dispatch(mid,token,"+repr(TEXT)+",11)")
        self.assertIsNone(self.queue.claim(1000,5))
        self.assertEqual(self.db.execute('SELECT state FROM intake').fetchone()[0], 'dispatching')
    def test_process_exit_mid_transaction_rolls_back(self):
        self.child("db.execute('BEGIN IMMEDIATE')\ndb.execute(\"UPDATE intake SET state='dispatching' WHERE message_id=1\")")
        self.assertEqual(self.db.execute('SELECT state FROM intake').fetchone()[0], 'pending')
        self.assertEqual(self.queue.claim(20,5)[0],1)
    def test_concurrent_dispatch_only_one_wins(self):
        mid,token = self.queue.claim(10,20)
        n = 12
        gate=threading.Barrier(n,timeout=15)
        def run(_):
            db=sqlite3.connect(self.path,timeout=10)
            try:
                q=Intake(db); gate.wait()
                return q.begin_dispatch(mid,token,TEXT,11)
            finally: db.close()
        with ThreadPoolExecutor(max_workers=n) as pool:
            results=list(pool.map(run,range(n)))
        self.assertEqual(sum(results),1)
        self.assertEqual(self.db.execute('SELECT state FROM intake').fetchone()[0],'dispatching')
    def test_conflict_freezes_pending(self):
        self.assertEqual(self.queue.capture(1,TEXT+' altered',**AUTH),'conflict')
        self.assertEqual(self.queue.capture(1,TEXT,**AUTH),'conflict')
        self.assertIsNone(self.queue.claim(20,5))
    def test_conflict_revokes_claim_before_dispatch(self):
        mid,token=self.queue.claim(10,20)
        self.assertEqual(self.queue.capture(1,TEXT+' altered',**AUTH),'conflict')
        self.assertFalse(self.queue.begin_dispatch(mid,token,TEXT,11))
        self.assertIsNone(self.queue.claim(100,5))
    def test_conflict_after_dispatch_does_not_undo_dispatch(self):
        mid,token=self.queue.claim(10,20)
        self.assertTrue(self.queue.begin_dispatch(mid,token,TEXT,11))
        self.assertEqual(self.queue.capture(1,TEXT+' altered',**AUTH),'conflict')
        self.assertTrue(self.queue.record_session(mid,token,'synthetic-id'))
        self.assertIsNone(self.queue.claim(100,5))

if __name__ == '__main__': unittest.main()
