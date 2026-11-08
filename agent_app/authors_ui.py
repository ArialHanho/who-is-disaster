"""Compact, asynchronously loaded Git author rows in a finding's detail."""
from datetime import datetime
from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, Slot
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout
from .background import Worker
from .review_theme import BROWN, MUTED, LINE, button, clear, label


class Avatar(QWidget):
    def __init__(self, name, path=''):
        super().__init__()
        self.setFixedSize(36, 36)
        self.setAccessibleName(name + ' 프로필 사진')
        self.letter = name[:1].upper() or '?'
        self.photo = QPixmap(path) if path else QPixmap()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shape = QPainterPath(); shape.addEllipse(0, 0, self.width(), self.height())
        painter.setClipPath(shape)
        painter.fillRect(self.rect(), QColor(LINE))
        if not self.photo.isNull():
            photo = self.photo.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            painter.drawPixmap((self.width()-photo.width())//2, (self.height()-photo.height())//2, photo)
        else:
            painter.setPen(QColor(BROWN)); painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.letter)


class AuthorDetails(QWidget):
    def __init__(self, panel, repo, finding):
        super().__init__()
        self.panel, self.repo, self.finding = panel, repo, finding
        self.started = False
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(0, 8, 0, 8)
        self.content.setSpacing(9)
        self.content.addWidget(label('수정자를 확인하고 있어요…', 12, MUTED))

    def showEvent(self, event):
        super().showEvent(event)
        if not self.started:
            self.started = True
            QTimer.singleShot(0, self.load)

    def load(self):
        panel = self.panel
        worker = Worker(lambda: panel.service.authors.lookup(self.repo, self.finding))
        panel.workers.append(worker)
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(self.failed)
        worker.signals.finished.connect(lambda: panel.workers.remove(worker) if worker in panel.workers else None)
        QThreadPool.globalInstance().start(worker)

    @Slot(str)
    def failed(self, error):
        clear(self.content)
        self.content.addWidget(label('수정자 이력을 가져오지 못했어요.', 12, MUTED))
        self.content.addWidget(button('다시 확인', self.load, role='chip'))

    @Slot(object)
    def received(self, rows):
        self.rows, self.details_open = rows, False
        self.render_rows()

    def toggle_records(self):
        self.details_open = not self.details_open
        self.render_rows()

    def render_rows(self):
        clear(self.content)
        if not self.rows:
            self.content.addWidget(label('수정자를 확인할 코드 근거가 없어요.', 12, MUTED))
            return
        groups = {}
        for row in self.rows:
            if row.get('note'):
                self.content.addWidget(label(f"{row['path']}:{row['line']} · {row['note']}", 12, MUTED))
                continue
            person = row.get('author', {})
            groups.setdefault(person.get('login') or row['sha'], []).append(row)
        for records in groups.values():
            row = records[0]
            person, name = row.get('author', {}), row['author_name']
            line = QHBoxLayout(); line.setSpacing(10)
            line.addWidget(Avatar(name, person.get('avatar', '')), 0, Qt.AlignmentFlag.AlignTop)
            details = QVBoxLayout(); details.setSpacing(2)
            identity = name
            if person:
                identity = '@' + person['login'] if name.casefold() == person['login'].casefold() else name + ' · @' + person['login']
            heading = label('수정자: ' + identity, 13, BROWN, True)
            heading.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            details.addWidget(heading)
            context = f"{row['path']}:{row['line']}" + (f" 외 {len(records)-1}곳" if len(records)>1 else '')
            details.addWidget(label(context, 11, MUTED))
            line.addLayout(details, 1)
            if person:
                link = button('프로필', lambda url=person['url']: QDesktopServices.openUrl(QUrl(url)), role='icon')
                link.setToolTip(person['url']); line.addWidget(link, 0, Qt.AlignmentFlag.AlignTop)
            self.content.addLayout(line)
        known = [row for row in self.rows if row.get('sha')]
        if known:
            self.content.addWidget(button('변경 기록 접기' if self.details_open else f'변경 기록 {len(known)}개 보기', self.toggle_records, role='icon'))
        if not self.details_open: return
        for row in known:
            context = f"{row['path']}:{row['line']} · " + ', '.join(row.get('views', [row['view']]))
            reference = label(context if len(context)<100 else context[:97]+'…', 11, MUTED)
            reference.setToolTip(context); self.content.addWidget(reference)
            committer, person = row.get('committer', {}), row.get('author', {})
            if row.get('committer_name') and (row['committer_name'] != row['author_name'] or committer.get('login') != person.get('login')):
                self.content.addWidget(label('커밋 기록자: ' + row['committer_name'] + (' · @' + committer['login'] if committer else ''), 11, MUTED))
            title = '커밋 ' + row['sha'][:8]
            if row.get('author_time'):
                title += ' · ' + datetime.fromtimestamp(int(row['author_time'])).strftime('%Y.%m.%d')
            if row.get('commit_url'):
                self.content.addWidget(button(title, lambda url=row['commit_url']: QDesktopServices.openUrl(QUrl(url)), role='icon'))
            else: self.content.addWidget(label(title, 11, MUTED))
        self.content.addWidget(label('근거 줄의 마지막 변경 기록이에요.', 11, MUTED))
