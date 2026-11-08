from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_app.review_source import GitSource, git
from agent_app.review_parallel import capture, compose, parse_targets
from agent_app.review_analysis import integration_review, make_context, validate_ai, repair_prompt, analyze
from agent_app.review_service import ReviewService
from agent_app.review_catalog import branches
from tests.test_review import offline_actions


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_git("init", "-b", "main")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.run_git("config", "core.autocrlf", "false")
        self.write("a.py", "value = 1\n")
        self.write("b.py", "value = 2\n")
        self.write(".gitignore", "ignored.py\n")
        self.commit()
        self.source = GitSource(self.root / "cache")

    def tearDown(self):
        self.temp.cleanup()

    def run_git(self, *args, folder=None):
        return git(folder or self.repo, "-c", "commit.gpgsign=false", *args)

    def write(self, path, content, folder=None):
        target = (folder or self.repo) / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")

    def commit(self):
        self.run_git("add", ".")
        self.run_git("commit", "-m", "fixture")
        return self.run_git("rev-parse", "HEAD").decode().strip()

    def branch(self, name):
        self.run_git("switch", "-c", name, "main")

    def composed(self, targets=(), working=False):
        return compose(self.source, capture(self.source, str(self.repo), "main", list(targets), working))

    def test_branches_form_cycle_only_in_combination_without_touching_index(self):
        self.branch("agent-a")
        self.write("a.py", "import b\nvalue = 1\n")
        self.commit()
        self.branch("agent-b")
        self.write("b.py", "import a\nvalue = 2\n")
        self.commit()
        before = (self.repo / ".git/index").read_bytes()
        head = self.run_git("rev-parse", "HEAD")
        snapshot = self.composed(["agent-a", "agent-b"])
        result = integration_review(snapshot)
        self.assertEqual(len(result["findings"]), 1)
        f = result["findings"][0]
        self.assertIn("함께 합칠 때", f["title"])
        self.assertIn("agent-a", f["detail"])
        self.assertIn("agent-b", f["detail"])
        self.assertEqual((self.repo / ".git/index").read_bytes(), before)
        self.assertEqual(self.run_git("rev-parse", "HEAD"), head)
        self.assertEqual(self.run_git("status", "--porcelain"), b"")
        self.assertTrue(snapshot["composition_complete"])

    def test_conflicting_versions_are_both_available_and_not_parsed_as_merged(self):
        for branch, value in [("left", 10), ("right", 20)]:
            self.branch(branch)
            self.write("a.py", f"value = {value}\n")
            self.commit()
        snapshot = self.composed(["left", "right"])
        self.assertFalse(snapshot["composition_complete"])
        self.assertNotIn("a.py", snapshot["files"])
        self.assertEqual(snapshot["views"]["branch:left"]["files"]["a.py"], "value = 10\n")
        f = integration_review(snapshot)["findings"][0]
        self.assertEqual(f["category"], "structural_conflict")
        self.assertEqual({e["view"] for e in f["evidence"]}, {"base", "branch:left", "branch:right"})

    def test_worktrees_include_staged_unstaged_untracked_and_invalidate_cache(self):
        second = self.root / "worker"
        self.run_git("worktree", "add", "-b", "worker", str(second))
        self.write("a.py", "staged = 3\n")
        self.run_git("add", "a.py")
        self.write("a.py", "saved = 4\n")
        self.write("new.py", "created = True\n")
        self.write("ignored.py", "not_sent = True\n")
        self.write(".env", "PASSWORD=private\n")
        self.write("b.py", "worker_change = True\n", second)
        before = (self.repo / ".git/index").read_bytes()
        snapshot = self.composed(working=True)
        self.assertEqual(snapshot["files"]["a.py"], "saved = 4\n")
        self.assertEqual(snapshot["files"]["b.py"], "worker_change = True\n")
        self.assertIn("new.py", snapshot["files"])
        self.assertNotIn("ignored.py", snapshot["files"])
        self.assertNotIn(".env", snapshot["files"])
        self.assertEqual(before, (self.repo / ".git/index").read_bytes())
        calls = []
        def analyzer(data, rules):
            calls.append(data)
            return {"findings": [], "summary": "", "ai_error": "", "coverage": {}}
        service = ReviewService(self.root / "db", analyzer, offline_actions)
        rid = service.save_repo(str(self.repo), "main", include_working=True)
        service.review(rid)
        self.assertTrue(service.review(rid)["cached"])
        self.write("a.py", "saved = 5\n")
        service.review(rid)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1]["head"], calls[0]["head"])
        self.assertEqual(calls[-1]["files"]["a.py"], "saved = 5\n")
        self.assertEqual(service.pending_notice(rid)["findings"], [])

    def test_multiple_pr_refs_and_view_grounding(self):
        self.run_git("remote", "add", "origin", "https://github.com/example/app.git")
        self.branch("api")
        self.write("api.py", "def save(value, *, scope):\n    return value\n")
        first = self.commit()
        self.branch("ui")
        self.write("ui.py", "from api import save\nsave(1)\n")
        second = self.commit()
        requests = []
        def fetch(reader, web, ref):
            requests.append(ref)
            head = {"refs/pull/12/head": first, "refs/pull/13/head": second}[ref]
            return {"folder": str(self.repo), "head": head, "web": web}
        targets = parse_targets("https://github.com/example/app/pull/12\npr:13\n#12", str(self.repo))
        with patch("agent_app.review_parallel.fetch_ref", side_effect=fetch):
            snapshot = self.composed(targets)
        self.assertEqual(requests, ["refs/pull/12/head", "refs/pull/13/head"])
        context = make_context(snapshot, integration_review(snapshot), "")
        f = {"category": "missing_change", "severity": "problem", "confidence": "high", "key": "save:scope", "title": "scope 누락",
             "detail": "호출 인자 누락", "simplification": "새 호출에 scope를 전달", "instruction": "PR #13의 save 호출에 PR #12의 필수 scope를 전달해줘.",
             "evidence": [{"view": "pr:12", "path": "api.py", "line": 1, "quote": "def save(value, *, scope):"},
                          {"view": "pr:13", "path": "ui.py", "line": 2, "quote": "save(1)"}]}
        self.assertEqual(len(validate_ai({"summary": "", "findings": [deepcopy(f)]}, snapshot, context)[0]), 1)
        f["evidence"][0]["view"] = "pr:13"
        self.assertEqual(validate_ai({"summary": "", "findings": [f]}, snapshot, context)[1], 1)
        with self.assertRaises(ValueError):
            parse_targets("https://github.com/another/app/pull/12", str(self.repo))

    def test_deleted_file_and_stacked_branch_do_not_resurrect_code(self):
        self.branch("first")
        self.write("a.py", "one = 1\n")
        self.commit()
        self.run_git("switch", "-c", "stacked")
        self.write("b.py", "two = 2\n")
        self.commit()
        (self.repo / "a.py").unlink()
        snapshot = self.composed(["first", "stacked"], working=True)
        self.assertNotIn("a.py", snapshot["files"])
        self.assertTrue(snapshot["composition_complete"])
        self.assertEqual(snapshot["files"]["b.py"], "two = 2\n")

    def test_complete_catalog_and_all_branch_scope_include_new_branches(self):
        for i in range(10):
            self.run_git("branch", "agent-" + str(i))
        self.run_git("branch", "기능/한글")
        inventory = branches(str(self.repo))
        self.assertEqual(len(inventory["branches"]), 12)
        calls = []
        def analyzer(data, rules):
            calls.append(data)
            return {"findings": [], "summary": "", "ai_error": "", "coverage": {}}
        service = ReviewService(self.root / "all-db", analyzer, offline_actions)
        rid = service.save_repo(str(self.repo), "main", all_branches=True)
        service.review(rid)
        self.assertEqual(len(calls[0]["views"]), 12)
        self.assertTrue(service.review(rid)["cached"])
        self.run_git("branch", "new-agent")
        service.review(rid)
        self.assertIn("branch:new-agent", calls[-1]["views"])
        self.assertEqual(len(service.catalog(rid)["branches"]), 13)

    def test_many_active_branches_are_all_sent_in_bounded_ai_batches(self):
        data = self.source.read(self.source.prepare(str(self.repo)))
        data["views"] = {"base": {**data, "label": "main", "kind": "base"}}
        for i in range(11):
            path = f"worker{i}.py"
            data["files"][path] = f"value = {i}\n"
            data["views"][f"branch:worker{i}"] = {**data, "views": {}, "files": {path: data["files"][path]}, "changed": [path], "label": f"worker{i}", "kind": "branch"}
        data["changed"] = list(data["files"])
        contexts = []
        def generate(context, *args, **kwargs):
            contexts.append(context)
            return {"result": {"summary": "검토 완료", "findings": []}, "usage": {"input_tokens": 10}}
        with patch("agent_app.review_analysis.codex_provider.status", return_value={"ready": True, "label": "ChatGPT"}), patch("agent_app.review_analysis.codex_provider.generate", side_effect=generate):
            result = analyze(data)
        self.assertEqual(result["ai_error"], "")
        self.assertEqual(len(contexts), 2)
        included = {f["view"] for context in contexts for f in context["files"]}
        self.assertTrue({f"branch:worker{i}" for i in range(11)} <= included)
        self.assertEqual(result["coverage"]["completed_batches"], 2)
        self.assertEqual(result["usage"]["input_tokens"], 20)


if __name__ == "__main__":
    unittest.main()
