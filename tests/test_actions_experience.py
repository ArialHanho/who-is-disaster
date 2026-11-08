from copy import deepcopy
import json
import io
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import uuid
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from agent_app import github_actions as ci
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel, CONFIG
from agent_app.character import DesktopCharacter
from agent_app.single_instance import SingleInstance
from tests.test_review import snapshot, finding


HEAD = "a" * 40
TARGETS = [{"id": "base", "kind": "base", "label": "main", "head": HEAD}]


def raw_run(rid=10, status="completed", conclusion="failure", attempt=1):
    return {"id": rid, "name": "Python CI", "workflow_id": 9, "head_sha": HEAD, "head_branch": "main", "event": "push",
            "status": status, "conclusion": conclusion, "run_attempt": attempt, "updated_at": str(attempt), "pull_requests": []}


def job(conclusion="failure", name="Run tests"):
    return {"id": 100, "name": "unit tests", "status": "completed", "conclusion": conclusion,
            "steps": [{"name": name, "number": 1, "status": "completed", "conclusion": conclusion}]}


class ActionsEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.jobs = [raw_run()], [job()]
        self.calls = []
        def pages(repo, suffix, field):
            self.calls.append(suffix)
            return deepcopy(self.rows if field == "workflow_runs" else self.jobs), True
        self.patches = [patch.object(ci.gh, "signed_in", return_value=True), patch.object(ci, "pages", side_effect=pages),
                        patch.object(ci, "job_log", return_value=("TypeError: encode() missing required keyword-only argument: topic", False)),
                        patch.object(ci.codex_provider, "status", return_value={"ready": False})]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()

    def collect(self, previous=None): return ci.collect("https://github.com/example/app", TARGETS, previous)

    def test_latest_attempt_and_running_run_never_reuse_old_success(self):
        self.rows = [raw_run(rid=9, conclusion="success"), raw_run(status="in_progress", conclusion=None, attempt=2)]
        report = self.collect()
        self.assertEqual(report["commits"][HEAD]["state"], "running")
        self.assertIn("runs/10/attempts/2/jobs", self.calls)
        self.assertEqual(len(report["commits"][HEAD]["runs"]), 1)

    def test_workflows_and_trigger_events_are_not_conflated(self):
        self.rows += [{**raw_run(rid=11, conclusion="success"), "workflow_id": 10},
                      {**raw_run(rid=12, conclusion="success"), "event": "pull_request", "pull_requests": [{"number": 7}]}]
        report = self.collect()
        self.assertEqual(len(report["commits"][HEAD]["runs"]), 3)
        self.assertEqual(report["commits"][HEAD]["state"], "failed")

    def test_failed_jobs_have_grounded_instruction_and_cached_diagnosis(self):
        report = self.collect()
        run = report["commits"][HEAD]["runs"][0]
        prompt = ci.repair_instruction(report, run)
        self.assertIn(HEAD, prompt)
        self.assertIn("TypeError", prompt)
        self.assertIn("Run tests", prompt)
        self.calls.clear()
        self.collect(report)
        self.assertEqual(self.calls, ["runs?head_sha=" + HEAD])

    def test_ai_invented_log_is_rejected(self):
        with patch.object(ci.codex_provider, "status", return_value={"ready": True, "label": "ChatGPT"}), patch.object(ci.codex_provider, "generate", return_value={"result": {
                "cause": "invented", "instruction": "invented", "evidence": [{"job_id": 100, "quote": "not in logs"}]}}):
            run = self.collect()["commits"][HEAD]["runs"][0]
            self.assertEqual(run["diagnosis"]["method"], "steps")

    def test_passed_build_is_not_test_pass_and_virtual_or_old_code_cannot_inherit(self):
        self.rows, self.jobs = [raw_run(conclusion="success")], [job("success", "Build package")]
        report = self.collect()
        self.assertEqual(report["commits"][HEAD]["state"], "passed")
        self.assertEqual(report["commits"][HEAD]["tests"], "테스트 단계 식별 안 됨")
        item = {**finding(), "head": "b"*40}
        self.assertIn("미확인", ci.finding_execution(item, report))
        item.update(head=HEAD, snapshot_id="virtual")
        self.assertIn("가상 통합본은 미실행", ci.finding_execution(item, report))
        item.update(sources={"worktree:x": {"kind": "worktree", "head": HEAD}}, evidence=[{"view": "worktree:x"}])
        self.assertIn("미커밋", ci.finding_execution(item, report))

    def test_green_workflow_with_reported_test_failure_still_has_diagnosis_and_alert(self):
        self.rows = [raw_run(conclusion="success")]
        report = self.collect()
        self.assertEqual(report["commits"][HEAD]["state"], "passed")
        self.assertEqual(report["commits"][HEAD]["tests"], "테스트 단계 실패")
        warned = ci.failed_runs(report)
        self.assertEqual(len(warned), 1)
        self.assertIn("TypeError", ci.repair_instruction(report, warned[0]))

    def test_legacy_actions_refresh_is_explicit_and_not_part_of_code_review(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ReviewService(temp, lambda *a: {"findings": [], "ai_error": ""})
            rid = service.save_repo("https://github.com/example/app")
            data = snapshot({"app.py": "value = 1"})
            with patch.object(service.source, "prepare", return_value=data), patch.object(service.source, "read", return_value=data):
                service.review(rid)
                self.assertEqual(service.actions_report(rid), {})
                service.refresh_actions(rid, TARGETS)
                notice = service.pending_notice(rid)
                self.assertEqual(len(notice["ci"]), 1)
                service.mark_notified(notice)
                self.rows, self.jobs = [raw_run(attempt=2, conclusion="success")], [job("success")]
                self.assertTrue(service.review(rid)["cached"])
                service.refresh_actions(rid, TARGETS)
                self.assertEqual(service.actions_report(rid)["commits"][HEAD]["state"], "passed")
                self.assertEqual(service.pending_notice(rid)["ci"], [])
                self.rows, self.jobs = [raw_run(attempt=3)], [job()]
                service.review(rid)
                service.refresh_actions(rid, TARGETS)
                self.assertEqual(len(service.pending_notice(rid)["ci"]), 1)
                service.disconnect(rid)
                self.assertEqual(service.actions_report(rid), {})

    def test_lookup_failure_cannot_report_stale_pass(self):
        self.rows, self.jobs = [raw_run(conclusion="success")], [job("success")]
        old = self.collect()
        with patch.object(ci, "pages", side_effect=ValueError("access unavailable")):
            now = self.collect(old)
        self.assertTrue(now["error"])
        self.assertEqual(now["commits"], {})
        self.assertIn("미확인", ci.finding_execution({**finding(), "head": HEAD}, now))


class ResidentExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def test_cross_process_duplicate_activates_existing_and_lock_releases(self):
        name = "companion-test-" + uuid.uuid4().hex
        first = SingleInstance(name)
        events = []
        first.activated.connect(lambda:events.append("show"))
        self.assertTrue(first.primary)
        code = "from PySide6.QtWidgets import QApplication; from agent_app.single_instance import SingleInstance; import sys; app=QApplication([]); guard=SingleInstance(sys.argv[1]); print(guard.primary, guard.notify(), flush=True); guard.close()"
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
            child = subprocess.Popen([sys.executable, "-c", code, name], cwd=temp, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                deadline = time.monotonic()+10
                while (child.poll() is None or not events) and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                out, err = child.communicate(timeout=1)
                self.assertEqual(child.returncode, 0, err)
                self.assertEqual(out.strip(), b"False True")
                self.assertEqual(events, ["show"])
            finally:
                if child.poll() is None: child.kill(); child.wait()
                first.close()
        replacement = SingleInstance(name)
        self.assertTrue(replacement.primary)
        replacement.close()

    def test_restore_does_not_toggle_or_overlap_panel_and_details_close_first(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ReviewService(Path(temp)/"db")
            rid = service.save_repo("https://github.com/example/app")
            service.record(service.repo(rid), snapshot({"app.py": "value = 1"}), "one", {"findings": [finding()]})
            panel = ReviewPanel(temp, service)
            panel.timer.stop()
            sprite = DesktopCharacter(CONFIG, Path(temp), panel)
            panel.sprite = sprite
            try:
                sprite.open_panel()
                sprite.restore()
                self.assertTrue(panel.isVisible())
                self.assertFalse(sprite.bubble.isVisible())
                fid = service.findings(rid)[0]["id"]
                panel.toggle_finding(fid)
                self.assertEqual(len(panel.expanded), 1)
                self.assertEqual(service.pending_notice(rid)["findings"], [])
                heading = next(w for w in panel.findChildren(QLabel) if "펼치기 또는 접기" in w.accessibleName())
                QTest.keyClick(heading, Qt.Key.Key_Escape)
                self.assertTrue(panel.isVisible())
                self.assertFalse(panel.expanded)
                QTest.keyClick(panel, Qt.Key.Key_Escape)
                self.assertFalse(panel.isVisible())
            finally:
                sprite.timer.stop(); sprite.click_timer.stop(); sprite.skin.close()
                for w in (sprite.restore_tab, sprite.bubble, sprite, panel): w.hide(); w.deleteLater()
                self.app.processEvents()


class LogAndPaginationTests(unittest.TestCase):
    def test_logs_read_control_codes_into_pipe_then_strip_secrets_and_controls(self):
        proc = Mock()
        proc.stdout = io.BytesIO(b"\x1b[31mError: failed\x1b[0m\npassword=do-not-display\nAuthorization: Bearer abcdefghijk\n")
        proc.wait.return_value = proc.poll.return_value = 0
        with patch.object(ci.gh, "cli_path", return_value=Path("gh.exe")), patch.object(ci.subprocess, "Popen", return_value=proc) as launch:
            log, _ = ci.job_log("example/app", 100)
        self.assertIn("--allow-escape-sequences", launch.call_args.args[0])
        self.assertIn("Error: failed", log)
        self.assertNotIn("\x1b", log)
        self.assertNotIn("do-not-display", log)
        self.assertNotIn("abcdefghijk", log)

    def test_workflow_pagination_is_read_only_and_retains_all_pages(self):
        first = {"total_count": 101, "workflow_runs": [{"id": n} for n in range(100)]}
        second = {"total_count": 101, "workflow_runs": [{"id": 100}]}
        with patch.object(ci.gh, "_run", side_effect=[SimpleNamespace(stdout=json.dumps(d).encode()) for d in [first, second]]) as request:
            rows, complete = ci.pages("example/app", "runs?head_sha="+HEAD, "workflow_runs")
        self.assertTrue(complete)
        self.assertEqual(len(rows), 101)
        self.assertTrue(all(c.args[1:3] == ("--method", "GET") for c in request.call_args_list))


if __name__ == "__main__": unittest.main()
