from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from agent_app.review_analysis import static_review, integration_review, make_context, validate_ai, fingerprint, repair_prompt, finding_policy, batch_repair_prompt
from agent_app.review_service import ReviewService
from agent_app.review_source import GitSource, normalize_source, redact


def snapshot(files, changed=None, head="a"*40, initial=False):
    return {"files": files, "head": head, "base": "b"*40, "web": "https://github.com/example/app", "name": "example/app",
            "changed": list(files) if changed is None else changed, "deleted": [], "diff": "", "omitted": 0,
            "scope": "test", "initial_review": initial}


def finding(category="overengineering", confidence="high"):
    return {"key": "unused-wrapper:app", "title": "중복된 래퍼", "category": category, "severity": "warning",
            "confidence": confidence, "detail": "동작을 추가하지 않는 중복 계층입니다.", "simplification": "기존 함수를 직접 호출하세요.",
            "instruction": "외부 동작을 유지하면서 중복 계층을 줄여줘.", "evidence": [{"path": "app.py", "line": 1, "quote": "value = 1"}]}


def offline_actions(source, targets, previous=None, **kwargs):
    return {"checked": 0, "targets": targets, "commits": {}, "error": "test fixture", "repository": "example/app"}


class AnalysisTests(unittest.TestCase):
    def test_independent_branch_cycles_keep_their_own_source(self):
        data = snapshot({"main.py": "value = 1\n"})
        base = {**data, "label": "main", "kind": "base"}
        isolated = {**snapshot({"a.py": "import b\n", "b.py": "import a\n"}), "label": "docs", "kind": "branch", "independent": True}
        data["views"] = {"base": base, "branch:docs": isolated}
        findings = integration_review(data)["findings"]
        self.assertEqual(len(findings), 1)
        self.assertEqual({e["view"] for e in findings[0]["evidence"]}, {"branch:docs"})
        self.assertIn("docs", findings[0]["detail"])
        isolated["merged"] = True
        self.assertEqual(integration_review(data)["findings"], [])

    def test_python_cycles_include_concrete_edges_but_skip_type_checking(self):
        data = snapshot({"pkg/__init__.py": "", "pkg/a.py": "from . import b\n", "pkg/b.py": "from pkg import a\n"})
        result = static_review(data)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual({e["path"] for e in result["findings"][0]["evidence"]}, {"pkg/a.py", "pkg/b.py"})
        data["files"]["pkg/b.py"] = "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import a\n"
        self.assertEqual(static_review(data)["findings"], [])
        data["files"]["pkg/b.py"] = "def run():\n    from pkg import a\n"
        self.assertEqual(static_review(data)["findings"][0]["confidence"], "medium")

    def test_typescript_relative_cycle_and_type_only_import(self):
        data = snapshot({"src/a.ts": "import { b } from './b';", "src/b.ts": "import { a } from './a.js';"})
        self.assertEqual(len(static_review(data)["findings"]), 1)
        data["files"]["src/b.ts"] = "import type { A } from './a';"
        self.assertEqual(static_review(data)["findings"], [])
        data["files"]["src/b.ts"] = "// import { a } from './a';"
        self.assertEqual(static_review(data)["findings"], [])

    def test_nonruntime_imports_do_not_form_cycles(self):
        for source in (
            'const text = "import x from \'./b\'";',
            'const example = `\nimport x from "./b";\n`;',
            '/*\nimport x from "./b";\n*/',
            'import { type B, type C as D } from "./b";',
            'export { type B } from "./b";',
            'import "./setup"\nexport type { B } from "./b";',
        ):
            with self.subTest(source=source):
                self.assertEqual(static_review(snapshot({'a.ts': source, 'b.ts': 'import a from "./a";'}))['findings'], [])
        for source in (
            'if False:\n    import b\n',
            'while 0:\n    import b\n',
            'from typing import TYPE_CHECKING as TC\nif TC:\n    import b\n',
            'import typing as t\nif t.TYPE_CHECKING:\n    import b\n',
        ):
            with self.subTest(source=source):
                self.assertEqual(static_review(snapshot({'a.py': source, 'b.py': 'import a\n'}))['findings'], [])

    def test_runtime_imports_remain_visible_with_correct_lines(self):
        for source, line in (
            ('/* example */\nimport {\n type B, value\n} from "./b";', 2),
            ('/* comment */ import b from "./b";', 1),
            ('const example = "import fake from something"; import b from "./b";', 1),
            ('export { value, type B } from "./b";', 1),
        ):
            with self.subTest(source=source):
                f = static_review(snapshot({'a.ts': source, 'b.ts': 'import a from "./a";'}))['findings'][0]
                self.assertEqual(f['confidence'], 'high')
                self.assertEqual(next(e['line'] for e in f['evidence'] if e['path'] == 'a.ts'), line)
        for source in (
            'from typing import TYPE_CHECKING as TC\nif TC:\n    pass\nelse:\n    import b\n',
            'from typing import TYPE_CHECKING as TC\nTC = True\nif TC:\n    import b\n',
            'from typing import TYPE_CHECKING as TC\nif enabled:\n    TC = True\nif TC:\n    import b\n',
        ):
            self.assertEqual(static_review(snapshot({'a.py': source, 'b.py': 'import a\n'}))['findings'][0]['confidence'], 'high')
        result = static_review(snapshot({'a.js': 'function run() { return require("./b"); }', 'b.js': 'const a = require("./a");'}))
        self.assertEqual(result['findings'][0]['confidence'], 'medium')
        shadowed = 'from typing import TYPE_CHECKING as TC\ndef run(TC):\n    if TC:\n        import b\n'
        self.assertEqual(static_review(snapshot({'a.py': shadowed, 'b.py': 'import a\n'}))['findings'][0]['confidence'], 'medium')

    def test_ai_needs_distinct_counterpart_evidence_for_alerts(self):
        data = snapshot({'app.py': 'value = 1\nprint(value)\n'})
        context = make_context(data, static_review(data), '')
        f = finding('missing_change')
        f['assessment'] = {'trigger': '값을 사용할 때', 'impact': '잘못된 값을 사용해요.', 'intent': '올바른 값을 출력해요.', 'applicability': 'normal', 'basis': 'runtime'}
        f['evidence'][0]['role'] = 'implementation'
        checked = validate_ai({'summary': '', 'findings': [deepcopy(f)]}, data, context)[0][0]
        self.assertEqual(checked['confidence'], 'medium')
        self.assertIn('review_reason', checked)
        self.assertIn('검토 후보', repair_prompt({'source': 'example/app'}, checked))
        f['evidence'].append({'path': 'app.py', 'line': 2, 'quote': 'print(value)', 'role': 'caller'})
        checked = validate_ai({'summary': '', 'findings': [deepcopy(f)]}, data, context)[0][0]
        self.assertEqual(checked['confidence'], 'high')
        duplicate = {**finding(), 'origin': 'codex'}
        duplicate['evidence'].append({**duplicate['evidence'][0], 'view': 'base'})
        self.assertEqual(finding_policy(duplicate)['confidence'], 'medium')
        duplicate['evidence'][1] = {'path': 'app.py', 'line': 2, 'quote': '# TODO simplify'}
        self.assertEqual(finding_policy(duplicate)['confidence'], 'medium')

    def test_integration_conflict_copy_is_conditional(self):
        f = finding('structural_conflict')
        f.update(origin='git-merge-tree', key='merge:app.py')
        shown = finding_policy(f)
        self.assertTrue(shown['integration_only'])
        self.assertIn('함께 병합하면', shown['title'])
        self.assertTrue(repair_prompt({'source': 'example/app'}, shown).startswith('먼저 관련 브랜치들이 함께 반영할 변경인지'))

    def test_ai_rejects_invented_evidence_and_initial_feature(self):
        data = snapshot({"app.py": "value = 1\n"}, initial=True)
        context = make_context(data, static_review(data), "")
        f = finding()
        accepted, rejected = validate_ai({"summary": "", "findings": [deepcopy(f)]}, data, context)
        self.assertEqual(len(accepted), 1)
        f["evidence"][0]["quote"] = "made up"
        self.assertEqual(validate_ai({"summary": "", "findings": [f]}, data, context)[1], 1)
        self.assertEqual(validate_ai({"summary": "", "findings": [finding("feature")]}, data, context)[1], 1)
        data["initial_review"] = False
        self.assertEqual(len(validate_ai({"summary": "", "findings": [finding("feature")]}, data, context)[0]), 1)

    def test_prompt_is_grounded_and_has_current_commit_check(self):
        f = finding()
        f["head"] = "a"*40
        prompt = repair_prompt({"source": "https://github.com/example/app"}, f)
        self.assertIn("app.py:1", prompt)
        self.assertIn("검토 커밋과 달라졌다면", prompt)
        self.assertEqual(repair_prompt({"source": "x"}, finding("feature")), "")

    def test_bulk_prompt_groups_repos_and_excludes_handled_findings(self):
        repo = {'id': 1, 'name': 'one/app', 'source': 'https://github.com/one/app'}
        f = {**finding(), 'id': 1, 'state': 'open', 'disposition': 'open'}
        candidate = {**f, 'id': 2, 'state': 'uncertain', 'confidence': 'medium'}
        entries = [(repo, f), (repo, f), (repo, candidate)]
        entries += [(repo, {**f, 'id': 3, 'state': 'resolved'}), (repo, {**f, 'id': 4, 'disposition': 'dismissed'})]
        entries += [({**repo, 'id': 2, 'name': 'two/app'}, {**f, 'id': 5})]
        prompt = batch_repair_prompt(entries)
        self.assertEqual(prompt.count('### 항목 #1\n'), 1)
        self.assertIn('검토 후보', prompt)
        self.assertNotIn('### 항목 #3', prompt)
        self.assertNotIn('### 항목 #4', prompt)
        self.assertIn('## two/app', prompt)


    def test_source_accepts_only_github_or_existing_git_and_redacts_secrets(self):
        self.assertEqual(normalize_source("owner/repo")[0], "https://github.com/owner/repo")
        for source in ("https://evil.test/x/y", "https://user:pass@github.com/x/y", "https://github.com/x/y/pull/2"):
            with self.assertRaises(ValueError):
                normalize_source(source)
        self.assertNotIn("abcdefghijk", redact('api_key = "abcdefghijk"'))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.calls, self.output = [], [finding()]
        def analyzer(data, rules):
            self.calls.append((data, rules))
            return {"findings": deepcopy(self.output), "summary": "분석 기록", "ai_error": "", "coverage": {}, "scope": "test"}
        self.analyzer = analyzer
        self.service = ReviewService(self.root / "data", analyzer, offline_actions)
        self.rid = self.service.save_repo("https://github.com/example/app", auto=True)
        self.data = snapshot({"app.py": "value = 1\n"})
        self.patch_prepare = patch.object(GitSource, "prepare", side_effect=lambda *_:dict(self.data))
        self.patch_read = patch.object(GitSource, "read", side_effect=lambda data, _:dict(data))
        self.patch_prepare.start()
        self.patch_read.start()

    def tearDown(self):
        self.patch_prepare.stop()
        self.patch_read.stop()
        self.temp.cleanup()

    def test_cache_survives_restart_and_same_problem_never_realerts(self):
        self.service.review(self.rid)
        notice = self.service.pending_notice(self.rid)
        self.assertEqual(len(notice["findings"]), 1)
        self.service.mark_notified(notice)
        restarted = ReviewService(self.root / "data", self.analyzer, offline_actions)
        self.assertTrue(restarted.review(self.rid)["cached"])
        self.assertEqual(len(self.calls), 1)
        self.data["head"] = "c"*40
        restarted.review(self.rid)
        self.assertEqual(restarted.pending_notice(self.rid)["findings"], [])

    def test_persisted_weak_ai_finding_is_candidate_without_new_ai_call(self):
        self.output = [{**finding(), 'origin': 'codex'}]
        self.service.review(self.rid)
        restarted = ReviewService(self.root / 'data', self.analyzer, offline_actions)
        self.assertEqual(restarted.findings(self.rid)[0]['confidence'], 'medium')
        self.assertEqual(restarted.pending_notice(self.rid)['findings'], [])
        self.assertEqual(len(self.calls), 1)

    def test_focused_recheck_does_not_rescan_or_change_unselected_items(self):
        self.output.append({**finding(), 'key': 'another-issue', 'title': '다른 문제'})
        self.service.review(self.rid)
        first, other = self.service.findings(self.rid)
        signature = self.service.repo(self.rid)['signature']
        self.data = snapshot({'app.py': 'value = 2\n'}, head='c'*40)
        response = {'result': {'checks': [{'id': first['id'], 'state': 'resolved', 'confidence': 'high',
            'resolution_scope': 'repository', 'reason': '원인이 제거됐어요.',
            'evidence': [{'view': 'combined', 'path': 'app.py', 'line': 1, 'quote': 'value = 2'}]}]}}
        with patch('agent_app.codex_provider.status', return_value={'ready': True, 'label': 'ChatGPT'}), patch('agent_app.codex_provider.generate', return_value=response):
            result = self.service.review(self.rid, recheck_ids=[first['id']])
        items = {f['id']: f for f in self.service.findings(self.rid, True)}
        self.assertEqual(items[first['id']]['state'], 'resolved')
        self.assertEqual(items[other['id']]['events'], other['events'])
        self.assertEqual(items[other['id']]['last_seen'], other['last_seen'])
        self.assertEqual(self.service.repo(self.rid)['signature'], signature)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(result['recheck_only'])

    def test_good_change_quiet_and_omission_is_not_a_verified_fix(self):
        self.service.review(self.rid)
        self.service.mark_notified(self.service.pending_notice(self.rid))
        self.output = []
        self.data["head"] = "c"*40
        self.service.review(self.rid)
        self.assertEqual(self.service.pending_notice(self.rid)["findings"], [])
        self.assertEqual(self.service.findings(self.rid)[0]["state"], "open")
        self.assertEqual(self.service.findings(self.rid)[0]["verification"]["method"], "unchanged")

    def test_uncertain_findings_silent_accepted_design_carried_forward(self):
        self.output = [finding(confidence="medium")]
        self.service.review(self.rid)
        self.assertEqual(self.service.pending_notice(self.rid)["findings"], [])
        f = self.service.findings(self.rid)[0]
        self.service.disposition(f["id"], "accepted")
        self.data["head"] = "c"*40
        self.output = [finding()]
        self.service.review(self.rid)
        self.assertEqual(self.service.pending_notice(self.rid)["findings"], [])
        self.assertIn("유지하기로 한 설계", self.calls[-1][1])

    def test_error_alert_once_and_retry_not_cached(self):
        def unavailable(data, rules):
            return {"findings": [], "summary": "AI 미완료", "ai_error": "usage limit", "coverage": {}}
        self.service.analyzer = unavailable
        self.service.review(self.rid)
        notice = self.service.pending_notice(self.rid)
        self.assertEqual(notice["error"], "usage limit")
        self.service.mark_notified(notice)
        self.assertEqual(self.service.pending_notice(self.rid)["error"], "")
        self.service.analyzer = self.analyzer
        self.assertFalse(self.service.review(self.rid)["cached"])


class GitObjectTests(unittest.TestCase):
    def test_reads_committed_objects_excludes_uncommitted_and_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            def run(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
            run("init")
            (repo / "app.py").write_text("value = 1\n")
            (repo / ".env").write_text("PRIVATE=must-not-leak")
            run("add", "app.py", ".env")
            run("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=", "commit", "-m", "initial")
            (repo / "app.py").write_text("uncommitted = True\n")
            source = GitSource(root / "cache")
            data = source.read(source.prepare(str(repo)))
            self.assertEqual(data["files"]["app.py"], "value = 1\n")
            self.assertNotIn(".env", data["files"])


if __name__ == "__main__":
    unittest.main()
