"""Simulated intake and remote failures: no network or real external service."""
import os
import sqlite3
import tempfile
import unittest
from hq.cluster_intake import Intake

AUTH = dict(trusted_source=True,owner_verified=True,room_verified=True)
TEXT = '@Notion2 synthetic integration'

class FakeRemote:
    def __init__(self, mode): self.mode,self.calls,self.created=mode,0,[]
    def create(self):
        self.calls += 1
        if self.mode == 'timeout_before_accept': raise TimeoutError()
        self.created.append('synthetic-session')
        if self.mode == 'timeout_after_accept': raise TimeoutError()
        return self.created[-1]

def offline_attempt(q, remote, mid=1, text=TEXT):
    status=q.capture(mid,text,**AUTH)
    if status != 'captured': return status
    job=q.claim(10,10)
    if not job or not q.begin_dispatch(job[0],job[1],text,11): return 'blocked'
    try: session=remote.create()
    except TimeoutError: return 'unknown'
    return 'known' if q.record_session(job[0],job[1],session) else 'unknown'

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=os.path.join(self.tmp.name,'queue.sqlite')
        self.db=sqlite3.connect(self.path,timeout=10)
        self.q=Intake(self.db)
    def tearDown(self):
        self.db.close(); self.tmp.cleanup()
    def test_simulated_success_and_duplicate(self):
        r=FakeRemote('success')
        self.assertEqual(offline_attempt(self.q,r),'known')
        self.assertEqual(offline_attempt(self.q,r),'duplicate')
        self.assertEqual(r.calls,1)
        self.assertEqual(self.q.inspect(1),{'state':'session_known','session_known':True,'conflict_observations':0})
    def test_timeout_before_accept_stays_blocked(self):
        r=FakeRemote('timeout_before_accept')
        self.assertEqual(offline_attempt(self.q,r),'unknown')
        self.assertEqual(r.created,[])
        self.assertIsNone(self.q.claim(999,10))
        self.assertEqual(r.calls,1)
        self.assertEqual(self.q.inspect(1)['state'],'dispatching')
    def test_timeout_after_accept_stays_blocked(self):
        r=FakeRemote('timeout_after_accept')
        self.assertEqual(offline_attempt(self.q,r),'unknown')
        self.assertEqual(len(r.created),1)
        self.assertIsNone(self.q.claim(999,10))
        self.assertEqual(r.calls,1)
    def test_conflict_after_session_persists_across_reopen(self):
        r=FakeRemote('success'); self.assertEqual(offline_attempt(self.q,r),'known')
        self.assertEqual(self.q.capture(1,TEXT+' altered',**AUTH),'conflict')
        self.assertEqual(self.q.capture(1,TEXT+' altered',**AUTH),'conflict')
        db2=sqlite3.connect(self.path)
        try:
            state=Intake(db2).inspect(1)
            self.assertEqual(state,{'state':'session_known','session_known':True,'conflict_observations':2})
            self.assertNotIn(TEXT,repr(db2.execute('SELECT * FROM intake_conflicts').fetchall()))
        finally: db2.close()
    def test_duplicate_original_while_conflict_does_not_increase_count(self):
        self.q.capture(1,TEXT,**AUTH)
        self.q.capture(1,TEXT+' altered',**AUTH)
        self.assertEqual(self.q.capture(1,TEXT,**AUTH),'conflict')
        self.assertEqual(self.q.inspect(1)['conflict_observations'],1)
    def test_conflict_during_dispatch_persists_without_cancelling_session(self):
        self.q.capture(1,TEXT,**AUTH); mid,token=self.q.claim(10,10)
        self.q.begin_dispatch(mid,token,TEXT,11)
        self.q.capture(1,TEXT+' altered',**AUTH)
        self.assertEqual(self.q.inspect(1)['conflict_observations'],1)
        self.assertTrue(self.q.record_session(mid,token,'synthetic-session'))
        self.assertEqual(self.q.inspect(1)['state'],'session_known')
    def test_database_write_failure_does_not_call_remote(self):
        r=FakeRemote('success'); self.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.OperationalError): offline_attempt(self.q,r)
        self.assertEqual(r.calls,0)
    def test_db_failure_after_remote_accept_leaves_ambiguous_dispatch(self):
        r=FakeRemote('success')
        self.q.capture(1,TEXT,**AUTH); mid,token=self.q.claim(10,10)
        self.assertTrue(self.q.begin_dispatch(mid,token,TEXT,11))
        session=r.create()
        self.db.execute('PRAGMA query_only=ON')
        with self.assertRaises(sqlite3.OperationalError): self.q.record_session(mid,token,session)
        self.db.execute('PRAGMA query_only=OFF')
        self.assertEqual(self.q.inspect(mid)['state'],'dispatching')
        self.assertEqual(len(r.created),1)
        self.assertIsNone(self.q.claim(999,10))

    def test_unauthorized_not_captured(self):
        self.assertEqual(self.q.capture(1,TEXT,trusted_source=False,owner_verified=True,room_verified=True),'unauthorized')
        self.assertIsNone(self.q.inspect(1))
    def test_inspect_invalid_and_missing_ids(self):
        for mid in (True,0,2**63,'1',None,1): self.assertIsNone(self.q.inspect(mid))

if __name__ == '__main__': unittest.main()
