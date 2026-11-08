from copy import deepcopy
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QLabel
from agent_app.review_analysis import integration_review, repair_prompt, make_context, static_review, validate_ai
from agent_app.review_service import ReviewService
from agent_app.review_source import allowed_file, GitSource, git
from agent_app.github_actions import finding_execution
from agent_app.review_parallel import sources_metadata
from agent_app.review_ui import ReviewPanel, CONFIG
from agent_app.character import DesktopCharacter
from tests.test_review import snapshot, finding, offline_actions


class ScopeEvidenceTests(unittest.TestCase):
    def test_dummy_design_and_cycle_alert_once_while_feature_notices_stay_quiet(self):
        with tempfile.TemporaryDirectory() as temp:
            service=ReviewService(temp)
            rid=service.save_repo('https://github.com/example/app')
            categories=['placeholder_code','overengineering','circular_dependency']
            findings=[finding(c) for c in categories]+[finding('feature')]
            data=snapshot({'app.py':'value = 1'})
            service.record(service.repo(rid),data,'one',{'findings':findings})
            notice=service.pending_notice(rid)
            self.assertEqual({f['category'] for f in notice['findings']},set(categories))
            service.mark_notified(notice)
            service.record(service.repo(rid),data,'two',{'findings':findings})
            self.assertEqual(service.pending_notice(rid)['findings'],[])

    def test_dummy_report_requires_real_code_evidence(self):
        data=snapshot({'app.py':'value = 1'})
        context=make_context(data,static_review(data),'')
        item=finding('placeholder_code')
        self.assertEqual(len(validate_ai({'summary':'','findings':[item]},data,context)[0]),1)
        item['evidence'][0]['quote']='not in this code'
        self.assertEqual(validate_ai({'summary':'','findings':[item]},data,context)[0],[])

    def test_large_hashed_lock_keeps_real_version_lines_in_ai_context(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, 'init', isolated=True)
            lines = ['# generated lock'] + ['    --hash=sha256:'+'a'*64+' \\']*1400 + ['python-dateutil==2.9.0.post0 \\']
            body = '\n'.join(lines)
            (root/'requirements.lock').write_text(body, encoding='utf-8')
            (root/'requirements.txt').write_text('python-dateutil>=3,<4\n', encoding='utf-8')
            git(root, 'add', '--', 'requirements.lock', 'requirements.txt', isolated=True)
            git(root, 'commit', '-m', 'fixture', isolated=True)
            source = GitSource(root/'cache')
            data = source.read(source.prepare(str(root)))
            self.assertEqual(data['files']['requirements.lock'].splitlines(), body.splitlines())
            data['changed'] = ['requirements.txt']
            context = make_context(data, static_review(data), '')
            lock = next(f for f in context['files'] if f['path']=='requirements.lock')
            self.assertIn('1402: python-dateutil==2.9.0.post0', lock['code'])
            self.assertNotIn('--hash', lock['code'])
            self.assertTrue(lock['truncated'])


    def test_virtual_code_stays_unexecuted_while_related_branch_failure_is_visible(self):
        issue = {'snapshot_id':'combined', 'sources':{'branch:dep':{'head':'a'*40,'kind':'branch'}},
                 'evidence':[{'view':'combined'},{'view':'branch:dep'}]}
        report = {'commits':{'a'*40:{'state':'failed','tests':'테스트 단계 실패'}}}
        status = finding_execution(issue, report)
        self.assertIn('가상 통합본은 미실행', status)
        self.assertIn('관련 커밋: Actions 실패', status)

    def test_full_scope_is_one_off_and_includes_every_branch_and_open_pr(self):
        with tempfile.TemporaryDirectory() as temp:
            service = ReviewService(temp, lambda *a: {"findings": [], "ai_error": ""}, offline_actions)
            rid = service.save_repo("https://github.com/example/app")
            base = {**snapshot({"app.py":"value = 1"}), "label":"main", "kind":"base"}
            feature = {**base, "id":"branch:feature", "kind":"branch", "head":"c"*40}
            pr = {**base, "id":"pr:7", "kind":"pr", "head":"d"*40}
            combined = {**base,"snapshot_id":"all", "composition_complete":True, "views":{"base":base,"branch:feature":feature,"pr:7":pr}}
            with patch("agent_app.review_service.branches", return_value={"default":"main","branches":[{"name":"main"},{"name":"feature"}]}), \
                 patch("agent_app.review_service.github_account.open_pulls", return_value=[{"number":7,"head":"d"*40,"base":"main"}]), \
                 patch("agent_app.review_service.capture", return_value={"base":base,"signature":"all","views":[feature,pr]}) as capture, \
                 patch("agent_app.review_service.compose", return_value=combined):
                result = service.review(rid, all_scope=True)
            self.assertEqual(capture.call_args.args[3], ["feature", "pr:7"])
            self.assertEqual(result["selection"]["pulls"], 1)
            self.assertEqual(service.repo(rid)["all_branches"], 0)
            self.assertEqual(service.repo(rid)["open_prs"], 0)

    def test_manifest_conflict_cites_different_versions_and_only_relevant_refs(self):
        for name in ("requirements.txt", "requirements-dev.txt", "requirements.lock", "uv.lock"):
            self.assertTrue(allowed_file(name), name)
        self.assertFalse(allowed_file("notes.txt"))
        self.assertFalse(allowed_file(".data/requirements.txt"))
        base = {**snapshot({"requirements.txt":"shared==1\npython-dateutil>=2.9,<3\n"}), "label":"main", "kind":"base"}
        branch = {**snapshot({"requirements.txt":"shared==1\npython-dateutil>=3,<4\n"}), "label":"dependency", "kind":"branch"}
        data = {**base, "files":{}, "views":{"base":base,"branch:dependency":branch}, "conflicts":[{"left":["base"],"right":"branch:dependency","paths":["requirements.txt"]}]}
        issue = integration_review(data)["findings"][0]
        self.assertEqual(len(issue["evidence"]), 2)
        self.assertTrue(all(e["line"] == 2 and "python-dateutil" in e["quote"] for e in issue["evidence"]))
        issue["sources"] = {**sources_metadata(data), "branch:unrelated": {"label":"unrelated","head":"f"*40,"kind":"branch"}}
        prompt = repair_prompt({"source":"https://github.com/example/app"}, issue)
        self.assertIn("requirements.txt:2", prompt)
        self.assertNotIn("unrelated", prompt)

    def test_full_scan_reuses_ai_for_pr_title_change_but_rechecks_code_and_base(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def analyze(data, rules):
                calls.append(data["head"])
                return {"findings": [], "ai_error": ""}
            service = ReviewService(temp, analyze, offline_actions)
            rid = service.save_repo("https://github.com/example/app")
            base = {**snapshot({"app.py": "value = 1"}), "label": "main", "kind": "base"}
            pull = {"number": 7, "head": "d"*40, "base": "main", "title": "Before"}
            view = {**base, "id": "pr:7", "kind": "pr", "head": pull["head"]}
            captured = {"base": base, "signature": "all", "views": [view]}
            combined = {**base, "snapshot_id": "all", "composition_complete": True,
                        "views": {"base": base, "pr:7": view}}
            with patch.object(service, "catalog", return_value={"default": "main", "branches": [{"name": "main"}], "pulls": [pull]}), \
                 patch("agent_app.review_service.capture", return_value=captured), \
                 patch("agent_app.review_service.compose", side_effect=lambda *a: deepcopy(combined)), \
                 patch.object(service, "refresh_actions") as actions:
                self.assertFalse(service.review(rid, all_scope=True)["cached"])
                pull["title"] = "Renamed without changing code"
                self.assertTrue(service.review(rid, all_scope=True)["cached"])
                self.assertEqual(len(calls), 1)
                actions.assert_not_called()  # Code review no longer depends on Actions.
                pull["base"] = "release"
                self.assertFalse(service.review(rid, all_scope=True)["cached"])
                pull["head"] = view["head"] = "e"*40
                captured["signature"] = "changed"
                self.assertFalse(service.review(rid, all_scope=True)["cached"])
                self.assertEqual(len(calls), 3)

    def test_new_conflict_evidence_updates_existing_item_without_realert(self):
        with tempfile.TemporaryDirectory() as temp:
            data = snapshot({"requirements.txt":"python-dateutil>=3,<4"})
            issue = {**finding("structural_conflict"), "key":"merge:requirements.txt", "origin":"git-merge-tree", "evidence":[]}
            current = {**issue,"evidence":[{"view":"combined","path":"requirements.txt","line":1,"quote":"python-dateutil>=3,<4"}]}
            service = ReviewService(temp, lambda *a:{"findings":[deepcopy(current)],"ai_error":""}, offline_actions)
            rid=service.save_repo("https://github.com/example/app")
            service.record(service.repo(rid),data,"old",{"findings":[issue]})
            fid=service.findings(rid)[0]["id"]
            service.mark_read(fid)
            with patch.object(service.source,"prepare",return_value=data), patch.object(service.source,"read",return_value=data):
                service.review(rid,force=True)
            rows=service.findings(rid,True)
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]["id"],fid)
            self.assertTrue(rows[0]["evidence"])
            self.assertEqual(service.pending_notice(rid)["findings"],[])


