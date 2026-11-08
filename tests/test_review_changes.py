import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agent_app.review_authors import AuthorLookup
from agent_app.review_changes import history, resolution_of
from agent_app.review_source import git


class ChangeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.repo = self.root/'repo'; self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        self.before = self.commit('value = 1\nother = 0\n', 'Alice')
        self.fixed = self.commit('value = 2\nother = 0\n', 'Bob')
        self.after = self.commit('value = 2\nother = 9\n', 'Charlie')
        self.authors = AuthorLookup(self.root/'git', self.root/'authors')
        self.repo_info = {'source': str(self.repo)}
        self.item = {'state': 'resolved', 'head': self.before,
                     'evidence': [{'path': 'app.py', 'line': 1, 'quote': 'value = 1'}],
                     'verification': {'state': 'resolved', 'head': self.after, 'reason': '기본값을 2로 고쳤어요.',
                                      'evidence': [{'path': 'app.py', 'line': 1, 'quote': 'value = 2'}]}}

    def tearDown(self): self.temp.cleanup()

    def commit(self, text, author):
        (self.repo/'app.py').write_text(text, encoding='utf-8')
        git(self.repo, 'add', '--', 'app.py')
        git(self.repo, '-c', 'user.name='+author, '-c', 'user.email='+author+'@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '-m', 'change')
        return git(self.repo, 'rev-parse', 'HEAD').decode().strip()

    def test_problem_writer_fixer_and_checked_head_are_distinct(self):
        with patch('agent_app.codex_provider.generate', side_effect=AssertionError('no AI')):
            result = history(self.repo_info, self.item, self.authors)
        self.assertEqual(result['writers'][0]['author_name'], 'Alice')
        self.assertEqual([r['author_name'] for r in result['fixers']], ['Bob'])
        self.assertEqual(result['fixers'][0]['sha'], self.fixed)
        self.assertEqual(result['confirmed'][0]['sha'], self.after)
        self.assertIn('value = 1', result['files'][0]['before'])
        self.assertIn('value = 2', result['files'][0]['after'])

    def test_deleted_problem_line_still_identifies_actual_fixer(self):
        removed = self.commit('other = 9\n', 'Dora')
        self.item['head'] = self.after
        self.item['evidence'][0]['quote'] = 'value = 2'
        self.item['verification'].update(head=removed, evidence=[])
        result = history(self.repo_info, self.item, self.authors)
        self.assertEqual(result['fixers'][0]['author_name'], 'Dora')
        self.assertEqual(result['files'][0]['before'], ['value = 2'])
        self.assertEqual(result['files'][0]['after'], [])

    def test_uncommitted_fix_never_assigns_head_author_as_fixer(self):
        self.item['sources'] = {'worktree:x': {'head': self.before, 'kind': 'worktree'}}
        self.item['evidence'][0]['view'] = 'worktree:x'
        self.item['verification']['sources'] = {'worktree:x': {'head': self.after, 'kind': 'worktree', 'uncommitted': True}}
        result = history(self.repo_info, self.item, self.authors)
        self.assertEqual(result['fixers'], [])
        self.assertEqual(result['confirmed'], [])
        self.assertTrue(any('미커밋' in note for note in result['notes']))

    def test_unresolved_problem_does_not_claim_a_fixer(self):
        self.item['state'] = 'open'
        result = history(self.repo_info, self.item, self.authors)
        self.assertFalse(result['resolved']); self.assertEqual(result['fixers'], [])

    def test_legacy_resolution_uses_recorded_event_not_latest_check(self):
        self.item['events'] = [{'state': 'resolved', 'head': self.fixed, 'evidence': [], 'stamp': 100}]
        self.assertEqual(resolution_of(self.item)['after']['head'], self.fixed)

    def test_history_frame_exposes_before_after_people_and_commit_without_expanding(self):
        from PySide6.QtWidgets import QApplication, QLabel, QPushButton
        from agent_app.finding_history_ui import FindingHistory
        from agent_app.review_theme import load_fonts
        app = QApplication.instance() or QApplication([]); load_fonts()
        widget = FindingHistory(SimpleNamespace(workers=[]), self.repo_info, self.item)
        widget.received(history(self.repo_info, self.item, self.authors))
        words = '\n'.join(w.text() for w in widget.findChildren(QLabel))
        for text in ('변경 기록', '변경 전', '변경 후', '문제 코드 작성자', '해결한 사람', 'Alice', 'Bob'):
            self.assertIn(text, words)
        self.assertTrue(any('해결 확인 커밋' in w.text() for w in widget.findChildren(QLabel)))
        self.assertNotIn('Actions', words)
        widget.deleteLater(); app.processEvents()


if __name__ == '__main__': unittest.main()
