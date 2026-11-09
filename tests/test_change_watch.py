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


if __name__=='__main__': unittest.main()
