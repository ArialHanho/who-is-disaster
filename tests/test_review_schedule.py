import unittest
from agent_app.review_schedule import ReviewSchedule
from agent_app.review_revision import captured_token, revision_token


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.schedule = ReviewSchedule(clock=lambda: self.now)

    def test_push_burst_coalesces_until_quiet_then_respects_cooldown(self):
        s = self.schedule
        for n in range(12):
            self.now = 1000+n*10
            s.observe(1, str(n))
            self.assertFalse(s.ready(1))
        self.now += 90
        self.assertTrue(s.ready(1))
        s.begin(1); s.complete(1, True)
        self.assertEqual(s.baselines['1'], '11')
        s.observe(1, '12')
        self.now += 90
        self.assertFalse(s.ready(1))
        self.now += 210
        self.assertTrue(s.ready(1))

    def test_continuous_pushes_have_a_maximum_wait(self):
        for n in range(21):
            self.now = 1000+n*30
            self.schedule.observe(1, str(n))
        self.assertTrue(self.schedule.ready(1))
        self.assertEqual(len(self.schedule.pending), 1)
        self.assertEqual(self.schedule.pending[1]['token'], '20')

    def test_running_review_is_not_restarted_and_does_not_consume_newer_head(self):
        s = self.schedule
        s.observe(1, 'a'); s.begin(1)
        self.now += 400
        s.observe(1, 'b'); s.observe(1, 'c')
        self.now += 90
        self.assertFalse(s.ready(1))
        s.complete(1, True)
        self.assertEqual(s.baselines['1'], 'a')
        self.assertEqual(s.pending[1]['token'], 'c')
        self.assertTrue(s.ready(1))

    def test_actual_capture_consumes_only_matching_new_head_and_manual_seeds_baseline(self):
        s = self.schedule
        s.observe(1, 'a'); s.begin(1)
        s.observe(1, 'b'); s.complete(1, True, 'b')
        self.assertNotIn(1, s.pending)
        s.begin(2); s.complete(2, True, 'manual')
        s.observe(2, 'manual')
        self.assertNotIn(2, s.pending)

    def test_failure_backoff_survives_restart_and_manual_bypasses_it(self):
        s = self.schedule
        s.observe(1, 'a'); s.begin(1); s.complete(1, False)
        for delay in (300, 600):
            self.now += delay
            self.assertTrue(s.ready(1))
            s.begin(1); s.complete(1, False)
        restarted = ReviewSchedule(s.state(), clock=lambda: self.now)
        restarted.observe(1, 'new')
        self.now += 600
        self.assertFalse(restarted.ready(1))
        restarted.begin(1)  # Explicit user action ignores the automatic gate.
        restarted.complete(1, True, 'new')
        self.assertNotIn('1', restarted.retry_after)

    def test_rollback_while_reviewing_is_preserved_and_disconnect_drops_pending(self):
        s = self.schedule
        s.baselines['1'] = 'old'
        s.observe(1, 'new'); s.begin(1)
        s.observe(1, 'old'); s.complete(1, True)
        self.assertEqual(s.pending[1]['token'], 'old')
        s.retain(set())
        self.assertFalse(s.pending)

    def test_captured_branch_heads_replace_stale_catalog_and_ignore_titles(self):
        repo = dict(source='https://github.com/example/app', branch='', all_branches=1,
                    open_prs=1, targets='[]', include_working=0, rules='')
        catalog = dict(default='main', branches=[dict(name='main', head='old'), dict(name='feature', head='old')])
        pulls = [dict(number=1, head='pr', base='main')]
        token = captured_token(repo, catalog, {'head':'main-new'},
                               {'views':[dict(id='branch:feature', head='feature-new')]}, pulls)
        latest = dict(default='main', branches=[dict(name='main', head='main-new'), dict(name='feature', head='feature-new')])
        self.assertEqual(token, revision_token(repo, latest, [(1,'pr','main')]))
        latest['branches'][0]['head'] = 'newer'
        self.assertNotEqual(token, revision_token(repo, latest, [(1,'pr','main')]))
