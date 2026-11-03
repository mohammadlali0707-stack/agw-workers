import json
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from hq.health_snapshot import observation, probe_service, probe_disk, probe_memory, make_snapshot, render_fa

NOW = datetime(2026, 11, 3, tzinfo=timezone.utc)

class HealthSnapshotTests(unittest.TestCase):
    def test_active_is_process_state_not_end_to_end(self):
        x = probe_service('svc', NOW, lambda *a, **kw: SimpleNamespace(stdout='active\n', returncode=0))
        self.assertEqual(x['status'], 'healthy')
        self.assertIn('انتهابه‌انتها', x['detail'])
    def test_failed_and_missing_and_timeout(self):
        for out, rc, expected in [('failed',3,'degraded'), ('unknown',4,'unknown')]:
            self.assertEqual(probe_service('x', NOW, lambda *a, **kw: SimpleNamespace(stdout=out, returncode=rc))['status'], expected)
        def timeout(*a, **kw): raise subprocess.TimeoutExpired('systemctl', 2)
        self.assertEqual(probe_service('x', NOW, timeout)['status'], 'unknown')
    def test_memory_and_disk_thresholds(self):
        self.assertEqual(probe_memory(NOW, lambda: 'MemTotal: 100 kB\nMemAvailable: 9 kB\n')['status'], 'degraded')
        self.assertEqual(probe_memory(NOW, lambda: 'broken')['status'], 'unknown')
        self.assertEqual(probe_disk(NOW, lambda _: SimpleNamespace(total=100, used=90))['status'], 'degraded')
    def test_stale_unknown_and_future(self):
        a = observation('old','healthy','fixture',NOW-timedelta(seconds=181))
        b = observation('future','healthy','fixture',NOW+timedelta(seconds=61))
        s = make_snapshot([a,b], NOW)
        self.assertEqual([x['status'] for x in s['checks']], ['stale','unknown'])
        self.assertEqual(s['overall'], 'unknown')
    def test_degraded_wins_and_input_is_unchanged(self):
        a = observation('a','healthy','fixture',NOW-timedelta(hours=1))
        b = observation('b','degraded','fixture',NOW)
        s = make_snapshot([a,b], NOW)
        self.assertEqual(s['overall'], 'degraded')
        self.assertEqual(a['status'], 'healthy')
        self.assertIn('نیازمند توجه', render_fa(s))
        json.dumps(s, ensure_ascii=False)
    def test_empty_and_invalid_status(self):
        self.assertEqual(make_snapshot([],NOW)['overall'], 'unknown')
        with self.assertRaises(ValueError): observation('x','good','fixture',NOW)
    def test_no_shell_and_fixed_timeout(self):
        def run(args, **kw):
            self.assertEqual(args, ['systemctl', 'is-active', 'svc'])
            self.assertNotIn('shell', kw)
            self.assertEqual(kw['timeout'], 2)
            return SimpleNamespace(stdout='active', returncode=0)
        probe_service('svc', NOW, run)

if __name__ == '__main__': unittest.main()
