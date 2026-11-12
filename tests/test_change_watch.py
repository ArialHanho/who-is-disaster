import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThreadPool
from agent_app.review_service import ReviewService
from agent_app.review_source import git
from agent_app.review_ui import ReviewPanel
from agent_app.review_watch import ChangeWatcher, revision
from tests.test_review import offline_actions


class ChangeWatchTests(unittest.TestCase):
    def setUp(self):
        for name in ('QUIET_SECONDS', 'MIN_INTERVAL'):
            mocked = patch('agent_app.review_schedule.'+name, 0)
            mocked.start(); self.addCleanup(mocked.stop)

    def test_local_commit_triggers_review_but_unchanged_refs_do_not(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root/'repo'; repo.mkdir()
            git(repo,'init','-b','main',isolated=True)
            (repo/'app.py').write_text('value = 1\n')
            git(repo,'add','app.py',isolated=True)
            git(repo,'commit','-m','first',isolated=True)
            calls = []
            def analyze(data,*_):
                calls.append(data['head'])
                return {'findings':[], 'ai_error':''}
            service = ReviewService(root/'db', analyze, offline_actions)
            service.save_repo(str(repo),auto=True,all_branches=True)
            panel = ReviewPanel(root,service); panel.timer.stop()
            def until(check):
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    app.processEvents()
                    if check() and not panel.busy and not panel.watcher.workers: return
                    time.sleep(.02)
                self.fail('Git change was not processed')
            try:
                panel.watcher.poll()
                until(lambda:len(calls)==1)
                panel.watcher.local_event()
                panel.watcher.debounce.stop(); panel.watcher.poll()
                until(lambda:not panel.watcher.pending)
                self.assertEqual(len(calls),1)
                (repo/'app.py').write_text('value = 2\n')
                git(repo,'add','app.py',isolated=True)
                git(repo,'commit','-m','second',isolated=True)
                until(lambda:len(calls)==2)
                self.assertNotEqual(calls[0],calls[1])
                self.assertFalse(panel.isVisible())
            finally:
                panel.timer.stop();panel.watcher.close()
                QThreadPool.globalInstance().waitForDone()
                panel.deleteLater();app.processEvents()

    def test_saved_uncommitted_change_is_detected_and_matches_capture(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main", isolated=True)
            file = repo / "app.py"
            file.write_text("value = 1\n")
            (repo / ".gitattributes").write_text("*.py filter=fixture\n")
            git(repo, "add", "app.py", ".gitattributes", isolated=True)
            git(repo, "commit", "-m", "fixture", isolated=True)
            git(repo, "config", "filter.fixture.clean", "false", isolated=True)
            git(repo, "config", "filter.fixture.required", "true", isolated=True)
            service = ReviewService(root / "db", lambda *_: {"findings": [], "ai_error": ""}, offline_actions)
            rid = service.save_repo(str(repo), auto=True, include_working=True)
            before = revision(service.repo(rid))
            file.write_text("value = 2\n")
            after = revision(service.repo(rid))
            self.assertNotEqual(before, after)
            self.assertEqual(service.review(rid)["watch_token"], after)
            self.assertEqual(revision(service.repo(rid)), after)

    def test_unselected_local_worktree_is_outside_watch_and_review(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            git(repo, "init", "-b", "main", isolated=True)
            (repo / "app.py").write_text("value = 1\n")
            git(repo, "add", "app.py", isolated=True)
            git(repo, "commit", "-m", "fixture", isolated=True)
            draft = root / "draft"
            git(repo, "worktree", "add", "-b", "draft", str(draft), isolated=True)
            service = ReviewService(root / "db", lambda *_: {"findings": [], "ai_error": ""}, offline_actions)
            rid = service.save_repo(str(repo), auto=True, include_working=True, all_branches=False)
            before = revision(service.repo(rid))
            (draft / "app.py").write_text("value = 2\n")
            self.assertEqual(revision(service.repo(rid)), before)
            result = service.review(rid)
            self.assertEqual(result["watch_token"], before)
            self.assertEqual(sum(v["kind"] == "worktree" for v in result["sources"].values()), 1)
            self.assertTrue(all(not v["label"].startswith("draft") for v in result["sources"].values()))

    def test_remote_probe_uses_refs_and_pr_heads_and_remembers_success(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            service=ReviewService(temp)
            rid=service.save_repo('https://github.com/example/app',auto=True,all_branches=True,open_prs=True)
            repo=service.repo(rid)
            catalog={'default':'main','branches':[{'name':'main','head':'a'*40}]}
            pulls=[{'number':7,'head':'b'*40,'base':'main','title':'Before'}]
            with patch('agent_app.review_watch.branches',return_value=catalog), patch('agent_app.review_watch.github_account.open_pulls',return_value=pulls):
                first=revision(repo)
                pulls[0]['title']='After'
                self.assertEqual(first,revision(repo))
                pulls[0]['head']='c'*40
                second=revision(repo)
                self.assertNotEqual(first,second)
            watcher=ChangeWatcher(service)
            watcher.repos={rid:repo}
            queued=[];watcher.changed.connect(queued.append)
            watcher.detected(rid,first);watcher.started(rid);watcher.completed(rid,True)
            watcher.detected(rid,first)
            self.assertEqual(queued,[rid])
            watcher.detected(rid,second)
            self.assertEqual(queued,[rid,rid])
            watcher.started(rid)
            watcher.completed(rid,False)
            self.assertEqual(watcher.baselines[str(rid)],first)
            watcher.close();watcher.deleteLater();app.processEvents()

    def test_new_unchecked_branch_pr_does_not_schedule_a_review(self):
        repo = dict(source='https://github.com/example/app', branch='', all_branches=0, open_prs=1,
                    targets='["dev"]', include_working=0, rules='')
        catalog = {'default': 'main', 'branches': [
            {'name': 'main', 'head': 'a'*40}, {'name': 'dev', 'head': 'b'*40}, {'name': 'new', 'head': 'c'*40},
        ]}
        pulls = [{'number': 7, 'head': 'c'*40, 'base': 'main', 'head_branch': 'new', 'same_repository': True}]
        with patch('agent_app.review_watch.branches', return_value=catalog), patch(
                'agent_app.review_watch.github_account.open_pulls', return_value=pulls):
            before = revision(repo)
            pulls[0]['head'] = 'd'*40
            self.assertEqual(revision(repo), before)
            pulls[0]['same_repository'] = False
            self.assertNotEqual(revision(repo), before)


if __name__=='__main__': unittest.main()