class FooterExperienceTests(unittest.TestCase):
    def test_bottom_content_is_reachable_and_footer_and_character_do_not_overlap(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            panel=ReviewPanel(root)
            panel.timer.stop()
            sprite=DesktopCharacter(CONFIG,root,panel)
            panel.sprite=sprite
            try:
                panel.open_tab("settings")
                sprite.open_panel()
                app.processEvents()
                self.assertFalse(panel.geometry().intersects(sprite.geometry()))
                self.assertLessEqual(panel.scroll.geometry().bottom(), panel.footer.geometry().top())
                self.assertTrue(panel.scroll_more.isVisible())
                for _ in range(12):
                    if not panel.scroll_more.isVisible(): break
                    panel.scroll_more.click()
                    app.processEvents()
                self.assertFalse(panel.scroll_more.isVisible())
                last=next(w for w in panel.findChildren(QLabel) if w.text().startswith("자동 점검과"))
                bottom=last.mapTo(panel.scroll.viewport(),QPoint(0,last.height())).y()
                self.assertLessEqual(bottom,panel.scroll.viewport().height())
                rid = panel.service.save_repo('https://github.com/example/app')
                panel.service.record(panel.service.repo(rid), snapshot({'a.py':'class Empty: pass'}), 'one', {'findings':[finding()]})
                issue = panel.service.findings(rid)[0]
                with patch('sys.excepthook') as error:
                    panel.show_finding(issue)
                    for _ in range(4): app.processEvents()
                    error.assert_not_called()
                heading = next(w for w in panel.findChildren(QLabel) if w.isVisible() and w.property('findingId') == issue['id'])
                self.assertGreaterEqual(heading.mapTo(panel.scroll.viewport(), QPoint()).y(), 0)
                self.assertLess(heading.mapTo(panel.scroll.viewport(), QPoint()).y(), panel.scroll.viewport().height())
                self.assertEqual(panel.repo_id, rid)
            finally:
                sprite.timer.stop();sprite.click_timer.stop();sprite.skin.close()
                for w in (sprite.restore_tab,sprite.bubble,sprite,panel):w.hide();w.deleteLater()
                app.processEvents()


if __name__ == "__main__": unittest.main()
