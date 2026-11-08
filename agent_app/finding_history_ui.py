"""Always-visible change history: code result, before/after, and separate people."""
from PySide6.QtCore import QThreadPool, QTimer, QUrl, Qt, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout
from .authors_ui import Avatar
from .background import Worker
from .review_changes import history, resolution_of
from .review_theme import BROWN, MUTED, label, button, clear


class FindingHistory(QFrame):
    def __init__(self, panel, repo, finding):
        super().__init__()
        self.panel, self.repo, self.finding = panel, repo, finding
        self.started, self.all_changes = False, False
        self.setObjectName('changeHistory')
        self.setStyleSheet('QFrame#changeHistory{background:#fffbf1;border:1px solid #dec8a8;border-radius:12px;}')
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(14, 14, 14, 14)
        self.content.setSpacing(10)
        self.content.addWidget(label('변경 기록', 15, BROWN, True))
        resolution = resolution_of(finding)
        self.candidate = finding.get('confidence') != 'high' or finding.get('state') in {'uncertain', 'not_seen'}
        self.content.addWidget(label('해결 확인 · 코드 기준' if resolution else '검토 중' if self.candidate else '해결 확인 전', 14, '#39765b' if resolution else '#9a5637', True))
        if resolution and resolution['after'].get('reason'):
            self.content.addWidget(label(resolution['after']['reason'], 13))
        self.content.addWidget(label('작성자와 변경 코드를 가져오고 있어요…', 12, MUTED))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.started:
            self.started = True
            QTimer.singleShot(0, self.load)

    def load(self):
        panel = self.panel
        worker = Worker(lambda: history(self.repo, self.finding, panel.service.authors))
        panel.workers.append(worker)
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(self.failed)
        worker.signals.finished.connect(lambda: panel.workers.remove(worker) if worker in panel.workers else None)
        QThreadPool.globalInstance().start(worker)

    @Slot(str)
    def failed(self, error):
        clear(self.content)
        self.content.addWidget(label('변경 기록', 15, BROWN, True))
        self.content.addWidget(label('수정 이력을 읽지 못했어요. 코드 판정은 위 상태에서 확인할 수 있어요.', 12, MUTED))
        self.content.addWidget(button('이력 다시 가져오기', self.load, role='chip'))

    @Slot(object)
    def received(self, data):
        self.data = data
        self.render()

    def toggle_changes(self):
        self.all_changes = not self.all_changes
        self.render()

    def link(self, text, url):
        if url:
            control = button(text, lambda: QDesktopServices.openUrl(QUrl(url)), role='chip')
            control.setToolTip(url)
            self.content.addWidget(control)
        else: self.content.addWidget(label(text, 12, MUTED))

    def people(self, title, rows, *, commits=False, origin=False):
        self.content.addWidget(label(title, 13, BROWN, True))
        groups, missing = {}, []
        for row in rows:
            if row.get('note'): missing.append(row['note']); continue
            person = row.get('author', {})
            groups.setdefault(person.get('login') or (row.get('sha'), row.get('author_name')), []).append(row)
        for records in groups.values():
            record = records[0]
            person, name = record.get('author', {}), record['author_name']
            row = QHBoxLayout(); row.setSpacing(9)
            row.addWidget(Avatar(name, person.get('avatar', '')), 0, Qt.AlignmentFlag.AlignTop)
            identity = '@' + person['login'] if person and name.casefold() == person['login'].casefold() else name + (' · @' + person['login'] if person else '')
            row.addWidget(label(identity, 13, BROWN, True), 1)
            if person: row.addWidget(button('프로필', lambda url=person['url']: QDesktopServices.openUrl(QUrl(url)), role='icon'))
            self.content.addLayout(row)
            if origin:
                known = {}
                for record in records: known.setdefault(record['sha'], record)
                for record in known.values(): self.link('근거 커밋 ' + record['sha'][:8], record.get('commit_url'))
            if commits:
                for record in records:
                    confirmed = any(c['sha'] == record['sha'] for c in self.data['confirmed'])
                    self.link(('수정·해결 확인 커밋 ' if confirmed else '수정 커밋 ') + record['sha'][:8], record.get('commit_url'))
                    if record.get('committer_name') and record['committer_name'] != name:
                        self.content.addWidget(label('커밋 기록자 · ' + record['committer_name'], 11, MUTED))
        if not groups:
            self.content.addWidget(label(missing[0] if missing else '아직 확인되지 않았어요.', 12, MUTED))

    def render(self):
        clear(self.content)
        data = self.data
        self.content.addWidget(label('변경 기록', 15, BROWN, True))
        resolved = data['resolved']
        self.content.addWidget(label('해결 확인 · 코드 기준' if resolved else '검토 중' if self.candidate else '해결 확인 전', 14, '#39765b' if resolved else '#9a5637', True))
        if data.get('reason'):
            self.content.addWidget(label(data['reason'], 13))
        elif not resolved:
            self.content.addWidget(label('문제 여부와 실제 영향을 먼저 확인해 주세요.' if self.candidate else '아직 이 문제를 해결한 변경이 확인되지 않았어요.', 12, MUTED))
        files = data.get('files', [])
        for change in files if self.all_changes else files[:2]:
            self.content.addWidget(label(change['path'], 12, BROWN, True))
            for key, title, color, background in [('before', '변경 전', '#9c493c', '#fff0ea'), ('after', '변경 후', '#35664d', '#eef7f0')]:
                lines = change[key]
                if not lines: text = '(추가된 줄 없음)' if key == 'after' else '(삭제된 줄 없음)'
                else:
                    text = '\n'.join(lines if self.all_changes else lines[:3])
                    if not self.all_changes and len(lines) > 3: text += '\n…'
                self.content.addWidget(label(title, 11, color))
                code = label(text[:6000], 12, color)
                code.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                code.setStyleSheet(f"font-family:'Consolas','Malgun Gothic';font-size:14px;background:{background};padding:7px;border:0;")
                self.content.addWidget(code)
        if files and (len(files) > 2 or any(len(c[k]) > 3 for c in files for k in ('before', 'after'))):
            self.content.addWidget(button('변경 코드 접기' if self.all_changes else f'변경 코드 전체 보기 · {len(files)}개 파일', self.toggle_changes, role='chip'))
        self.people('관련 코드 작성자' if self.finding.get('integration_only') else '근거 코드 작성자' if self.candidate and not resolved else '문제 코드 작성자', data['writers'], origin=True)
        if resolved:
            self.people('해결한 사람' if len(data['fixers']) == 1 else '해결 관련 수정자', data['fixers'], commits=True)
            for commit in data['confirmed']:
                if not any(p['sha'] == commit['sha'] for p in data['fixers']):
                    self.link('해결 확인 커밋 ' + commit['sha'][:8], commit['url'])
            for note in data.get('notes', []): self.content.addWidget(label(note, 12, MUTED))
