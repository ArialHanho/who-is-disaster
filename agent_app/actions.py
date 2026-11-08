"""Character actions reflect actual app work, including overlapping jobs."""
import time
import uuid

ACTION_NAMES = {
    "idle": "기다리는 중", "searching": "자료 찾는 중", "thinking": "생각하는 중",
    "organizing": "기록 정리 중", "success": "정리했어요", "error": "확인이 필요해요",
    "attention": "새 소식이 있어요", "sleep": "쉬는 중", "greeting": "안녕하세요", "dragging": "이동 중", "happy": "좋아요!",
}


class ActionState:
    def __init__(self, clock=time.monotonic, sleep_after=300):
        self.clock, self.sleep_after = clock, sleep_after
        self.last_interaction = clock()
        self.activities = {}
        self.transient = None

    def touch(self):
        self.last_interaction = self.clock()

    def begin(self, action):
        if action not in {"searching", "thinking", "organizing"}:
            raise ValueError("Unknown working action")
        self.touch()
        token = uuid.uuid4().hex
        self.activities[token] = action
        return token

    def finish(self, token, failed=False, quiet=False):
        if token not in self.activities:
            return
        del self.activities[token]
        if not quiet:
            self.pulse("error" if failed else "success", 3 if failed else 1.5)

    def update(self, token, action):
        if token in self.activities and action in {"searching", "thinking", "organizing"}:
            self.activities[token] = action

    def pulse(self, action, seconds=1.5):
        if action not in ACTION_NAMES:
            raise ValueError("Unknown character action")
        self.touch()
        self.transient = (action, self.clock() + seconds)

    @property
    def state(self):
        active = self.transient and self.clock() < self.transient[1]
        if active and self.transient[0] in {"error", "dragging", "happy", "greeting", "attention"}:
            return self.transient[0]
        if self.activities:
            return next(reversed(self.activities.values()))
        if active:
            return self.transient[0]
        return "sleep" if self.clock() - self.last_interaction >= self.sleep_after else "idle"
