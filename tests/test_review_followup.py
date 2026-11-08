from copy import deepcopy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from agent_app.review_analysis import static_review, fingerprint
from agent_app.review_followup import verify, tracking, context_for, validate_check, check_result, recover_integrated_views
from agent_app.review_parallel import sources_metadata
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel
from tests.test_review import snapshot, finding, offline_actions


def tracked(data, item=None):
    item = deepcopy(item or finding())
    return {**item, "id": 1, "fingerprint": fingerprint(item), "state": "open", "disposition": "open",
            "head": data["head"], "tracking": tracking(data, item)}


def answer(state="resolved", quote="value = 2", **changes):
    return {"id": 1, "state": state, "confidence": "high", "reason": "이전 원인이 현재 코드에서 제거됐어요.",
            "evidence": [{"view": "combined", "path": "app.py", "line": 1, "quote": quote}], **changes}


class FollowupEvidenceTests(unittest.TestCase):
    def test_timeout_does_not_drop_other_checks_and_completed_checks_are_reused(self):
        from agent_app.codex_provider import ReviewTimeout
        old = snapshot({'app.py':'value = 1\n'})
        issues = [{**tracked(old), 'id':i} for i in range(6)]
        current = snapshot({'app.py':'value = 2\n'}, head='b'*40)
        calls = []
        def generated(context, **kwargs):
            ids = [i['id'] for i in context['issues']]
            calls.append(ids)
            if 0 in ids: raise ReviewTimeout('fixture timeout')
            return {'result':{'checks':[answer('uncertain', id=i) for i in ids]}}
        with tempfile.TemporaryDirectory() as cache:
            checks, error = verify(current, issues, [], generate=generated, cache_dir=cache)
            self.assertTrue(error)
            self.assertTrue(all(checks[i]['method']=='codex' for i in range(1,6)))
            self.assertEqual(checks[0]['state'], 'uncertain')
            calls.clear()
            verify(current, issues, [], generate=generated, cache_dir=cache)
            self.assertTrue(calls)
            self.assertTrue(all(0 in ids for ids in calls))

    def test_many_findings_build_each_source_index_once_per_verification(self):
        from agent_app import review_followup as followup
        old = snapshot({'app.py':'value = 1\n'})
        issues = [{**tracked(old), 'id':i} for i in range(24)]
        current = snapshot({'app.py':'value = 2\n'}, head='b'*40)
        def generated(context, **kwargs):
            return {'result':{'checks':[answer('uncertain', id=i['id']) for i in context['issues']]}}
        with patch.object(followup, 'dependencies', wraps=followup.dependencies) as graph, patch.object(followup, 'symbol_index', wraps=followup.symbol_index) as symbols:
            checks, error = verify(current, issues, [], generate=generated)
            self.assertEqual(len(checks), 24)
            self.assertFalse(error)
            self.assertEqual(graph.call_count, 1)
            self.assertEqual(symbols.call_count, 1)
            verify(current, issues, [], generate=generated)
            self.assertEqual(graph.call_count, 2)
            self.assertEqual(symbols.call_count, 2)

    def test_real_git_ancestry_maps_retired_branch_to_fixed_base(self):
        from tests.test_parallel_review import ParallelTests
        fixture = ParallelTests()
        fixture.setUp()
        try:
            fixture.branch("agent-bug")
            fixture.write("a.py", "import b\n")
            fixture.write("b.py", "import a\n")
            fixture.commit()
            old = fixture.composed(["agent-bug"])
            item = static_review(old["views"]["branch:agent-bug"])["findings"][0]
            item["evidence"] = [{**e, "view": "branch:agent-bug"} for e in item["evidence"]]
            issue = {**tracked(old, item), "sources": sources_metadata(old)}
            fixture.run_git("switch", "main")
            fixture.run_git("merge", "--ff-only", "agent-bug")
            fixture.write("b.py", "value = 2\n")
            fixture.commit()
            now = fixture.source.read(fixture.source.prepare(str(fixture.repo), "main"))
            recover_integrated_views(now, [issue])
            checks, _ = verify(now, [issue], [])
            self.assertEqual(checks[1]["state"], "resolved")
            self.assertEqual(checks[1]["integrated_refs"]["branch:agent-bug"]["base_head"], now["head"])
            self.assertEqual(checks[1]["sources"]["branch:agent-bug"]["head"], now["head"])
        finally:
            fixture.tearDown()

    def test_static_cycle_fix_requires_complete_readable_same_scope(self):
        old = snapshot({"a.py": "import b\n", "b.py": "import a\n"})
        issue = tracked(old, static_review(old)["findings"][0])
        fixed = snapshot({"a.py": "import b\n", "b.py": "value = 1\n"}, head="c"*40)
        checks, error = verify(fixed, [issue], [])
        self.assertEqual(checks[1]["state"], "resolved")
        self.assertEqual(checks[1]["method"], "python-import")
        self.assertFalse(checks[1]["execution_verified"])
        self.assertEqual(checks[1]["evidence"][1]["quote"], "value = 1")
        for incomplete in ({**fixed, "omitted": 1}, snapshot({"a.py": "import b\n", "b.py": "def broken(\n"})):
            self.assertEqual(verify(incomplete, [issue], [])[0][1]["state"], "uncertain")

    def test_removed_or_merged_pr_never_means_fixed(self):
        old = snapshot({"app.py": "value = 1\n"})
        old["views"] = {"base": deepcopy(old), "pr:7": deepcopy(old)}
        item = finding()
        item["evidence"][0]["view"] = "pr:7"
        issue = tracked(old, item)
        now = snapshot({"app.py": "value = 2\n"})
        for views in ({"base": deepcopy(now)}, {"base": deepcopy(now), "pr:7": {**now, "merged": True}}):
            now["views"] = views
            checks, _ = verify(now, [issue], [])
            self.assertEqual(checks[1]["state"], "uncertain")

    def test_merge_fix_requires_original_participants(self):
        old = snapshot({"app.py": "value = 1\n"})
        old["views"] = {"base": deepcopy(old), "branch:a": deepcopy(old), "branch:b": deepcopy(old)}
        old["composition_complete"] = False
        item = finding("structural_conflict")
        item["origin"] = "git-merge-tree"
        item["evidence"][0]["view"] = "branch:a"
        issue = tracked(old, item)
        fixed = deepcopy(old)
        fixed["composition_complete"] = True
        self.assertEqual(verify(fixed, [issue], [])[0][1]["state"], "resolved")
        del fixed["views"]["branch:b"]
        self.assertEqual(verify(fixed, [issue], [])[0][1]["state"], "uncertain")

    def test_semantic_fix_requires_exact_current_quotes_and_full_context(self):
        old = snapshot({"app.py": "value = 1\n"})
        issue = tracked(old)
        now = snapshot({"app.py": "value = 2\n"})
        context = context_for(now, issue)
        self.assertEqual(validate_check(answer(), context, now)["state"], "resolved")
        self.assertIsNone(validate_check(answer(quote="invented"), context, now))
        self.assertIsNone(validate_check(answer(evidence=[]), context, now))
        incomplete = {**context, "complete_context": False}
        self.assertEqual(validate_check(answer(), incomplete, now)["state"], "uncertain")
        self.assertEqual(validate_check(answer(confidence="medium"), context, now)["state"], "uncertain")

    def test_same_source_avoids_ai_and_failed_recheck_stays_pending(self):
        old = snapshot({"app.py": "value = 1\n"})
        issue = tracked(old)
        with patch("agent_app.review_followup.codex_provider.generate", side_effect=AssertionError("no call")):
            self.assertEqual(verify(old, [issue], [])[0][1]["state"], "open")
        now = snapshot({"app.py": "value = 2\n"})
        def failed(*args, **kwargs): raise ValueError("offline")
        checks, error = verify(now, [issue], [], generate=failed)
        self.assertEqual(checks[1]["state"], "uncertain")
        self.assertIn("offline", error)

    def test_local_guard_fix_survives_unrelated_omissions_and_large_repo(self):
        old = snapshot({'app.py': 'rows = load()\ntrain(rows)\n'})
        f = finding('missing_change')
        f['evidence'] = [{'path': 'app.py', 'line': 2, 'quote': 'train(rows)'}]
        issue = tracked(old, f)
        now = snapshot({'app.py': 'rows = load()\nif not rows: raise ValueError("empty")\ntrain(rows)\n',
                        'unrelated.py': '# documentation\n' * 10000}, head='c'*40)
        now['omitted'] = 25
        calls = []
        def generated(context, *args, **kwargs):
            calls.append(context)
            return {'result': {'checks': [answer(quote='if not rows: raise ValueError("empty")', resolution_scope='local',
                evidence=[{'view': 'combined', 'path': 'app.py', 'line': 2, 'quote': 'if not rows: raise ValueError("empty")'}])]}}
        checks, error = verify(now, [issue], [], generate=generated)
        self.assertEqual(checks[1]['state'], 'resolved')
        self.assertFalse(checks[1]['execution_verified'])
        self.assertEqual([f['path'] for f in calls[0]['issues'][0]['current_files']], ['app.py'])
        context = calls[0]['issues'][0]
        result = generated({}, {})['result']['checks'][0]
        self.assertEqual(validate_check({**result, 'resolution_scope': 'related'}, context, now)['state'], 'resolved')
        self.assertEqual(validate_check({**result, 'resolution_scope': 'repository'}, context, now)['state'], 'uncertain')
        oversized = snapshot({'app.py': now['files']['app.py'] + '# tail\n' * 3000})
        self.assertEqual(validate_check(result, context_for(oversized, issue), oversized)['state'], 'uncertain')
        self.assertEqual(error, '')

    def test_related_fix_requires_readable_caller_code(self):
        old = snapshot({'app.py': 'value = 1\n'})
        issue = tracked(old, finding('missing_change'))
        now = snapshot({'app.py': 'value = 2\n', 'caller.py': 'import app\n' + '# large caller\n' * 3000})
        context = context_for(now, issue)
        self.assertIn('caller.py', [f['path'] for f in context['current_files']])
        self.assertEqual(validate_check(answer(resolution_scope='related'), context, now)['state'], 'uncertain')

    def test_global_simplification_cannot_use_local_guard_exception(self):
        old = snapshot({'app.py': 'value = 1\n'})
        issue = tracked(old)
        now = snapshot({'app.py': 'value = 2\n', 'unrelated.py': 'value = 1\n'})
        context = context_for(now, issue)
        self.assertEqual(validate_check(answer(resolution_scope='local'), context, now)['state'], 'uncertain')

    def test_small_followups_batched_and_missing_response_is_pending(self):
        old = snapshot({"app.py": "value = 1\n"})
        first, second = tracked(old), {**tracked(old), "id": 2}
        calls = []
        def generated(context, *args, **kwargs):
            calls.append(context)
            return {"result": {"checks": [answer()]}}
        checks, _ = verify(snapshot({"app.py": "value = 2\n"}), [first, second], [], generate=generated)
        self.assertEqual(len(calls), 1)
        self.assertEqual(checks[1]["state"], "resolved")
        self.assertEqual(checks[2]["state"], "uncertain")

    def test_manual_recheck_cannot_call_unchanged_source_a_fix(self):
        old = snapshot({"app.py": "value = 1\n"})
        generated = lambda *a, **k: {"result": {"checks": [answer(quote="value = 1")]}}
        checks, _ = verify(old, [tracked(old)], [], generate=generated, force=True)
        self.assertEqual(checks[1]["state"], "uncertain")
        self.assertEqual(checks[1]["method"], "unchanged-source")


class FollowupPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data, self.output = snapshot({"app.py": "value = 1\n"}), [finding()]
        self.service = ReviewService(self.root / "db", lambda *a: {"findings": deepcopy(self.output), "summary": "", "ai_error": ""}, offline_actions)
        self.rid = self.service.save_repo("https://github.com/example/app")
        self.patches = [patch.object(self.service.source, "prepare", side_effect=lambda *a:deepcopy(self.data)),
                        patch.object(self.service.source, "read", side_effect=lambda *a:deepcopy(self.data)),
                        patch("agent_app.review_followup.codex_provider.status", return_value={"ready": True, "label": "ChatGPT"})]
        for p in self.patches: p.start()
        self.service.review(self.rid)
        self.service.mark_notified(self.service.pending_notice(self.rid))

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def fix(self):
        self.output = []
        self.data = snapshot({"app.py": "value = 2\n"}, head="c"*40)
        with patch("agent_app.review_followup.codex_provider.generate", return_value={"result": {"checks": [answer()]}}):
            self.service.review(self.rid)

    def test_fix_is_silent_persistent_and_regression_alerts_only_once(self):
        self.fix()
        fixed = self.service.findings(self.rid, True)[0]
        self.assertEqual(fixed["state"], "resolved")
        self.assertEqual(fixed["original"]["evidence"][0]["quote"], "value = 1")
        self.assertEqual(fixed["verification"]["evidence"][0]["quote"], "value = 2")
        self.assertEqual(self.service.findings(self.rid), [])
        self.assertEqual(self.service.pending_notice(self.rid)["findings"], [])
        restarted = ReviewService(self.root / "db", lambda *a: self.fail("cached"))
        self.assertEqual(restarted.findings(self.rid, True)[0]["state"], "resolved")
        self.assertTrue(self.service.review(self.rid)["cached"])
        self.output = [finding()]
        self.data = snapshot({"app.py": "value = 1\n"}, head="d"*40)
        self.service.review(self.rid)
        notice = self.service.pending_notice(self.rid)
        self.assertEqual(len(notice["findings"]), 1)
        self.assertEqual(notice["findings"][0]["reopened_count"], 1)
        self.assertEqual([e["state"] for e in notice["findings"][0]["events"]], ["open", "resolved", "open"])
        self.service.mark_notified(notice)
        self.service.review(self.rid)
        self.assertEqual(self.service.pending_notice(self.rid)["findings"], [])

    def test_provider_error_retries_and_uncertain_remains_in_briefing(self):
        self.output = []
        self.data = snapshot({"app.py": "value = 2\n"}, head="c"*40)
        with patch("agent_app.review_followup.codex_provider.generate", side_effect=ValueError("offline")) as generated:
            self.service.review(self.rid)
            self.assertEqual(self.service.findings(self.rid)[0]["state"], "uncertain")
            self.assertIn("재확인", self.service.repo(self.rid)["error"])
            self.assertFalse(self.service.review(self.rid)["cached"])
            self.assertEqual(generated.call_count, 2)

    def test_first_resolution_commit_survives_later_unrelated_commits(self):
        self.fix()
        first = self.service.findings(self.rid, True)[0]['resolution']
        self.data['head'] = 'e'*40
        with patch('agent_app.review_followup.codex_provider.generate', side_effect=AssertionError('unchanged code needs no AI')):
            self.service.review(self.rid)
        current = self.service.findings(self.rid, True)[0]
        self.assertEqual(current['resolution'], first)
        self.assertEqual(current['resolution']['after']['head'], 'c'*40)
        self.assertEqual(current['resolution']['before']['evidence'][0]['quote'], 'value = 1')
        self.assertEqual(len(current['resolutions']), 1)

    def test_manual_recheck_bypasses_cache_and_card_opens_resolution(self):
        self.fix()
        app = QApplication.instance() or QApplication([])
        panel = ReviewPanel(self.root, self.service)
        panel.timer.stop()
        item = self.service.findings(self.rid, True)[0]
        calls = []
        def review(rid, progress=None, force=False, recheck_ids=None):
            calls.append((rid, force, recheck_ids))
            return {"repo_id": rid, "followup_counts": {"resolved": 1, "uncertain": 0}}
        with patch.object(self.service, "review", side_effect=review):
            panel.recheck_finding(self.service.repo(self.rid), item)
            deadline = time.monotonic() + 5
            while panel.busy and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            QThreadPool.globalInstance().waitForDone()
            app.processEvents()
        self.assertEqual(calls, [(self.rid, True, [item['id']])])
        self.assertEqual(panel.lifecycle, "resolved")
        self.assertIn("현재 코드로 다시 확인", [b.text() for b in panel.findChildren(QPushButton)])
        self.assertNotIn("AI 수정 지시문 복사", [b.text() for b in panel.findChildren(QPushButton)])
        self.assertTrue(any("변경 기록" in w.text() for w in panel.findChildren(QLabel)))
        self.assertFalse(any("실행 판정" in w.text() for w in panel.findChildren(QLabel)))
        self.assertNotIn('ci', panel.tabs)
        panel.deleteLater()
        app.processEvents()


if __name__ == "__main__": unittest.main()
