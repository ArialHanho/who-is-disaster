import unittest
from agent_app.actions import ActionState


class ActionTests(unittest.TestCase):
    def test_overlapping_tasks_and_transient_states(self):
        stamp = [0]
        state = ActionState(clock=lambda: stamp[0], sleep_after=10)
        first = state.begin("searching")
        second = state.begin("thinking")
        state.finish(second)
        self.assertEqual(state.state, "searching")
        state.finish(first, failed=True)
        self.assertEqual(state.state, "error")
        stamp[0] = 4
        self.assertEqual(state.state, "idle")
        stamp[0] = 11
        self.assertEqual(state.state, "sleep")
        state.pulse("greeting")
        self.assertEqual(state.state, "greeting")


if __name__ == "__main__":
    unittest.main()
