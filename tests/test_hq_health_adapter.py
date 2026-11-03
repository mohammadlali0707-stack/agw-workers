import sys
import unittest
from pathlib import Path
from threading import Event, Thread
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hq import health_adapter as adapter
from hq import health_snapshot as health

class HealthPresenterTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.calls = 0
        self.should_fail = False
    def clock(self): return self.now
    def collect(self):
        self.calls += 1
        if self.should_fail: raise RuntimeError('secret must not leak')
        now = health.utc_now()
        items = [health.observation(n, 'healthy', 'test', now, 'ok') for n in sorted(adapter.ALLOWED_NAMES)]
        return health.make_snapshot(items, now)
    def make(self, auth=lambda p,c: p == 'verified-owner'):
        return adapter.HealthPresenter(auth, self.collect, clock=self.clock)
    def test_unauthorized_does_not_collect(self):
        p=self.make(); self.assertEqual(p.render('intruder','telegram'), adapter.DENIED); self.assertEqual(self.calls,0)
    def test_auth_exception_or_truthy_non_boolean_denied(self):
        def bad(p,c): raise RuntimeError('secret')
        self.assertEqual(self.make(bad).render('any','bale'), adapter.DENIED)
        self.assertEqual(self.make(lambda p,c: 'yes').render('any','bale'), adapter.DENIED)
        self.assertEqual(self.calls,0)
    def test_unknown_channel_denied(self):
        p=self.make(); self.assertEqual(p.render('verified-owner','web'),adapter.DENIED)
        self.assertEqual(p.render('verified-owner',[]),adapter.DENIED); self.assertEqual(self.calls,0)
    def test_same_cached_data_on_all_channels(self):
        p=self.make(); outputs=[p.render('verified-owner',c) for c in ('telegram','bale','cluster')]
        self.assertTrue(all('وضعیت HQ' in x for x in outputs)); self.assertEqual(self.calls,1)
        self.assertEqual(outputs[0],outputs[1]); self.assertEqual(outputs[1],outputs[2])
    def test_original_collection_time_preserved_in_cache_and_fallback(self):
        captured = []
        def collect():
            snap = self.collect(); captured.append(snap['generated_at']); return snap
        p=adapter.HealthPresenter(lambda p,c: True,collect,clock=self.clock)
        first = p.render('x','cluster'); self.assertIn(captured[0], first)
        self.assertIn(captured[0], p.render('x','bale'))
        self.now+=31; self.should_fail=True
        self.assertIn(captured[0], p.render('x','cluster'))
    def test_refresh_after_ttl(self):
        p=self.make();p.render('verified-owner','cluster');self.now+=31;p.render('verified-owner','cluster');self.assertEqual(self.calls,2)
    def test_failure_before_any_data_does_not_leak_and_is_rate_limited(self):
        self.should_fail=True;p=self.make();a=p.render('verified-owner','cluster');b=p.render('verified-owner','cluster')
        self.assertEqual(a,adapter.UNAVAILABLE);self.assertEqual(a,b);self.assertNotIn('secret',a);self.assertEqual(self.calls,1)
        self.now+=6;p.render('verified-owner','cluster');self.assertEqual(self.calls,2)
    def test_stale_after_refresh_failure(self):
        p=self.make();p.render('verified-owner','cluster');self.now+=31;self.should_fail=True
        a=p.render('verified-owner','cluster'); self.assertIn('قدیمی',a); self.assertNotIn('secret',a)
        self.assertEqual(self.calls,2);p.render('verified-owner','cluster');self.assertEqual(self.calls,2)
        self.now+=6;self.should_fail=False;b=p.render('verified-owner','cluster');self.assertNotIn('داده زیر آخرین',b);self.assertEqual(self.calls,3)
    def test_bad_snapshot_rejected(self):
        p=adapter.HealthPresenter(lambda p,c: True,lambda: {'generated_at':'x','checks':[]},clock=self.clock)
        self.assertEqual(p.render('x','telegram'),adapter.UNAVAILABLE)
    def test_single_flight_returns_busy(self):
        entered,release=Event(),Event()
        def slow(): entered.set();release.wait(2);return self.collect()
        p=adapter.HealthPresenter(lambda p,c: True,slow,clock=self.clock,lock_timeout=0.01)
        t=Thread(target=lambda: p.render('x','telegram'));t.start();self.assertTrue(entered.wait(1))
        try: self.assertEqual(p.render('x','cluster'),adapter.BUSY)
        finally: release.set();t.join(2)
        self.assertEqual(self.calls,1)

if __name__ == '__main__': unittest.main()
