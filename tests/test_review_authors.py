import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from agent_app.review_authors import AuthorLookup
from agent_app.review_source import git


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'; self.repo.mkdir()
        git(self.repo, 'init', '-b', 'main')
        self.lookup = AuthorLookup(self.root / 'git-cache', self.root / 'authors')
        self.repo_info = {'source': str(self.repo), 'branch': 'main'}
        self.commit('value = 1\nother = 0\n', 'Alice')
        self.first = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        self.commit('value = 1\nother = 2\n', 'Bob')
        self.head = git(self.repo, 'rev-parse', 'HEAD').decode().strip()

    def tearDown(self): self.temp.cleanup()

    def commit(self, code, name):
        (self.repo / 'app.py').write_text(code, encoding='utf-8')
        git(self.repo, 'add', '--', 'app.py')
        git(self.repo, '-c', 'user.name=' + name, '-c', 'user.email=' + name.lower() + '@example.test',
            '-c', 'commit.gpgsign=false', 'commit', '-m', 'change')

    def finding(self, **extra):
        return {'head': self.head, 'evidence': [{'path': 'app.py', 'line': 1, 'quote': 'value = 1'}], **extra}

    def test_line_author_is_not_unrelated_latest_committer(self):
        rows = self.lookup.lookup(self.repo_info, self.finding())
        self.assertEqual(rows[0]['author_name'], 'Alice')
        self.assertEqual(rows[0]['sha'], self.first)

    def test_parallel_evidence_tracks_each_branch_and_uncommitted_stays_unknown(self):
        sources = {'base': {'head': self.first, 'label': 'main', 'kind': 'base'},
                   'branch:feature': {'head': self.head, 'label': 'feature', 'kind': 'branch'},
                   'worktree:local': {'head': self.head, 'label': 'local', 'kind': 'worktree'}}
        evidence = [{'view': 'base', 'path': 'app.py', 'line': 1, 'quote': 'value = 1'},
                    {'view': 'branch:feature', 'path': 'app.py', 'line': 2, 'quote': 'other = 2'},
                    {'view': 'worktree:local', 'path': 'app.py', 'line': 1, 'quote': 'value = 3'}]
        rows = self.lookup.lookup(self.repo_info, self.finding(sources=sources, evidence=evidence))
        self.assertEqual([r.get('author_name') for r in rows], ['Alice', 'Bob', None])
        self.assertIn('미커밋', rows[2]['note'])

    def test_shallow_boundary_is_not_misattributed(self):
        shallow = self.root / 'shallow'
        git(self.root, 'clone', '--depth=1', self.repo.as_uri(), str(shallow))
        rows = self.lookup.lookup({'source': str(shallow)}, self.finding())
        self.assertNotIn('author_name', rows[0])
        self.assertIn('이력의 경계', rows[0]['note'])

    def test_missing_quote_and_ambiguous_combined_line_do_not_assign_author(self):
        finding = self.finding(evidence=[{'path': 'app.py', 'line': 1, 'quote': 'never here'}])
        self.assertNotIn('author_name', self.lookup.lookup(self.repo_info, finding)[0])
        self.commit('same = 1\nsame = 1\n', 'Carol')
        head = git(self.repo, 'rev-parse', 'HEAD').decode().strip()
        finding = self.finding(sources={'base': {'head': head, 'kind': 'base'}},
                              evidence=[{'view': 'combined', 'path': 'app.py', 'line': 2, 'quote': 'same = 1'}])
        self.assertNotIn('author_name', self.lookup.lookup(self.repo_info, finding)[0])

    def test_account_mapping_keeps_author_and_committer_separate_and_reuses_cache(self):
        data = {'sha': self.first, 'author': {'login': 'alice', 'id': 11, 'html_url': 'https://github.com/alice'},
                'committer': {'login': 'github-actions[bot]', 'id': 22, 'html_url': 'https://github.com/apps/github-actions'}}
        with patch('agent_app.review_authors.github_account._run', return_value=SimpleNamespace(stdout=json.dumps(data).encode())) as api, \
             patch.object(self.lookup, '_avatar', return_value='photo.img'):
            people = self.lookup._accounts('https://github.com/example/app', self.first)
            self.assertEqual(people['author']['login'], 'alice')
            self.assertEqual(people['committer']['url'], 'https://github.com/apps/github-actions')
            self.assertEqual(self.lookup._accounts('https://github.com/example/app', self.first), people)
            api.assert_called_once()

    def test_author_card_groups_repeated_accounts_and_keeps_records_expandable(self):
        import os
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication, QLabel, QPushButton
        from agent_app.authors_ui import AuthorDetails, Avatar
        from agent_app.review_theme import load_fonts
        app = QApplication.instance() or QApplication([])
        load_fonts()
        row = {'path': 'app.py', 'line': 1, 'view': 'main', 'sha': self.first, 'author_name': 'Alice',
               'author': {'login': 'alice', 'url': 'https://github.com/alice'}, 'committer_name': 'Integrator',
               'commit_url': 'https://github.com/example/app/commit/' + self.first}
        widget = AuthorDetails(SimpleNamespace(workers=[]), {}, {})
        widget.received([row, {**row, 'line': 2}])
        self.assertEqual(len(widget.findChildren(Avatar)), 1)
        self.assertTrue(any(b.text() == '변경 기록 2개 보기' for b in widget.findChildren(QPushButton)))
        with patch('agent_app.authors_ui.QDesktopServices.openUrl') as opened:
            next(b for b in widget.findChildren(QPushButton) if b.text() == '프로필').click()
            self.assertEqual(opened.call_args.args[0].toString(), 'https://github.com/alice')
        widget.toggle_records()
        self.assertTrue(any('커밋 기록자: Integrator' == l.text() for l in widget.findChildren(QLabel)))
        with patch('agent_app.authors_ui.QDesktopServices.openUrl') as opened:
            next(b for b in widget.findChildren(QPushButton) if b.text().startswith('커밋 ')).click()
            self.assertEqual(opened.call_args.args[0].toString(), row['commit_url'])
        widget.deleteLater(); app.processEvents()


if __name__ == '__main__': unittest.main()
