"""Compact, typed speech anchored to the desktop character."""
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPen, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QMenu
from .review_analysis import CATEGORIES
from .typing_label import TypingLabel, sentence_lines
from .review_theme import STYLE, BROWN, MUTED, label, button, size_popup, RoundedSurface


def short_text(text, length):
    text = " ".join(text.split())
    return text if len(text) <= length else text[:length - 1].rstrip() + "…"


def issue_summary(findings, *, repository="", error=False):
    problems = sum(f["severity"] == "problem" for f in findings)
    warnings = len(findings) - problems
    counts = [f"{label} {count}건" for label, count in (("문제", problems), ("경고", warnings)) if count]
    lines = [short_text(repository, 20)] if repository else []
    if counts:
        lines.append(" · ".join(counts))
    if error:
        lines.append("분석 연결을 확인해 주세요.")
    priority = sorted(findings, key=lambda f: (f["severity"] != "problem", -f["last_seen"]))
    for finding in priority[:1 if error else 2]:
        title = ("재발 · " if finding.get("reopened_count") else "") + finding["title"]
        lines.append("• " + short_text(title, 32))
        detail = CATEGORIES[finding["category"]]
        evidence = finding["evidence"]
        if evidence:
            anchor = next((e for e in evidence if e.get("role") == "implementation"), evidence[0])
            filename = anchor["path"].replace("\\", "/").rsplit("/", 1)[-1]
            detail += f" · {short_text(filename, 18)}:{anchor['line']}"
        lines.append("\u3000" + detail)
    return "\n".join(lines)


