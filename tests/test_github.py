"""Account discovery, changing PR scope, and repository selection without live credentials."""
from copy import deepcopy
import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch, Mock
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from agent_app import github_account as gh
from agent_app.review_service import ReviewService
from tests.test_review import offline_actions
from agent_app.review_source import git
from agent_app.review_ui import BranchDialog, ReviewPanel, RepoDialog
from agent_app.github_ui import GitHubDialog
from agent_app.review_theme import label


class GitHubAccountTests(unittest.TestCase):
    def test_all_pages_include_private_org_repositories_and_fork_prs(self):
        rows = [{"id": i, "full_name": f"team/repo-{i}", "private": True, "owner": {"login": "team"}} for i in range(205)]
        response = subprocess.CompletedProcess([], 0, json.dumps([rows[:100], rows[100:200], rows[200:]]).encode(), b"")
        with patch.object(gh, "account", return_value={"connected": True, "login": "alice"}), patch.object(gh, "_run", return_value=response) as run:
            result = gh.repositories()
            self.assertEqual(len(result["repositories"]), 205)
            self.assertTrue(result["repositories"][-1]["private"])
            self.assertIn("--paginate", run.call_args.args)
            self.assertIn("--slurp", run.call_args.args)
            self.assertIn("organization_member", run.call_args.args[3])
        pr = {"number": 8, "title": "from fork", "head": {"sha": "c"*40}, "base": {"ref": "release"}, "draft": True}
        with patch.object(gh, "_run", return_value=subprocess.CompletedProcess([], 0, json.dumps([[pr], [{**pr, "number": 9}]]).encode(), b"")):
            pulls = gh.open_pulls("https://github.com/team/app")
        self.assertEqual([p["number"] for p in pulls], [8, 9])
        self.assertEqual(pulls[0]["base"], "release")
        self.assertTrue(pulls[0]["draft"])

    def test_auth_is_scoped_to_github_network_reads_and_errors_hide_secrets(self):
        with patch.dict(os.environ, {"GH_TOKEN": "unrelated-secret", "GITHUB_TOKEN": "another-secret", "GH_DEBUG": "api"}), patch.object(gh, "cli_path", return_value=Path("C:/Program Files/GitHub/gh.exe")), patch.object(gh, "signed_in", return_value=True), patch("agent_app.review_source.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"", b"")) as run:
            git(".", "ls-remote", "--heads", "https://github.com/team/app")
            args, env = run.call_args.args[0], run.call_args.kwargs["env"]
            self.assertTrue(any("auth git-credential" in a for a in args))
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("GH_DEBUG", env)
            self.assertNotIn("--global", args)
            git(".", "rev-parse", "HEAD")
            self.assertFalse(any("credential" in a for a in run.call_args.args[0]))
            git(".", "ls-remote", "https://example.org/team/app")
            self.assertFalse(any("credential" in a for a in run.call_args.args[0]))
        with patch.object(gh, "cli_path", return_value=Path("gh")), patch.object(gh.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"401 private-token-should-not-escape")):
            with self.assertRaisesRegex(ValueError, "GitHub 로그인") as raised:
                gh._run("api", "user")
            self.assertNotIn("private-token", str(raised.exception))


class GitHubScopeTests(unittest.TestCase):
    def test_selected_repos_follow_all_branches_and_open_pr_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            seen, targets = [], []
            def analyzer(data, rules):
                seen.append(deepcopy(data))
                return {"findings": [], "summary": "", "ai_error": "", "coverage": {}}
            service = ReviewService(Path(temp), analyzer, offline_actions)
            selected = [{"source": "https://github.com/team/one"}, {"source": "https://github.com/team/two"}]
            ids = service.connect_github(selected)
            self.assertEqual(ids, service.connect_github(selected))
            self.assertEqual(service.due_repos(), ids)
            for repo in service.repos():
                self.assertEqual((repo["auto"], repo["all_branches"], repo["open_prs"], repo["interval"]), (1, 1, 1, 5))
            inventory = {"default": "main", "branches": [{"name": "main"}, {"name": "agent"}]}
            def pr(n, base="main"):
                return {"number": n, "title": f"PR {n}", "base": base, "head": "b"*40, "draft": False, "url": f"https://github.com/team/one/pull/{n}"}
            pulls = [pr(1), pr(2)]
            base = {"files": {"app.py": "value = 1\n"}, "head": "a"*40, "base": "a"*40, "web": selected[0]["source"], "changed": [], "deleted": [], "label": "main", "kind": "base"}
            def capture(reader, source, branch, selected, working):
                targets.append(selected)
                return {"base": deepcopy(base), "views": [{**deepcopy(base), "id": t if t.startswith("pr:") else "branch:"+t, "head": "b"*40, "kind": "pr" if t.startswith("pr:") else "branch"} for t in selected], "signature": json.dumps(selected)}
            def compose(reader, captured):
                return {**deepcopy(base), "views": {"base": deepcopy(base), **{v["id"]: v for v in captured["views"]}}, "snapshot_id": captured["signature"], "composition_complete": True}
            with patch("agent_app.review_service.branches", side_effect=lambda _:deepcopy(inventory)), patch.object(gh, "open_pulls", side_effect=lambda _:deepcopy(pulls)), patch("agent_app.review_service.capture", side_effect=capture), patch("agent_app.review_service.compose", side_effect=compose):
                service.review(ids[0])
                self.assertEqual(set(targets[-1]), {"agent", "pr:1", "pr:2"})
                self.assertTrue(service.review(ids[0])["cached"])
                self.assertEqual(len(seen), 1)
                pulls[:] = [pr(2), pr(3, "release")]
                inventory["branches"].append({"name": "new-agent"})
                service.review(ids[0])
                self.assertEqual(set(targets[-1]), {"agent", "new-agent", "pr:2", "pr:3"})
                self.assertEqual(seen[-1]["views"]["pr:3"]["base_branch"], "release")
                pulls[1]["base"] = "main"
                self.assertFalse(service.review(ids[0])["cached"])
                pulls[1]["head"] = "c"*40
                with self.assertRaisesRegex(ValueError, "동안 PR이 변경"):
                    service.review(ids[0])
                self.assertEqual(len(seen), 3)
                with patch.object(gh, "open_pulls", side_effect=ValueError("permission unavailable")):
                    with self.assertRaises(ValueError): service.review(ids[0])
                self.assertTrue(service.repo(ids[0])["error"])
                self.assertEqual(len(service.catalog(ids[0])["pulls"]), 2)
                pulls[:] = [{**pr(1), "head_branch": "agent", "same_repository": True},
                            {**pr(2), "head_branch": "agent", "same_repository": False}]
                service.set_scope(ids[0], True, [], excluded=["agent"])
                service.review(ids[0], all_scope=True)
                self.assertEqual(set(targets[-1]), {"new-agent", "pr:2"})


