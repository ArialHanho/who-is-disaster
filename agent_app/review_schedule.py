"""Coalesce Git revisions without cancelling a review already in flight."""
import time

QUIET_SECONDS = 90
MIN_INTERVAL = 300
MAX_WAIT = 600


class ReviewSchedule:
    def __init__(self, state=None, clock=time.time):
        state = state or {}
        self.clock = clock
        self.baselines = state.get('baselines', {})
        self.started = state.get('started', {})
        self.retry_after = state.get('retry_after', {})
        self.failures = state.get('failures', {})
        self.pending, self.active, self.seen = {}, {}, {}

    def state(self):
        return dict(version=2, baselines=self.baselines, started=self.started,
                    retry_after=self.retry_after, failures=self.failures)

    def observe(self, rid, token):
        self.seen[rid] = token
        if token == self.active.get(rid):
            self.pending.pop(rid, None)
            return
        if rid not in self.active and token == self.baselines.get(str(rid)):
            self.pending.pop(rid, None)
            return
        previous = self.pending.get(rid)
        if previous and previous['token'] == token:
            return
        now = self.clock()
        self.pending[rid] = dict(token=token, first=previous['first'] if previous else now, changed=now)

    def ready(self, rid):
        item, now = self.pending.get(rid), self.clock()
        return bool(item and rid not in self.active
                    and now >= self.retry_after.get(str(rid), 0)
                    and now >= self.started.get(str(rid), 0) + MIN_INTERVAL
                    and (now-item['changed'] >= QUIET_SECONDS or now-item['first'] >= MAX_WAIT))

    def begin(self, rid):
        item = self.pending.pop(rid, None)
        self.active[rid] = item['token'] if item else self.seen.get(rid)
        self.started[str(rid)] = self.clock()

    def complete(self, rid, success, token=None, *, reviewed=True):
        active = self.active.pop(rid, None)
        key, now = str(rid), self.clock()
        if success:
            self.failures.pop(key, None)
            self.retry_after.pop(key, None)
            completed = (token or active) if reviewed else None
            if completed:
                self.baselines[key] = completed
                if self.pending.get(rid, {}).get('token') == completed:
                    self.pending.pop(rid, None)
            if not reviewed and active and rid not in self.pending:
                self.observe(rid, active)
        else:
            count = self.failures.get(key, 0) + 1
            self.failures[key] = count
            self.retry_after[key] = now + min(1800, MIN_INTERVAL * 2**min(count-1, 3))
            if active and rid not in self.pending:
                self.observe(rid, active)

    def retain(self, ids):
        for values in (self.pending, self.seen):
            for rid in list(values):
                if rid not in ids:
                    values.pop(rid)