class ReviewBubble(RoundedSurface):
    def __init__(self, character):
        super().__init__()
        self.character = character
        self.mode, self.target_repo, self.remaining_ms, self.hovered = "greeting", None, 0, False
        self.target_page = "brief"
        self.speech_tail = None
        self.setObjectName("speech")
        self.setWindowTitle("코드 비서 · 말풍선")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(388, 286)
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        tag = self.tag = label("코드 비서", display=True)
        tag.setObjectName("tag")
        tag.setStyleSheet("")
        self.bubble_header = QWidget()
        header = QHBoxLayout(self.bubble_header)
        header.setContentsMargins(0,0,0,0)
        header.addWidget(tag, 0, Qt.AlignmentFlag.AlignLeft)
        header.addStretch()
        self.message = TypingLabel()
        self.skip = button("바로 읽기", self.message.finish, role="icon")
        self.skip.hide()
        header.addWidget(self.skip)
        layout.addWidget(self.bubble_header)
        message_row = QHBoxLayout()
        message_row.setSpacing(8)
        message_row.addWidget(self.message,1)
        self.chat_close = button('✕',self.hide,role='icon')
        self.chat_close.setFixedSize(26,26)
        self.chat_close.setAccessibleName('말풍선 닫기')
        self.chat_close.hide()
        message_row.addWidget(self.chat_close,0,Qt.AlignmentFlag.AlignTop)
        layout.addLayout(message_row)
        self.overview = button('전체 문제 보기', lambda: self.open_details(all_repos=self.mode != 'notice'), role='chip')
        layout.addWidget(self.overview)
        self.message.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.message.customContextMenuRequested.connect(lambda pos:self.show_chatter_menu(self.message.mapToGlobal(pos)))
        self.chat_credit = label('',11,MUTED)
        self.chat_credit.hide()
        layout.addWidget(self.chat_credit)
        self.reading_hint = label("", 11, MUTED)
        self.reading_hint.setFixedHeight(18)
        layout.addWidget(self.reading_hint)
        self.shortcuts_row = QWidget()
        shortcuts = QHBoxLayout(self.shortcuts_row)
        shortcuts.setContentsMargins(0, 0, 0, 0)
        self.shortcuts = []
        for title, fn in [("GitHub", character.panel.add_repo), ("로컬 폴더", character.panel.add_local_repo), ("주소 입력", character.panel.add_manual_repo)]:
            shortcut = button(title, lambda f=fn:self.quick(f), role="chip")
            self.shortcuts.append(shortcut)
            shortcuts.addWidget(shortcut)
        layout.addWidget(self.shortcuts_row)
        self.bubble_footer = QWidget()
        row = QHBoxLayout(self.bubble_footer)
        row.setContentsMargins(0,0,0,0)
        self.character_button = button("캐릭터", lambda:self.quick(character.panel.customize_character, "settings"), role="icon")
        row.addWidget(self.character_button)
        row.addStretch()
        row.addWidget(button("닫기", self.hide, role="icon"))
        self.details = button("브리핑 열기", self.open_details, True)
        row.addWidget(self.details)
        layout.addWidget(self.bubble_footer)
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.hide)
        self.message.typing_changed.connect(self.typing_changed)
        self.message.finished.connect(self.arm_expiry)

    def quick(self, fn, page="repos"):
        self.expiry.stop()
        self.character.panel.open_tab(page)
        self.character.open_panel()
        fn()

    def open_details(self, *, all_repos=False):
        panel = self.character.panel
        if self.mode == 'chatter':
            panel.idle_chat.speak(manual=True)
            return
        panel.category = "all"
        panel.lifecycle = "active"
        panel.finding_page = 0
        panel.expanded.clear()
        panel.repo_id = self.target_repo if not all_repos and self.target_repo in {r["id"] for r in panel.service.repos()} else None
        panel.save_settings()
        panel.open_tab(self.target_page)
        self.character.open_panel()

    def compact(self):
        return self.mode in {'chatter', 'greeting', 'interaction', 'progress', 'summary', 'notice'}

    def sync_actions(self):
        self.shortcuts_row.setVisible(not self.compact() and not bool(self.character.panel.service.repos()))
        for shortcut in self.shortcuts:
            shortcut.setEnabled(not self.character.panel.busy)
            shortcut.setToolTip("현재 분석을 마친 뒤 연결할 수 있어요" if self.character.panel.busy else "")

    def refresh_text(self):
        self.expiry.stop()
        panel = self.character.panel
        repos = panel.service.repos()
        count = len(repos)
        if panel.busy:
            self.show_message(panel.progress_text, mode="progress")
        elif not count:
            self.show_message("브리핑에서 저장소를 연결해요.")
        else:
            errors = sum(bool(r["error"]) for r in repos)
            unreviewed = sum(not bool(r["last_head"]) for r in repos)
            issues = [f for r in repos for f in panel.service.findings(r['id'], limit=None)
                      if f['category'] != 'feature' and f['state'] == 'open' and f['confidence'] == 'high']
            if issues:
                self.show_message(issue_summary(issues, error=bool(errors)), mode="summary")
                return
            if errors: text = f"연결·분석 확인이 필요한 저장소가 {errors}곳 있어요."
            elif unreviewed: text = f"아직 분석하지 않은 저장소가 {unreviewed}곳 있어요."
            else: text = self.character.dialogue.choose('quiet_greeting')
            self.show_message(text)

    def show_message(self, text, *, mode="greeting", repo_id=None, auto_hide_ms=0, page="brief", credit=''):
        self.target_page = page
        self.expiry.stop()
        self.mode, self.target_repo, self.remaining_ms = mode, repo_id, auto_hide_ms
        chatting = self.compact()
        self.overview.setText({"greeting": "브리핑 열기", "progress": "브리핑 열기", "notice": "저장소 문제 보기"}.get(mode, "전체 문제 보기"))
        self.overview.setVisible(mode in {"greeting", "progress", "summary", "notice"})
        self.details.setText("알림 보기" if mode == "notice" else "브리핑 열기")
        self.bubble_header.setVisible(not chatting)
        self.bubble_footer.setVisible(not chatting)
        self.chat_close.setVisible(chatting)
        margin=14 if chatting else 20
        self.layout().setContentsMargins(margin,14 if chatting else 16,margin,24 if chatting else 16)
        self.layout().setSpacing(8 if chatting else 12)
        self.chat_credit.setText(credit)
        self.chat_credit.setVisible(bool(credit) and not chatting)
        self.sync_actions()
        self.message.ensurePolished()
        width = 332 if mode in {"summary", "notice"} else 300 if chatting else 388
        width = min(width, self.screen().availableGeometry().width() - 24)
        if chatting: text=sentence_lines(text,width-2*margin-34,self.message.fontMetrics())
        # Progress can change before a sentence finishes. Never restart its
        # typewriter on every update or leave a lone first letter on screen.
        self.message.present(text, animate=mode != 'progress' and not self.character.preferences["reduced_motion"])
        self.message.setFixedHeight(self.message.heightForWidth(width-2*margin-(34 if chatting else 0)))
        self.layout().invalidate()
        if chatting:
            # Hidden footer/layout hints can retain the preceding longer message's height.
            desired = 14 + max(26, self.message.height()) + 24
            if not self.overview.isHidden(): desired += self.overview.sizeHint().height() + 8
        else:
            desired = max(170, self.layout().sizeHint().height())
        size_popup(self, width=width, height=desired)
        if self.isVisible(): self.character.place_popup(self)

    def set_speech_tail(self,x):
        target=max(22,min(x,self.width()-22)) if x is not None else None
        if target!=self.speech_tail:
            self.speech_tail=target
            self.update()

    def paintEvent(self,event):
        if not self.compact() or self.speech_tail is None:
            return super().paintEvent(event)
        p=QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        body=QPainterPath()
        body.addRoundedRect(QRectF(1,1,self.width()-2,self.height()-12),16,16)
        tail=QPainterPath()
        tail.moveTo(self.speech_tail-8,self.height()-12)
        tail.lineTo(self.speech_tail,self.height()-1)
        tail.lineTo(self.speech_tail+8,self.height()-12)
        tail.closeSubpath()
        p.setPen(QPen(QColor(BROWN),2))
        p.setBrush(QColor('#fff6de'))
        p.drawPath(body.united(tail))

    def typing_changed(self, typing):
        self.skip.setVisible(typing and not self.compact())
        self.reading_hint.setVisible(bool(self.remaining_ms) and not self.compact())
        self.reading_hint.setText("글을 누르면 바로 읽을 수 있어요" if typing else "마우스를 올리면 잠시 머물러요" if self.remaining_ms else "")

    def chatter_menu(self):
        menu=QMenu(self)
        panel=self.character.panel
        menu.addAction('한마디 더',lambda:panel.idle_chat.speak(manual=True))
        menu.addAction('기타 연결',panel.other_connections)
        menu.addAction('1시간 조용히',panel.idle_chat.quiet_hour)
        menu.addAction('오늘은 조용히',panel.idle_chat.quiet_today)
        menu.addAction('브리핑 열기', self.character.open_brief)
        menu.addSeparator()
        menu.addAction('닫기',self.hide)
        return menu

    def show_chatter_menu(self,position):
        if not self.compact(): return
        if self.expiry.isActive(): self.remaining_ms=max(1,self.expiry.remainingTime())
        self.expiry.stop()
        menu=self.chatter_menu()
        try: menu.exec(position)
        finally:
            menu.deleteLater()
            self.hovered=self.underMouse()
            self.arm_expiry()

    def contextMenuEvent(self,event):
        if self.compact(): self.show_chatter_menu(event.globalPos())
        else: super().contextMenuEvent(event)

    def arm_expiry(self):
        if self.isVisible() and not self.message.typing and self.remaining_ms and not self.hovered:
            self.expiry.start(self.remaining_ms)

    def enterEvent(self, event):
        self.hovered = True
        if self.expiry.isActive(): self.remaining_ms = max(1, self.expiry.remainingTime())
        self.expiry.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.arm_expiry()
        super().leaveEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.character.place_popup(self)
        self.character.update()
        self.hovered = self.underMouse()
        self.arm_expiry()

    def hideEvent(self, event):
        self.expiry.stop()
        self.message.timer.stop()
        super().hideEvent(event)
        self.character.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape: self.hide()
        elif event.key() in {Qt.Key.Key_Space, Qt.Key.Key_Return} and self.message.typing: self.message.finish()
        else: super().keyPressEvent(event)