class GitHubPickerTests(unittest.TestCase):
    def test_branch_exclusion_preserves_follow_new_branches_and_reopens_checked(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp, patch.object(BranchDialog, "load"):
            service = ReviewService(Path(temp) / "db")
            rid = service.save_repo("example/app", all_branches=True)
            panel = ReviewPanel(Path(temp), service)
            panel.timer.stop()
            inventory = {"default": "main", "branches": [
                {"name": name, "head": "a" * 40, "subject": ""} for name in ("main", "archive", "feature")]}
            dialog = BranchDialog(panel, rid)
            try:
                dialog.received(inventory)
                dialog.list.item(1).setCheckState(Qt.CheckState.Unchecked)
                self.assertTrue(dialog.all.isChecked())
                dialog.save()
                reopened = BranchDialog(panel, rid)
                inventory["branches"].append({"name": "new", "head": "b" * 40, "subject": ""})
                reopened.received(inventory)
                self.assertEqual(reopened.list.item(1).checkState(), Qt.CheckState.Unchecked)
                self.assertEqual(reopened.list.item(3).checkState(), Qt.CheckState.Checked)
                reopened.deleteLater()
            finally:
                dialog.deleteLater()
                panel.watcher.close()
                panel.deleteLater()
                app.processEvents()

    def test_selection_survives_filters_and_split_device_code_opens_browser_once(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp, patch.object(GitHubDialog, "load"):
            service = ReviewService(Path(temp) / "db")
            panel = ReviewPanel(Path(temp), service)
            panel.timer.stop()
            dialog = GitHubDialog(panel)
            repos = [{"id": i, "name": f"team/repo-{i}", "owner": "team", "description": "", "private": True, "archived": False, "source": f"https://github.com/team/repo-{i}"} for i in range(3)]
            dialog.received({"connected": True, "login": "alice", "repositories": repos})
            dialog.list.item(0).setCheckState(Qt.CheckState.Checked)
            dialog.search.setText("repo-2")
            dialog.select_visible()
            self.assertEqual(dialog.selected, {0, 2})
            dialog.loading = True
            dialog.finished(Mock())
            self.assertTrue(dialog.confirm.isEnabled())
            dialog.auth = Mock()
            dialog.auth.readAllStandardOutput.side_effect = [b"First copy code: ABCD-", b"1234\nOpen browser", b"waiting"]
            with patch("agent_app.github_ui.QDesktopServices.openUrl") as browser:
                for _ in range(3): dialog.login_output()
                browser.assert_called_once()
                self.assertEqual(browser.call_args.args[0].toString(), "https://github.com/login/device")
            self.assertEqual(dialog.code_input.text(), "ABCD-1234")
            dialog.login_finished(0)
            self.assertEqual(dialog.auth_output, "")
            self.assertEqual(dialog.code_input.text(), "")
            dialog.save()
            self.assertEqual(len(service.repos()), 2)
            editor = RepoDialog(panel, service.repo(dialog.saved_ids[0]))
            editor.save()
            self.assertTrue(service.repo(dialog.saved_ids[0])["open_prs"])
            display = label("코드 · PR → ‘수정’ <안내>", 15, display=True)
            self.assertIn("font-family:Malgun Gothic", display.text())
            self.assertIn("&lt;안내&gt;", display.text())
            self.assertFalse(panel.isVisible())
            for widget in (display, editor, dialog, panel): widget.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
