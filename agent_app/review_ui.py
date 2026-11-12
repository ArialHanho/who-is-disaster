"""Resident POPME-style briefing, repository chips and expandable paper cards."""
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime
from PySide6.QtCore import Qt, QTimer, QThreadPool, QUrl
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices, QPainterPath, QRegion
from PySide6.QtWidgets import (QApplication, QWidget, QFrame, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QLineEdit, QCheckBox, QFileDialog, QSystemTrayIcon,
    QMenu, QListWidget, QListWidgetItem, QAbstractItemView, QSizePolicy, QLayout, QProgressBar, QLabel)
from .character import DesktopCharacter
from .background import Worker
from .settings import load_env
from . import codex_provider
from . import APP_NAME, __version__
from .review_analysis import CATEGORIES, repair_prompt, batch_repair_prompt
from .review_source import normalize_source
from .review_service import ReviewService
from .review_revision import excluded_branches, included_branches, included_pulls
from .review_theme import STYLE, ACCENT, BROWN, MUTED, label, button, load_fonts, clear, scroll_body, FlowLayout, size_popup, reveal, RoundedSurface, RoundedDialog
from .github_ui import GitHubDialog
from .github_actions import STATE_LABELS, finding_targets, needs_attention, repair_instruction
from .review_bubble import ReviewBubble
from .single_instance import SingleInstance
from .review_watch import ChangeWatcher
from .finding_history_ui import FindingHistory

CONFIG = {"name": APP_NAME, "kind": "code", "character_name": "코드 비서", "accent": ACCENT}
FINDINGS_PER_PAGE = 24


def rounded(widget):
    if isinstance(widget, (RoundedSurface, RoundedDialog)):
        widget.clearMask()
        return
    path = QPainterPath()
    path.addRoundedRect(0, 0, widget.width(), widget.height(), 16, 16)
    widget.setMask(QRegion(path.toFillPolygon().toPolygon()))


def frame(layout, important=False):
    w = QFrame()
    w.setObjectName("important" if important else "news")
    w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    inner = QVBoxLayout(w)
    inner.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    inner.setContentsMargins(16, 14, 16, 16)
    inner.setSpacing(11)
    layout.addWidget(w)
    return w, inner




class RepoDialog(RoundedDialog):
    def __init__(self, panel, repo=None, source="", local=False):
        super().__init__(panel)
        self.panel, self.repo, self.saved_ids = panel, repo, []
        self.setWindowTitle('코드 비서 · 저장소 설정')
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        size_popup(self)
        self.move(panel.pos())
        rounded(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.setSpacing(0)
        header_frame = QFrame()
        header_frame.setObjectName('header')
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(20, 14, 16, 14)
        header.addWidget(label("저장소 설정" if repo else "함께 볼 저장소", 16, BROWN, True), 1)
        header.addWidget(button("✕", self.reject, role="icon"))
        outer.addWidget(header_frame)
        self.scroll, layout = scroll_body(outer)
        layout.addWidget(label("GitHub 주소를 한 줄에 하나씩 넣어 주세요.\n여러 저장소를 한 번에 연결할 수 있어요." if not repo else repo["name"], 13, MUTED))
        self.source = QPlainTextEdit(repo["source"] if repo else source)
        self.source.setPlaceholderText("https://github.com/owner/app\n또는 C:\\Projects\\my-app 같은 로컬 Git 경로")
        self.source.setAccessibleName("연결할 GitHub 저장소 목록")
        self.source.setFixedHeight(72 if repo else 95)
        self.source.setReadOnly(bool(repo))
        layout.addWidget(self.source)
        if not repo:
            layout.addWidget(button("로컬 Git 폴더 고르기", self.pick_folder))
        self.all_branches = QCheckBox("전체 브랜치를 함께 살펴보기")
        self.all_branches.setChecked(bool(repo["all_branches"]) if repo else True)
        layout.addWidget(self.all_branches)
        layout.addWidget(label("새 브랜치도 따라가요. 제외할 브랜치는 ‘브랜치 보기’에서 체크를 풀어 주세요.", 12, MUTED))
        self.open_prs = QCheckBox("열린 PR도 자동으로 포함")
        self.open_prs.setChecked(bool(repo["open_prs"]) if repo else False)
        layout.addWidget(self.open_prs)
        layout.addWidget(label("GitHub 로그인이 필요해요. 새 PR은 포함하고 닫힌 PR은 제외해요.", 12, MUTED))
        self.working = QCheckBox("등록된 작업 폴더의 미커밋 변경 포함")
        self.working.setChecked(bool(repo["include_working"]) if repo else local)
        layout.addWidget(self.working)
        layout.addWidget(label("로컬 연결에서 사용 · 파일을 저장한 내용까지 읽어요.", 11, MUTED))
        def working_available():
            entries = [s.strip() for s in self.source.toPlainText().splitlines() if s.strip()]
            available = not entries or any(Path(s).is_dir() for s in entries)
            self.working.setEnabled(available)
            if not available: self.working.setChecked(False)
            self.working.setToolTip("미커밋 변경을 보려면 로컬 Git 폴더를 연결해 주세요." if not available else "")
        self.source.textChanged.connect(working_available)
        working_available()
        layout.addWidget(label("목표와 유지할 설계", 14, BROWN, True))
        self.rules = QPlainTextEdit(repo["rules"] if repo else "")
        self.rules.setPlaceholderText("예: 작은 팀용 앱. 기존 API를 유지하고 새 프레임워크는 추가하지 않기.")
        self.rules.setFixedHeight(144)
        self.rules.setAccessibleName('목표와 유지할 설계')
        layout.addWidget(self.rules)
        self.auto = QCheckBox("커밋·푸시 변경을 모아서 자동 점검")
        self.auto.setToolTip("마지막 변경 후 90초 · 자동 분석 최소 5분 간격\n계속 변경되면 최대 10분까지 모아요. 지금 분석은 바로 실행해요.")
        self.auto.setChecked(bool(repo["auto"]) if repo else True)
        layout.addWidget(self.auto)
        layout.addWidget(label("로컬 Git 변경은 바로 감지해요. GitHub 연결은 새 커밋·PR 여부만 가볍게 확인하고, 달라졌을 때 분석해요. 저장만 한 미커밋 변경은 ‘지금 분석’으로 확인할 수 있어요.", 12, MUTED))
        layout.addWidget(label("기존 Codex 로그인과 구독 한도를 사용해요. 바뀐 코드가 없으면 AI를 다시 부르지 않아요.", 12, MUTED))
        layout.addStretch()
        self.footer = QFrame()
        self.footer.setObjectName('footer')
        footer = QVBoxLayout(self.footer)
        footer.setContentsMargins(18, 12, 18, 14)
        footer.setSpacing(10)
        self.error = label("", 12, "#d65151")
        self.error.hide()
        footer.addWidget(self.error)
        self.remove_pending = False
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        if repo:
            self.remove_button = button("연결 해제", self.remove_repo, role="danger")
            self.remove_button.setToolTip('이 비서에서 저장소 연결 해제')
            row.addWidget(self.remove_button)
        row.addStretch()
        self.cancel_button = button("취소", self.cancel, role="icon")
        row.addWidget(self.cancel_button)
        self.save_button = button("저장" if repo else "연결하기", self.save, True)
        row.addWidget(self.save_button)
        footer.addLayout(row)
        outer.addWidget(self.footer)

    def show_error(self, message):
        self.error.setText(message)
        self.error.setVisible(bool(message))

    def cancel(self):
        if self.remove_pending:
            self.remove_pending = False
            self.show_error("")
            self.remove_button.setText("연결 해제")
            self.save_button.setEnabled(True)
            self.cancel_button.setText("취소")
        else: self.reject()

    def remove_repo(self):
        if not self.remove_pending:
            self.remove_pending = True
            self.show_error("이 저장소의 연결과 비서 기록을 지울까요? GitHub 원본과 로컬 코드는 유지돼요.")
            self.remove_button.setText("해제 확인")
            self.save_button.setEnabled(False)
            self.cancel_button.setText("연결 유지")
            return
        try:
            self.panel.service.disconnect(self.repo["id"])
            self.panel.queue = [rid for rid in self.panel.queue if rid != self.repo["id"]]
            self.panel.latest_bubble_message = ""
            self.panel.status.setText("저장소 연결을 해제했어요. 자동 분석도 중단돼요.")
            self.accept()
        except Exception as exc:
            self.show_error(str(exc))

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "분석할 Git 폴더")
        if folder:
            self.source.appendPlainText(folder)
            self.working.setChecked(True)

    def save(self):
        try:
            sources = list(dict.fromkeys(normalize_source(s)[0] for s in self.source.toPlainText().splitlines() if s.strip()))
            if not sources:
                raise ValueError("연결할 저장소를 입력해 주세요.")
            existing = {(r["source"], r["branch"]): r["id"] for r in self.panel.service.repos()}
            for source in sources:
                if not self.repo and (source, "") in existing:
                    self.saved_ids.append(existing[source, ""])
                    continue
                rid = self.panel.service.save_repo(source, self.repo["branch"] if self.repo else "", self.rules.toPlainText(),
                    self.auto.isChecked(), 5, self.repo["id"] if self.repo else None,
                    targets=json.loads(self.repo["targets"]) if self.repo else [],
                    include_working=self.working.isChecked() and not source.startswith("https://"), all_branches=self.all_branches.isChecked(), open_prs=self.open_prs.isChecked())
                self.saved_ids.append(rid)
            self.accept()
        except Exception as exc:
            self.show_error(str(exc))


class BranchDialog(RoundedDialog):
    def __init__(self, panel, repo_id):
        super().__init__(panel)
        self.panel, self.repo = panel, panel.service.repo(repo_id)
        self.rows, self.loading, self.base = [], False, self.repo["branch"]
        self.selected = set(json.loads(self.repo["targets"]))
        self.excluded = excluded_branches(self.repo)
        self.pulls = []
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        size_popup(self)
        self.move(panel.pos())
        rounded(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("분석할 브랜치", 16, BROWN, True), 1)
        head.addWidget(button("✕", self.reject, role="icon"))
        layout.addLayout(head)
        layout.addWidget(label(self.repo["name"], 13, MUTED))
        self.info = label("목록을 가져오고 있어요…", 12, MUTED)
        layout.addWidget(self.info)
        self.search = QLineEdit()
        self.search.setPlaceholderText("브랜치 이름으로 찾기")
        self.search.textChanged.connect(self.filter)
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.all = QCheckBox("새 브랜치도 자동 포함")
        self.all.setChecked(bool(self.repo["all_branches"]))
        self.all.toggled.connect(self.toggle_all)
        row.addWidget(self.all, 1)
        self.reload = button("새로고침", self.load, role="icon")
        row.addWidget(self.reload)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.itemChanged.connect(self.choose)
        layout.addWidget(self.list, 1)
        self.open_prs = QCheckBox("열린 PR 자동 포함")
        self.open_prs.setChecked(bool(self.repo["open_prs"]))
        layout.addWidget(self.open_prs)
        self.pr_info = label("열린 PR은 다음 분석 때 갱신해요.", 12, MUTED)
        layout.addWidget(self.pr_info)
        self.pr_list = QListWidget()
        self.pr_list.setWordWrap(True)
        self.pr_list.setFixedHeight(122)
        self.pr_list.setAccessibleName("열린 PR 목록")
        self.pr_list.itemActivated.connect(lambda item:QDesktopServices.openUrl(QUrl(item.data(Qt.ItemDataRole.UserRole))))
        self.pr_list.hide()
        layout.addWidget(self.pr_list)
        self.prs = QLineEdit(" ".join(t for t in self.selected if t.startswith("pr:")))
        self.prs.setPlaceholderText("pr:12 pr:18 또는 PR 주소")
        layout.addWidget(self.prs)
        self.prs.setVisible(not self.open_prs.isChecked())
        self.open_prs.toggled.connect(lambda checked:self.prs.setVisible(not checked))
        self.open_prs.toggled.connect(self.update_scope_info)
        layout.addWidget(label("체크한 브랜치를 살펴봐요. 기준 브랜치는 비교에 사용해요.", 12, MUTED))
        self.error = label("", 12, "#d65151")
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addWidget(button("취소", self.reject, role="icon"))
        row.addStretch()
        self.save_button = button("선택 적용", self.save, True)
        row.addWidget(self.save_button)
        layout.addLayout(row)
        cached = panel.service.catalog(repo_id)
        if cached["checked"]:
            self.received(cached)
        QTimer.singleShot(0, self.load)

    def load(self):
        if self.loading:
            return
        self.loading = True
        self.reload.setEnabled(False)
        worker = Worker(lambda: self.panel.service.catalog(self.repo["id"], refresh=True))
        self.panel.workers.append(worker)
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(self.error.setText)
        worker.signals.finished.connect(lambda: self.finished(worker))
        QThreadPool.globalInstance().start(worker)

    def finished(self, worker):
        self.loading = False
        self.reload.setEnabled(True)
        if worker in self.panel.workers:
            self.panel.workers.remove(worker)

    def received(self, data):
        self.rows = data["branches"]
        self.base = base = self.repo["branch"] or data["default"]
        self.pulls = data.get("pulls", [])
        self.list.blockSignals(True)
        self.list.clear()
        for row in self.rows:
            text = row["name"] + ("  · 기준" if row["name"] == base else "")
            item = QListWidgetItem(text)
            item.setToolTip(row["name"] + "\n" + row["head"] + ("\n" + row["subject"] if row["subject"] else ""))
            item.setData(Qt.ItemDataRole.UserRole, row["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = row["name"] == base or row["name"] not in self.excluded and (self.all.isChecked() or row["name"] in self.selected)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            if row["name"] == base:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            self.list.addItem(item)
        self.list.blockSignals(False)
        self.filter()
        self.update_scope_info()

    def filter(self, *_):
        text = self.search.text().strip().casefold()
        for i in range(self.list.count()):
            item = self.list.item(i)
            item.setHidden(text not in item.text().casefold())

    def toggle_all(self, checked):
        if not checked:
            self.update_scope_info()
            return
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                name = item.data(Qt.ItemDataRole.UserRole)
                item.setCheckState(Qt.CheckState.Unchecked if name in self.excluded else Qt.CheckState.Checked)
        self.list.blockSignals(False)
        self.update_scope_info()

    def choose(self, item):
        name = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self.excluded.discard(name)
        elif name != self.base:
            self.excluded.add(name)
        self.update_scope_info()

    def update_scope_info(self, *_):
        if self.rows:
            names = {row["name"] for row in self.rows}
            checked = {item.data(Qt.ItemDataRole.UserRole) for index in range(self.list.count())
                       if (item := self.list.item(index)).checkState() == Qt.CheckState.Checked}
            self.selected = (self.selected - names) | checked
            self.info.setText(f"{len(checked)}/{len(self.rows)}개 분석 · 기준 {self.base or 'HEAD'}")
        pulls = included_pulls({"excluded_branches": json.dumps(sorted(self.excluded))}, self.pulls)
        self.pr_info.setText(f"열린 PR {len(pulls)}개 포함" if self.open_prs.isChecked() else "PR을 직접 추가할 수 있어요.")
        self.pr_list.clear()
        for pull in pulls:
            item = QListWidgetItem(f"#{pull['number']} {pull['title']}\n→ {pull['base']}" + (" · 초안" if pull.get("draft") else ""))
            item.setToolTip("두 번 눌러 GitHub에서 열기")
            item.setData(Qt.ItemDataRole.UserRole, pull["url"])
            self.pr_list.addItem(item)
        self.pr_list.setVisible(self.open_prs.isChecked() and bool(pulls))

    def save(self):
        try:
            selected = sorted(t for t in self.selected if t != self.base and not t.startswith("pr:"))
            excluded = self.excluded | {row["name"] for row in self.rows if row["name"] not in self.selected}
            if not self.open_prs.isChecked():
                selected += self.prs.text().split()
            self.panel.service.set_scope(self.repo["id"], self.all.isChecked(), selected,
                                         open_prs=self.open_prs.isChecked(), excluded=excluded - {self.base})
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))


class ReviewPanel(RoundedSurface):
    def __init__(self, root, service=None):
        super().__init__()
        load_fonts()
        self.root = Path(root)
        self.service = service or ReviewService(self.root / ".data/code")
        self.repo_id, self.sprite, self.busy = None, None, False
        self.page, self.category = "brief", "all"
        self.finding_page = 0
        self.lifecycle, self.recheck_id, self.force_repos = "active", None, set()
        self.recheck_targets = {}
        self.actions_pending = set()
        self.full_scope_scan, self.scan_audit = False, None
        self.changed_repos = set()
        self.workers, self.queue, self.expanded = [], [], set()
        self.rendered_page, self.render_generation = None, 0
        self.model_draft = None
        self.latest_bubble_message, self.current_finding = "", None
        self.progress_text = "분석 준비 중이에요"
        self.settings_path = self.root / ".data/code/settings.json"
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.setAcceptDrops(True)
        try:
            settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            # Old single-repository selection becomes an all-repositories briefing.
            self.repo_id = settings.get("repo_filter")
            os.environ["CODEX_MODEL"] = settings.get("model", os.getenv("CODEX_MODEL", ""))
        except (OSError, ValueError):
            pass
        self.setObjectName("popover")
        self.setWindowTitle("Who is disaster · 코드 브리핑")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        size_popup(self)
        self.setStyleSheet(STYLE)
        rounded(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        header = QFrame()
        header.setObjectName("header")
        top = QHBoxLayout(header)
        top.setContentsMargins(16, 13, 12, 13)
        top.setSpacing(10)
        tag = self.tag = label("코드 비서", display=True)
        tag.setObjectName("tag")
        tag.setStyleSheet("")
        top.addWidget(tag)
        self.subtitle = label("함께 살펴볼게요", 12, MUTED)
        top.addWidget(self.subtitle, 1)
        self.scan = button("지금 분석", self.scan_now)
        top.addWidget(self.scan)
        top.addWidget(button("✕", self.hide, role="icon"))
        layout.addWidget(header)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("QProgressBar{border:0;background:#f3e5c8;} QProgressBar::chunk{background:#e8865a;}")
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        nav = QFrame()
        nav.setObjectName("nav")
        nav_layout = QHBoxLayout(nav)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)
        self.tabs = {}
        for key, title in [("brief", "브리핑"), ("repos", "저장소"), ("history", "기록"), ("settings", "설정")]:
            tab = button(title, lambda k=key: self.open_tab(k), role="tab")
            tab.setCheckable(True)
            self.tabs[key] = tab
            nav_layout.addWidget(tab, 1)
        layout.addWidget(nav)
        self.connect_bar = QWidget()
        connection_row = QHBoxLayout(self.connect_bar)
        connection_row.setContentsMargins(20, 14, 20, 0)
        connection_row.setSpacing(9)
        self.connect_buttons = []
        for title, fn in [("GitHub 연결", self.add_repo), ("로컬 폴더", self.add_local_repo), ("주소 입력", self.add_manual_repo)]:
            b = button(title, fn)
            self.connect_buttons.append(b)
            connection_row.addWidget(b, 1)
        layout.addWidget(self.connect_bar)
        self.scroll, self.cards = scroll_body(layout)
        self.footer = QFrame()
        self.footer.setObjectName("footer")
        footer_row = QHBoxLayout(self.footer)
        footer_row.setContentsMargins(18, 10, 12, 12)
        footer_row.setSpacing(12)
        self.status = label("필요한 알림만 알려드려요.", 11, MUTED)
        footer_row.addWidget(self.status, 1)
        self.scroll_more = button("아래로", self.scroll_down, role="chip")
        self.scroll_more.setToolTip("아래쪽에 남은 내용 보기")
        self.scroll_more.setAccessibleName("아래쪽 남은 내용 보기")
        self.scroll_more.hide()
        footer_row.addWidget(self.scroll_more, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.footer)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.update_scroll_hint)
        self.scroll.verticalScrollBar().valueChanged.connect(self.update_scroll_hint)
        self.reload_repos()
        self.watcher = ChangeWatcher(self.service, self)
        self.watcher.changed.connect(self.code_changed)
        self.watcher.failed.connect(lambda rid, error:self.status.setText("변경 감지 확인 필요 · " + error))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(60000)
        from .idle_chat import IdleChat
        self.idle_chat = IdleChat(self)

    def create_bubble(self, character): return ReviewBubble(character)

    def update_scroll_hint(self, *args):
        bar = self.scroll.verticalScrollBar()
        self.scroll_more.setVisible(bar.maximum() > bar.value()+2)

    def scroll_down(self):
        bar = self.scroll.verticalScrollBar()
        bar.setValue(min(bar.maximum(), bar.value()+max(80, bar.pageStep()-36)))

    def save_settings(self):
        self.settings_path.write_text(json.dumps({"repo_filter": self.repo_id, "model": os.getenv("CODEX_MODEL", "")}, ensure_ascii=False), encoding="utf-8")

    def reload_repos(self):
        if self.repo_id not in {r["id"] for r in self.service.repos()}:
            self.repo_id = None
        self.refresh()

    def open_tab(self, page):
        self.page = page
        self.refresh(reset_scroll=True)

    def select_repo(self, repo_id):
        self.repo_id = repo_id
        self.finding_page = 0
        self.expanded.clear()
        self.save_settings()
        self.refresh(reset_scroll=True)

    def chips(self, items, active, fn, role="chip"):
        w = QWidget()
        flow = FlowLayout(w)
        for key, title in items:
            b = button(title, lambda k=key: fn(k), role=role)
            b.setCheckable(True)
            b.setChecked(key == active)
            flow.addWidget(b)
        self.cards.addWidget(w)

    def card(self, title, body="", badge=""):
        _, layout = frame(self.cards)
        if badge:
            layout.addWidget(label(badge, 11, MUTED))
        layout.addWidget(label(title, 14, BROWN, True))
        if body:
            layout.addWidget(label(body, 13))
        return layout

    def refresh(self, reset_scroll=False):
        position = 0 if reset_scroll else self.scroll.verticalScrollBar().value()
        repos = self.service.repos()
        selected = [r for r in repos if self.repo_id is None or r["id"] == self.repo_id]
        for key, tab in self.tabs.items():
            tab.setChecked(key == self.page)
        self.scan.setEnabled(bool(repos) and not self.busy)
        self.scan.setText("분석 중" if self.busy else "지금 분석")
        self.progress_bar.setVisible(self.busy)
        self.connect_bar.setVisible(self.page in {"repos", "settings"} or not repos)
        for b in self.connect_buttons: b.setEnabled(not self.busy)
        if self.page == "settings" and self.rendered_page == "settings":
            self.model_save.setEnabled(not self.busy)
            self.model_save.setToolTip("현재 분석이 끝나면 저장할 수 있어요" if self.busy else "")
        if not self.busy:
            self.subtitle.setText(f"저장소 {len(repos)}곳")
        if self.sprite: self.sprite.bubble.sync_actions()
        # Background completions must not destroy a focused settings editor.
        if self.page == self.rendered_page == "settings":
            if reset_scroll: self.scroll.verticalScrollBar().setValue(0)
            return
        self.render_generation += 1
        generation = self.render_generation
        clear(self.cards)
        if self.page in {"brief", "history"}:
            self.cards.addWidget(label("오늘의 코드 브리핑" if self.page == "brief" else "함께 살펴본 기록", 15, BROWN, True))
            if repos:
                self.chips([(None, f"모든 저장소 {len(repos)}")] + [(r["id"], r["name"].split("/")[-1][:22]) for r in repos], self.repo_id, self.select_repo)
            if not repos:
                self.card("어떤 코드를 함께 살펴볼까요?", "위에서 GitHub·로컬 폴더·주소 입력 중 하나를 골라 주세요. 폴더를 이곳이나 캐릭터에 놓아도 연결할 수 있어요.")
            elif self.page == "history":
                for repo in selected:
                    self.cards.addWidget(label(repo["name"], 14, BROWN, True))
                    for f in self.service.findings(repo["id"], True):
                        if f["disposition"] != "open" or f["state"] != "open":
                            self.finding_card(repo, f)
                    runs = self.service.runs(repo["id"])[:5]
                    if runs:
                        log_box = QWidget()
                        log_layout = QVBoxLayout(log_box)
                        log_layout.setContentsMargins(0, 0, 0, 0)
                        for run in runs:
                            title = datetime.fromtimestamp(run['stamp']).strftime('%m월 %d일 %H:%M')
                            if run.get('ai_error') or run.get('followup_error'): title += ' · 확인 미완료'
                            log_layout.addWidget(label(title, 13, BROWN, True))
                            log_layout.addWidget(label(run.get('summary', ''), 12))
                        log_box.hide()
                        log_button = button(f'최근 분석 기록 {len(runs)}회 보기', lambda: None, role='chip')
                        def toggle_log(_checked=False, box=log_box, control=log_button, count=len(runs)):
                            show = box.isHidden()
                            box.setVisible(show)
                            control.setText('분석 기록 접기' if show else f'최근 분석 기록 {count}회 보기')
                        log_button.clicked.connect(toggle_log)
                        self.cards.addWidget(log_button)
                        self.cards.addWidget(log_box)
            else:
                current = [(r, f) for r in selected for f in self.service.findings(r['id'], limit=None) if f['category'] != 'feature']
                active_count = sum(f['state'] == 'open' and f['confidence'] == 'high' for _, f in current)
                candidate_count = len(current) - active_count
                self.chips([("active", f"코드 문제 {active_count}"), ("review", f"검토 후보 {candidate_count}"), ("resolved", "해결 기록")], self.lifecycle, self.set_lifecycle)
                bulk = QHBoxLayout()
                copy_all = button(f'전체 수정 지시문 복사 {len(current)}', self.copy_all_instructions, role='chip')
                copy_all.setToolTip('현재 선택한 저장소의 문제와 검토 후보를 함께 복사해요. 해결·오탐·설계 유지 항목은 제외해요.')
                copy_all.setEnabled(bool(current))
                bulk.addWidget(copy_all)
                recheck_all = button('수정 여부 일괄 확인', self.recheck_all, role='chip')
                recheck_all.setEnabled(bool(current) and not self.busy)
                bulk.addWidget(recheck_all)
                bulk_box = QWidget()
                bulk_box.setLayout(bulk)
                bulk.setContentsMargins(0, 0, 0, 0)
                bulk_box.setVisible(self.lifecycle != 'resolved')
                self.cards.addWidget(bulk_box)
                self.chips([("all", "전체"), ("structure", "구조"), ("placeholder_code", "더미"), ("overengineering", "과한 설계"), ("circular_dependency", "순환"), ("missing_change", "변경 누락"), ("structural_conflict", "충돌")], self.category, self.set_category, "filter")
                entries = self.brief_findings(selected, current)
                for index, (_, finding) in enumerate(entries):
                    if finding["id"] in self.expanded:
                        self.finding_page = index // FINDINGS_PER_PAGE
                        break
                last_page = max(0, (len(entries) - 1) // FINDINGS_PER_PAGE)
                self.finding_page = min(self.finding_page, last_page)
                start = self.finding_page * FINDINGS_PER_PAGE
                for repo in selected:
                    if repo["error"]:
                        self.card(repo["name"] + " · 확인이 필요해요", repo["error"], "분석 미완료")
                for repo, finding in entries[start:start + FINDINGS_PER_PAGE]:
                    self.finding_card(repo, finding)
                if last_page:
                    navigation = QHBoxLayout()
                    prev = button("이전", lambda: self.move_findings(-1), role="chip")
                    prev.setEnabled(self.finding_page > 0)
                    following = button("다음", lambda: self.move_findings(1), role="chip")
                    following.setEnabled(self.finding_page < last_page)
                    navigation.addWidget(prev)
                    navigation.addWidget(label(f"{start + 1}–{min(start + FINDINGS_PER_PAGE, len(entries))} / {len(entries)}", 12, MUTED), 1)
                    navigation.addWidget(following)
                    self.cards.addLayout(navigation)
                if not entries:
                    errors = sum(bool(r["error"]) for r in selected)
                    unreviewed = sum(not bool(r["last_head"]) for r in selected)
                    if self.lifecycle == "resolved":
                        self.card("확인된 해결 기록이 아직 없어요", "수정 근거를 확인한 항목만 이곳에 모아요. 경고가 안 보이거나 PR이 닫힌 것만으로 해결 처리하지 않아요.")
                    elif self.busy:
                        self.card("아직 살펴보는 중이에요", "수집과 AI 분석을 마치면 결과가 여기에 나타나요.")
                    elif errors:
                        self.card("분석을 마치지 못한 저장소가 있어요", "위 안내를 확인해 주세요. 아직 문제 유무를 판단할 수 없는 상태예요.")
                    elif unreviewed:
                        self.card(f"아직 분석하지 않은 저장소 {unreviewed}곳", "‘지금 분석’을 누르면 연결한 코드와 전체 브랜치를 순서대로 살펴봐요.")
                    elif self.category != "all":
                        self.card("이 분류의 항목은 없어요", "다른 분류의 결과는 ‘전체’에서 확인할 수 있어요.")
                    else:
                        self.card("검토 후보가 없어요" if self.lifecycle == "review" else "현재 확실한 코드 문제는 없어요", "추가 판단이 필요한 항목은 ‘검토 후보’에서 확인해 주세요.")
                self.cards.addWidget(label("보고 있는 저장소", 12, MUTED, True))
                for repo in selected:
                    catalog = self.service.catalog(repo["id"])
                    total = len(catalog["branches"])
                    count = len(included_branches(repo, catalog))
                    scope = f"브랜치 {count}/{total}개" if total else "선택한 브랜치"
                    if repo["open_prs"]: scope += f" · 열린 PR {len(included_pulls(repo, catalog.get('pulls', [])))}개" if catalog["checked"] else " · 열린 PR 자동"
                    self.cards.addWidget(label(repo["name"] + " · " + scope, 12, MUTED))
                    coverage = self.service.latest_coverage(repo['id'])
                    if coverage.get('omitted_files'):
                        self.cards.addWidget(label(f"읽지 못한 파일 {coverage['omitted_files']}개가 있어요. 전체 검토는 아직 미완료예요.", 12, '#9a5637'))
                    if 'full_source_review' in coverage:
                        done,total=coverage.get('completed_batches',0),coverage.get('ai_batches',0)
                        text=(f"전체 소스 {coverage.get('source_files',coverage.get('read_files',0))}개 · AI 검토 {done}/{total}묶음")
                        if not coverage['full_source_review']: text+=' · 미완료'
                        self.cards.addWidget(label(text,12,MUTED))
        elif self.page == "repos":
            row = QHBoxLayout()
            row.addWidget(label(f"연결한 저장소 {len(repos)}곳", 15, BROWN, True), 1)
            full_scan = button("전체 점검", self.scan_all, role="chip")
            full_scan.setToolTip("이번에만 모든 저장소의 대상 브랜치와 열린 PR을 확인해요. 체크를 푼 브랜치는 제외해요.")
            full_scan.setEnabled(bool(repos) and not self.busy)
            row.addWidget(full_scan)
            self.cards.addLayout(row)
            for repo in repos:
                catalog = self.service.catalog(repo["id"])
                total = len(catalog["branches"])
                count = len(included_branches(repo, catalog))
                scope = f"브랜치 {count}/{total}개" if total else "선택한 브랜치"
                detail = scope + (" · 미커밋 포함" if repo["include_working"] else "")
                if repo["open_prs"]: detail += f" · 열린 PR {len(included_pulls(repo, catalog.get('pulls', [])))}개" if catalog["checked"] else " · 열린 PR 자동"
                inner = self.card(repo["name"], detail, "변경 시 점검 " + ("켜짐" if repo["auto"] else "꺼짐"))
                row = QHBoxLayout()
                for title, fn in [("브랜치 보기", lambda r=repo:self.branches(r["id"])), ("설정", lambda r=repo:self.edit_repo(r["id"]))]:
                    b = button(title, fn)
                    b.setEnabled(not self.busy)
                    row.addWidget(b)
                row.addStretch()
                inner.addLayout(row)
            if not repos:
                self.card("내 저장소를 한 번에", "GitHub 로그인 후 목록에서 여러 저장소를 고르면 바로 분석을 시작해요.")
            self.cards.addWidget(label("선택한 저장소의 새 브랜치와 열린 PR도 자동으로 따라가요. 로컬 폴더를 연결하면 미커밋 변경도 함께 볼 수 있어요.", 12, MUTED))
        else:
            self.settings_content()
        self.cards.addStretch()
        self.rendered_page = self.page
        QTimer.singleShot(0, self, lambda: self.scroll.verticalScrollBar().setValue(position) if generation == self.render_generation else None)

    def brief_findings(self, selected, current):
        if self.lifecycle == "resolved":
            entries = [(repo, f) for repo in selected for f in self.service.findings(repo["id"], True, limit=None)
                       if f["state"] == "resolved" and f["category"] != "feature"]
        else:
            entries = [(repo, f) for repo, f in current
                       if (f["state"] == "open" and f["confidence"] == "high") == (self.lifecycle == "active")]
        latest = {}
        for repo, finding in current:
            latest[repo["id"]] = max(latest.get(repo["id"], 0), finding["last_seen"])

        def order(pair):
            repo, f = pair
            new = f["state"] == "open" and f["first_seen"] == f["last_seen"] == latest.get(repo["id"])
            return (not new, f["confidence"] != "high", f["severity"] != "problem", -f["last_seen"], f["id"])

        return sorted((pair for pair in entries if self.category in {"all", pair[1]["category"]}), key=order)

    def move_findings(self, offset):
        self.finding_page = max(0, self.finding_page + offset)
        self.expanded.clear()
        self.refresh(reset_scroll=True)

    def set_category(self, category):
        self.category = category
        self.finding_page = 0
        self.expanded.clear()
        self.refresh(reset_scroll=True)

    def set_lifecycle(self, lifecycle):
        self.lifecycle = lifecycle
        self.finding_page = 0
        self.expanded.clear()
        self.refresh(reset_scroll=True)

    def finding_card(self, repo, finding):
        card, inner = frame(self.cards, finding["confidence"] == "high" and finding["state"] == "open")
        if finding["id"] in self.expanded: card.setObjectName("detail")
        row = QHBoxLayout()
        row.setSpacing(7)
        mark = label("·" if finding['disposition'] != 'open' else "✓" if finding["state"] == "resolved" else ("!" if finding["severity"] == "problem" else "·"), 14, MUTED if finding['disposition'] != 'open' else "#54846a" if finding["state"] == "resolved" else ACCENT, True)
        row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
        # A wrapping label keeps long Korean titles inside the popover.
        heading = label(finding["title"], 14, BROWN, True)
        heading.setProperty("findingId", finding["id"])
        heading.setAccessibleName(finding["title"] + " · 펼치기 또는 접기")
        heading.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        heading.setCursor(Qt.CursorShape.PointingHandCursor)
        heading.mousePressEvent = lambda event:self.toggle_finding(finding["id"])
        heading.keyPressEvent = lambda event:self.toggle_finding(finding["id"]) if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Space) else event.ignore()
        row.addWidget(heading, 1)
        row.addWidget(button("접기" if finding["id"] in self.expanded else "상세", lambda:self.toggle_finding(finding["id"]), role="icon"), 0, Qt.AlignmentFlag.AlignTop)
        inner.addLayout(row)
        tags = repo["name"] + " · " + CATEGORIES[finding["category"]]
        if finding['state'] != 'resolved' and finding['disposition'] == 'open' and finding["confidence"] != "high": tags += " · 검토 후보"
        if finding["disposition"] != "open": tags += " · " + ("설계 유지" if finding["disposition"] == "accepted" else "오탐")
        elif finding["category"] != "feature":
            open_label = '병합 조건 확인' if finding.get('integration_only') else '확인 필요' if finding['confidence'] != 'high' else '아직 남아 있어요'
            tags += " · " + {"open": open_label, "resolved": "코드 기준 해결 확인", "uncertain": "확인 보류", "not_seen": "재확인 필요"}.get(finding["state"], "재확인 필요")
            if finding.get("reopened_count") and finding["state"] == "open": tags += " · 재발"
        inner.addWidget(label(tags, 11, MUTED))
        if finding["id"] in self.expanded:
            resolved = finding['state'] == 'resolved'
            actionable = not resolved and finding['disposition'] == 'open'
            inner.addWidget(label('수정 대상에서 제외한 항목이에요' if finding['disposition'] != 'open' else '어떤 문제였나요' if resolved else '함께 병합할 때 확인해 주세요' if finding.get('integration_only') else '확인이 필요한 이유' if finding['confidence'] != 'high' else '어떤 문제인가요', 14, BROWN, True))
            if finding.get('integration_only'):
                inner.addWidget(label('선택한 변경을 함께 병합할 때의 충돌이에요. 각 브랜치가 단독으로 잘못됐다는 뜻은 아니에요.', 12, MUTED))
            if finding.get('review_reason') and not resolved:
                inner.addWidget(label(finding['review_reason'], 12, MUTED))
            checked = finding.get('verification', {})
            if finding['state'] in {'uncertain', 'not_seen'} and checked.get('reason'):
                inner.addWidget(label('해결 확인 보류 · ' + checked['reason'], 12, '#9a5637'))
            inner.addWidget(label(finding["detail"], 13))
            assessment = finding.get('assessment', {})
            if assessment.get('trigger'): inner.addWidget(label('발생 조건 · ' + assessment['trigger'], 12, MUTED))
            sources = finding.get('sources', {})
            def evidence_row(layout, evidence):
                name = sources.get(evidence.get('view'), {}).get('label', '코드')
                role = {'implementation':'문제 구현','caller':'호출부','alternative':'기존 구현','requirement':'요구사항','contract':'연결 계약'}.get(evidence.get('role'))
                if role: name = role + ' · ' + name
                layout.addWidget(label(f"{evidence['path']}:{evidence['line']}", 12, BROWN, True))
                reference = label(name if len(name)<48 else name[:45]+'…', 11, MUTED)
                reference.setToolTip(name); layout.addWidget(reference)
                quote = label(evidence['quote'], 12)
                quote.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                quote.setStyleSheet("font-family:'Consolas','Malgun Gothic';font-size:14px;background:#fff6de;border-left:3px solid #ecdcbf;padding:8px;")
                layout.addWidget(quote)
            if not resolved and finding['evidence']:
                evidence_row(inner, finding['evidence'][0])
            inner.addWidget(FindingHistory(self, repo, finding))
            evidence_box = QWidget()
            evidence_layout = QVBoxLayout(evidence_box)
            evidence_layout.setContentsMargins(0, 4, 0, 4)
            for evidence in finding['evidence']: evidence_row(evidence_layout, evidence)
            evidence_box.hide()
            def toggle_evidence():
                visible = evidence_box.isHidden()
                evidence_box.setVisible(visible)
                evidence_button.setText('코드 근거 접기' if visible else f"코드 근거 {len(finding['evidence'])}곳 보기")
            evidence_button = button(f"코드 근거 {len(finding['evidence'])}곳 보기", toggle_evidence, role='chip')
            inner.addWidget(evidence_button); inner.addWidget(evidence_box)
            if finding['category'] != 'feature':
                if actionable:
                    copy = button('AI 수정 지시문 복사', lambda:self.copy_instruction(repo, finding, copy), True)
                    inner.addWidget(copy)
                    instruction = label(repair_prompt(repo, finding), 13)
                    instruction.hide()
                    def toggle_instruction():
                        visible = instruction.isHidden()
                        instruction.setVisible(visible)
                        instruction_button.setText('지시문 접기' if visible else '수정 지시문 내용 보기')
                    instruction_button = button('수정 지시문 내용 보기', toggle_instruction, role='chip')
                    inner.addWidget(instruction_button); inner.addWidget(instruction)
                self.actions_evidence(inner, repo, finding)
                row = QHBoxLayout()
                row.addWidget(button('원문', lambda:self.open_evidence(finding), role='icon'))
                row.addStretch()
                if actionable:
                    row.addWidget(button('설계 유지', lambda:self.dismiss(finding, 'accepted'), role='icon'))
                    row.addWidget(button('오탐', lambda:self.dismiss(finding, 'dismissed'), role='icon'))
                inner.addLayout(row)
                if finding['disposition'] != 'open':
                    inner.addWidget(button('다시 검토 대상으로', lambda:self.dismiss(finding, 'open'), role='chip'))
                if finding['disposition']=='open':
                    recheck = button('현재 코드로 다시 확인' if resolved else '수정됐는지 확인', lambda:self.recheck_finding(repo, finding), role='chip')
                    recheck.setEnabled(not self.busy)
                    inner.addWidget(recheck)

    def actions_evidence(self, layout, repo, finding):
        targets, scope = finding_targets(finding)
        if scope:
            layout.addWidget(label(" · ".join(scope), 12, MUTED))
        if not targets or not (repo["source"].startswith("https://github.com/") or finding.get("web")):
            return
        report = self.service.actions_report(repo["id"])
        available = report.get("commits", {})
        if report.get("error"):
            layout.addWidget(label(report["error"], 12, MUTED))
        else:
            for target in targets:
                head = target["head"]
                commit = available.get(head)
                if commit is None:
                    continue
                state = STATE_LABELS.get(commit["state"], "검사 결과 미확인")
                layout.addWidget(label(f'{target["label"]} · {head[:8]} · {state} · {commit["tests"]}', 12, MUTED))
                failure = next((run for run in commit.get("runs", ()) if needs_attention(run)), None)
                if failure:
                    detail = failure.get("diagnosis", {}).get("cause")
                    if detail:
                        layout.addWidget(label(detail, 12))
                    layout.addWidget(button("실패 실행 보기", lambda run=failure: QDesktopServices.openUrl(QUrl(run["url"])), role="chip"))
                    layout.addWidget(button("테스트 수정 지시문 복사", lambda run=failure: QApplication.clipboard().setText(repair_instruction(report, run)), role="chip"))
        pending = repo["id"] in self.actions_pending
        text = "검사 결과를 가져오고 있어요" if pending else "GitHub 검사 결과 보기"
        lookup = button(text, lambda: self.load_actions(repo["id"], targets), role="chip")
        lookup.setEnabled(not pending)
        layout.addWidget(lookup)

    def load_actions(self, repo_id, targets):
        if repo_id in self.actions_pending:
            return
        self.actions_pending.add(repo_id)
        worker = Worker(lambda: self.service.refresh_actions(repo_id, targets, on_demand=True))
        self.workers.append(worker)
        def finished():
            self.actions_pending.discard(repo_id)
            self.workers.remove(worker)
            self.refresh()
        worker.signals.error.connect(lambda error: self.status.setText(error))
        worker.signals.finished.connect(finished)
        self.refresh()
        QThreadPool.globalInstance().start(worker)

    def recheck_finding(self, repo, finding):
        if self.busy or QApplication.activeModalWidget(): return
        self.recheck_id, self.repo_id = finding["id"], repo["id"]
        self.expanded = {finding["id"]}
        self.service.mark_read(finding["id"])
        self.recheck_targets = {repo['id']: [finding['id']]}
        self.scan_now(repo["id"], force=True)

    def current_issue_entries(self):
        return [(repo, finding) for repo in self.service.repos() if self.repo_id is None or repo['id'] == self.repo_id
                for finding in self.service.findings(repo['id'], limit=None) if finding['category'] != 'feature']

    def copy_all_instructions(self):
        entries = self.current_issue_entries()
        prompt = batch_repair_prompt(entries)
        if not prompt:
            self.status.setText('복사할 미해결 항목이 없어요.'); return
        QApplication.clipboard().setText(prompt)
        self.status.setText(f'전체 {len(entries)}개 수정 지시문을 복사했어요.')

    def recheck_all(self):
        if self.busy or QApplication.activeModalWidget(): return
        self.recheck_targets = {}
        for repo, finding in self.current_issue_entries():
            self.recheck_targets.setdefault(repo['id'], []).append(finding['id'])
        if not self.recheck_targets: return
        self.recheck_id = None
        self.full_scope_scan, self.scan_audit = False, None
        self.queue = list(self.recheck_targets)
        self.force_repos = set(self.queue)
        self.scan_next()

    def toggle_finding(self, finding_id):
        self.expanded = set() if finding_id in self.expanded else {finding_id}
        if self.expanded: self.service.mark_read(finding_id)
        self.refresh()
        if self.expanded: self.focus_finding(finding_id)

    def focus_finding(self, finding_id):
        def reveal():
            if finding_id not in self.expanded: return
            for heading in self.scroll.findChildren(QLabel):
                if heading.isVisible() and heading.property("findingId") == finding_id:
                    y = heading.mapTo(self.scroll.widget(), heading.rect().topLeft()).y()
                    self.scroll.verticalScrollBar().setValue(max(0, y-16))
                    break
        QTimer.singleShot(0, self, reveal)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            if self.page in {"brief", "history"} and self.expanded:
                self.expanded.clear()
                self.refresh()
            else: self.hide()
        else: super().keyPressEvent(event)

    def showEvent(self, event):
        if self.sprite: self.sprite.bubble.hide()
        super().showEvent(event)

    def show_finding(self, finding):
        self.expanded = {finding["id"]}
        self.service.mark_read(finding["id"])
        self.current_finding = finding
        self.repo_id, self.category = finding["repo_id"], "all"
        self.lifecycle = "resolved" if finding["state"] == "resolved" else "review" if finding["state"] != "open" or finding["confidence"] != "high" else "active"
        self.page = "brief"
        self.refresh(reset_scroll=True)
        self.focus_finding(finding["id"])

    def copy_instruction(self, repo, finding, source_button=None):
        QApplication.clipboard().setText(repair_prompt(repo, finding))
        self.status.setText("복사했어요. 사용 중인 AI 대화에 붙여 넣어 주세요.")
        if self.sprite: self.sprite.actions.pulse("happy", 1)
        if source_button:
            source_button.setText("복사했어요")
            timer = getattr(source_button, "copy_timer", None)
            if timer is None:
                timer = source_button.copy_timer = QTimer(source_button)
                timer.setSingleShot(True)
                timer.timeout.connect(lambda:source_button.setText("AI 수정 지시문 복사"))
            timer.start(2200)

    def open_evidence(self, finding):
        from urllib.parse import quote
        sources = finding.get("sources", {})
        e = next((e for e in finding["evidence"] if not sources or sources.get(e.get("view"), {}).get("kind") in {"base", "branch", "pr"}), None)
        source = sources.get(e.get("view"), {}) if e else {}
        web = source.get("web") or finding.get("web")
        if web and e:
            QDesktopServices.openUrl(QUrl(web + "/blob/" + (source.get("head") or finding["head"]) + "/" + quote(e["path"]) + "#L" + str(e["line"])))
        else:
            self.status.setText("위 근거의 파일과 줄을 확인해 주세요. 가상 통합본은 원문과 줄 번호가 다를 수 있어요.")

    def dismiss(self, finding, value):
        self.service.disposition(finding["id"], value)
        self.refresh()

    def add_repo(self):
        if self.busy: return
        dialog = GitHubDialog(self)
        if dialog.exec():
            self.repo_id, self.page = None, "brief"
            self.reload_repos()
            self.save_settings()
            self.queue = dialog.saved_ids
            self.scan_next()

    def add_manual_repo(self, source="", local=False):
        if self.busy: return
        dialog = RepoDialog(self, source=source, local=local)
        if dialog.exec():
            self.repo_id, self.page = None, "repos"
            self.reload_repos()
            self.save_settings()
            self.status.setText("연결했어요. ‘브랜치 보기’에서 전체 목록을 확인할 수 있어요.")
            if self.sprite: self.sprite.actions.pulse("success", 1.5)
            self.queue = dialog.saved_ids
            self.scan_next()

    def add_local_repo(self):
        if self.busy: return
        folder = QFileDialog.getExistingDirectory(self, "연결할 로컬 Git 폴더")
        if folder: self.add_manual_repo(source=folder, local=True)

    def folder_drop(self, urls):
        if self.busy:
            self.status.setText("지금 분석을 마친 뒤 폴더를 연결해 주세요.")
            return
        folders = [url.toLocalFile() for url in urls if url.isLocalFile() and Path(url.toLocalFile()).is_dir()]
        if folders:
            if self.sprite: self.sprite.actions.pulse("greeting", 1)
            QTimer.singleShot(0, lambda:self.add_manual_repo("\n".join(folders), local=True))

    def dragEnterEvent(self, event):
        if any(url.isLocalFile() for url in event.mimeData().urls()): event.acceptProposedAction()

    def dropEvent(self, event):
        self.folder_drop(event.mimeData().urls())
        event.acceptProposedAction()

    def customize_character(self):
        from .character_ui import CharacterDialog
        dialog = CharacterDialog(self)
        if dialog.exec():
            self.status.setText("새 비서로 바꿨어요. 두 번 눌러 토닥여 보세요.")

    def other_connections(self):
        from .other_connections import OtherConnectionsDialog
        if self.sprite:
            self.sprite.bubble.hide()
            self.sprite.place_popup(self)
        dialog=OtherConnectionsDialog(self)
        dialog.exec()
        dialog.deleteLater()

    def edit_repo(self, repo_id):
        if self.busy: return
        if RepoDialog(self, self.service.repo(repo_id)).exec():
            self.reload_repos()
            self.save_settings()
            QTimer.singleShot(0, self.tick)

    def branches(self, repo_id):
        if self.busy: return
        if BranchDialog(self, repo_id).exec():
            self.refresh()
            self.status.setText("분석 범위를 저장했어요. ‘지금 분석’으로 살펴볼게요.")
            QTimer.singleShot(0, self.tick)

    def settings_content(self):
        self.cards.addWidget(label("비서 설정", 15, BROWN, True))
        if os.name == 'nt':
            from . import startup
            start = QCheckBox('Windows 로그인할 때 자동 실행')
            start.setChecked(startup.is_enabled())
            def save_startup(enabled):
                try:
                    startup.set_enabled(enabled)
                    self.status.setText('자동 실행을 켰어요.' if enabled else '자동 실행을 껐어요.')
                except OSError:
                    start.blockSignals(True); start.setChecked(startup.is_enabled()); start.blockSignals(False)
                    self.status.setText('자동 실행 설정을 저장하지 못했어요. 설치 경로를 확인해 주세요.')
            start.toggled.connect(save_startup)
            self.cards.addWidget(start)
            self.cards.addWidget(label('시작할 때 캐릭터만 표시해요. 이미 실행 중이면 하나만 유지해요.', 12, MUTED))
        self.card("표정도, 움직임도 내 취향대로", "캐릭터를 고르고 행동을 미리 보세요. 이름·크기와 내 애니메이션 팩도 바꿀 수 있어요.")
        self.cards.addWidget(button("캐릭터 바꾸기", self.customize_character, True))
        self.cards.addWidget(label("캐릭터를 숨기면 화면 가장자리에 ‘비서 열기’ 탭이 남아요. 트레이에서도 다시 열 수 있어요.", 12, MUTED))
        self.card('기타 연결','대기 중 가벼운 한마디와 현재 위치 날씨를 골라 켤 수 있어요.')
        self.cards.addWidget(button('기타 연결',self.other_connections))
        self.card("GitHub 저장소 연결", "로그인 후 저장소를 고르면 전체 브랜치와 열린 PR을 함께 봐요. 저장소 설정에서 변경 시 자동 점검을 켤 수 있어요.")
        self.cards.addWidget(button("GitHub 계정·저장소", self.add_repo))
        self.card("기존 Codex 로그인으로", "분석할 때 계정의 Codex 사용량이 소모돼요. 모델을 비우면 현재 Codex 기본 모델을 사용해요. 같은 코드의 분석 결과는 재사용해요.")
        choice = QLineEdit(self.model_draft if self.model_draft is not None else os.getenv("CODEX_MODEL", ""))
        choice.setObjectName("modelChoice")
        choice.setMaxLength(100)
        choice.textChanged.connect(lambda text:setattr(self, "model_draft", text))
        choice.setPlaceholderText("Codex 기본 모델")
        self.cards.addWidget(choice)
        def save():
            os.environ["CODEX_MODEL"] = choice.text().strip()[:100]
            self.model_draft = os.environ["CODEX_MODEL"]
            choice.setText(self.model_draft)
            self.save_settings()
            self.status.setText("모델 설정을 저장했어요.")
        self.model_save = button("모델 설정 저장", save)
        self.model_save.setEnabled(not self.busy)
        self.model_save.setToolTip("현재 분석이 끝나면 저장할 수 있어요" if self.busy else "")
        self.cards.addWidget(self.model_save)
        def connection():
            self.status.setText("연결을 확인하고 있어요…")
            worker = Worker(lambda: codex_provider.status(refresh=True))
            self.workers.append(worker)
            worker.signals.result.connect(lambda r:self.status.setText(r["label"] + " · " + r["message"]))
            worker.signals.error.connect(self.status.setText)
            worker.signals.finished.connect(lambda:self.workers.remove(worker) if worker in self.workers else None)
            QThreadPool.globalInstance().start(worker)
        self.cards.addWidget(button("로그인 연결 확인", connection))
        self.card("코드 문제에 집중", "코드 근거가 있는 문제만 알려드려요. 추가 판단이 필요한 항목은 검토 후보에 모으고, 수정 여부는 현재 코드로 확인해요.")
        self.cards.addWidget(label("자동 점검과 미커밋 포함 여부는 저장소별 설정에서 바꿀 수 있어요.", 12, MUTED))

    def ai_settings(self): self.open_tab("settings")

    def scan_now(self, repo_id=None, *, force=False):
        if self.busy or QApplication.activeModalWidget(): return
        self.full_scope_scan, self.scan_audit = False, None
        self.queue = [repo_id] if repo_id else [r["id"] for r in self.service.repos() if self.repo_id is None or r["id"] == self.repo_id]
        self.force_repos = set(self.queue) if force else set()
        self.scan_next()

    def scan_all(self):
        if self.busy or QApplication.activeModalWidget():
            self.status.setText("진행 중인 작업을 마친 뒤 전체 점검을 시작해 주세요.")
            return
        repos = self.service.repos()
        self.queue = [r["id"] for r in repos]
        self.full_scope_scan = True
        self.force_repos.clear()
        self.scan_audit = {"started": time.time(), "finished": None, "repositories": [{"id": r["id"], "name": r["name"]} for r in repos], "events": [], "results": []}
        self.write_scan_audit()
        self.scan_next()

    def write_scan_audit(self):
        if self.scan_audit is not None:
            path = self.root / ".data/last-full-scan.json"
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.scan_audit, ensure_ascii=False, indent=2), encoding="utf-8")
            temp.replace(path)

    def scan_next(self):
        if self.busy or not self.queue:
            return
        if QApplication.activeModalWidget():
            QTimer.singleShot(500, self.scan_next)
            return
        repo_id = self.queue.pop(0)
        try:
            repo = self.service.repo(repo_id)
        except ValueError:
            QTimer.singleShot(0, self.scan_next)
            return
        self.busy = True
        self.progress_text = "변경을 살펴보고 있어요"
        if self.sprite and self.sprite.bubble.mode=='chatter': self.sprite.bubble.hide()
        self.subtitle.setText("살펴보는 중…")
        self.status.setText(repo["name"] + (f" · 다음 저장소 {len(self.queue)}곳" if self.queue else " · 변경을 살펴보고 있어요"))
        self.refresh()
        token = self.sprite.actions.begin("searching") if self.sprite else None
        options = {"force": True} if repo_id in self.force_repos else {}
        if repo_id in self.recheck_targets: options['recheck_ids'] = self.recheck_targets.pop(repo_id)
        if self.full_scope_scan: options["all_scope"] = True
        self.force_repos.discard(repo_id)
        self.changed_repos.discard(repo_id)
        self.watcher.started(repo_id)
        from .review_process import review
        worker = Worker(lambda:review(self.service, repo_id, progress=worker.signals.progress.emit, **options))
        self.workers.append(worker)
        worker.signals.progress.connect(lambda action, text:self.progress(token, repo["name"], action, text))
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(lambda error:self.failed(repo_id, error))
        worker.signals.finished.connect(lambda:self.finished(worker, token))
        QThreadPool.globalInstance().start(worker)

    def progress(self, token, name, action, text):
        if action == "findings":
            self.refresh()
            action = "thinking"
        self.progress_text = text
        if self.scan_audit is not None:
            self.scan_audit["events"].append({"stamp": time.time(), "repository": name, "stage": action, "message": text})
            self.write_scan_audit()
        if self.sprite: self.sprite.actions.update(token, action)
        self.subtitle.setText({"searching": "변경 수집 중", "thinking": "AI 분석 중", "organizing": "브리핑 정리 중"}[action])
        self.status.setText(name + " · " + text)
        if self.sprite and self.sprite.bubble.isVisible() and self.sprite.bubble.mode == "progress":
            self.sprite.bubble.show_message(text, mode="progress")

    def received(self, result):
        self.watcher.completed(result['repo_id'], not bool(result.get('ai_error') or result.get('followup_error')),
                               result.get('watch_token'), reviewed=not result.get('recheck_only'))
        if self.scan_audit is not None:
            self.scan_audit["results"].append({"repo_id": result["repo_id"], "selection": result.get("selection", {}),
                "cached": bool(result.get("cached")),
                "findings": None if result.get("cached") else len(result.get("findings", [])), "coverage": result.get("coverage", {}),
                "ai_error": result.get("ai_error", ""), "followup_error": result.get("followup_error", "")})
            self.write_scan_audit()
        if self.recheck_id is not None:
            finding = next((f for f in self.service.findings(result["repo_id"], True, limit=None) if f["id"] == self.recheck_id), None)
            if finding:
                self.lifecycle = "resolved" if finding["state"] == "resolved" else "review" if finding["state"] != "open" or finding["confidence"] != "high" else "active"
                self.page, self.category = "brief", "all"
            self.recheck_id = None
        self.refresh()
        counts = result.get("followup_counts", {})
        self.status.setText("코드가 같아 이전 분석을 유지해요." if result.get("cached") else
                            (f"코드 기준 해결 {counts.get('resolved', 0)} · 확인 보류 {counts.get('uncertain', 0)} · 해결은 조용히 기록해요." if counts else "브리핑에 분석 결과를 정리했어요."))
        self.notify(result["repo_id"])

    def failed(self, repo_id, error):
        self.watcher.completed(repo_id, False)
        if self.scan_audit is not None:
            self.scan_audit["results"].append({"repo_id": repo_id, "error": error})
            self.write_scan_audit()
        self.recheck_id = None
        self.status.setText("일부 저장소를 읽지 못했어요. 브리핑에서 확인해 주세요.")
        self.refresh()
        self.notify(repo_id)

    def finished(self, worker, token):
        if self.sprite: self.sprite.actions.finish(token, quiet=True)
        self.busy = False
        if self.scan_audit is not None and not self.queue:
            self.scan_audit["finished"] = time.time()
            self.write_scan_audit()
        if worker in self.workers: self.workers.remove(worker)
        self.refresh()
        if self.sprite and self.sprite.bubble.isVisible() and self.sprite.bubble.mode == "progress" and not self.queue:
            self.sprite.bubble.show_message(self.status.text(), mode="greeting")
        if self.queue: QTimer.singleShot(0, self.scan_next)
        else: QTimer.singleShot(0, self.drain_changes)

    def notify(self, repo_id):
        if not self.sprite or not self.sprite.isVisible() or self.isVisible() or QApplication.activeModalWidget(): return
        if self.sprite.bubble.isVisible() and self.sprite.bubble.mode not in {"progress","chatter","interaction","greeting"}: return
        notice = self.service.pending_notice(repo_id)
        if not notice["findings"] and not notice["error"]: return
        titles = [("다시 발견 · " if f.get("reopened_count") else "") + f["title"] for f in notice["findings"][:2]]
        if len(notice["findings"]) > 2:
            titles = [f"확인할 코드 문제 {len(notice['findings'])}건이 있어요.", titles[0]]
        if notice["error"]: titles.insert(0, "분석 연결 확인이 필요해요")
        self.latest_bubble_message = notice["repo"]["name"] + "\n" + "\n".join(titles)
        self.sprite.actions.pulse("attention", 12)
        self.sprite.bubble.show_message(self.latest_bubble_message, mode="notice", repo_id=repo_id, auto_hide_ms=12000,
                                       page="brief")
        self.sprite.place_popup(self.sprite.bubble)
        self.sprite.bubble.show()
        self.service.mark_notified(notice)

    def tick(self):
        self.watcher.poll()
        if not self.busy and not self.queue and not QApplication.activeModalWidget():
            for repo in self.service.repos(): self.notify(repo["id"])
            self.drain_changes()

    def code_changed(self, repo_id):
        self.changed_repos.add(repo_id)
        self.drain_changes()

    def drain_changes(self):
        if not self.busy and not self.queue and not QApplication.activeModalWidget() and self.changed_repos:
            enabled = {r['id'] for r in self.service.repos() if r['auto']}
            self.changed_repos.intersection_update(enabled)
            ready = sorted(rid for rid in self.changed_repos if self.watcher.ready(rid))
            if not ready: return
            # Recheck readiness after other repositories finish; newer pushes may
            # have restarted their quiet period while this repository was busy.
            self.queue = ready[:1]
            self.changed_repos.discard(ready[0])
            self.full_scope_scan, self.scan_audit = False, None
            self.scan_next()

    def closeEvent(self, event):
        self.hide()
        event.ignore()

def run(root):
    root = Path(root)
    load_env(root)
    app = QApplication(sys.argv[:1])
    app.setApplicationName(CONFIG["name"])
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)
    instance = SingleInstance(parent=app)
    if not instance.primary:
        if '--startup' not in sys.argv:
            instance.notify("scan-all" if "--scan-all" in sys.argv else "show")
        instance.close()
        return 0
    load_fonts()
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parents[1]/'assets/icons/app.ico')))
    (root / ".data").mkdir(exist_ok=True)
    # Diagnostic PID only; exclusion belongs to the OS, independent of checkout paths.
    pid_path = root / ".data/code-desktop.pid"
    pid_path.write_text(str(os.getpid()), encoding="ascii")
    panel = ReviewPanel(root)
    sprite = DesktopCharacter(CONFIG, root, panel)
    panel.sprite = sprite
    instance.activated.connect(sprite.restore)
    instance.full_scan_requested.connect(panel.scan_all)
    sprite.show()
    pixmap = QPixmap(sprite.size())
    pixmap.fill(Qt.GlobalColor.transparent)
    sprite.render(pixmap)
    tray = QSystemTrayIcon(QIcon(pixmap), app)
    tray.setToolTip("코드 비서 · 문제와 새 기능만 알려드려요")
    menu = QMenu()
    def open_brief():
        panel.open_tab("brief")
        sprite.open_panel()
    menu.addAction("코드 브리핑", open_brief)
    menu.addAction("지금 분석", panel.scan_now)
    menu.addAction("캐릭터 바꾸기", panel.customize_character)
    menu.addAction('기타 연결',panel.other_connections)
    menu.addAction("비서 다시 열기", sprite.restore)
    menu.addAction("비서 숨기기 · 열기 탭 유지", sprite.hide_character)
    menu.addSeparator()
    menu.addAction("종료", app.quit)
    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason:sprite.open_panel() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
    tray.show()
    QTimer.singleShot(1500, panel.scan_all if "--scan-all" in sys.argv else panel.tick)
    try:
        return app.exec()
    finally:
        panel.timer.stop()
        panel.idle_chat.close()
        panel.watcher.close()
        panel.queue.clear()
        from .review_process import cancel_reviews
        cancel_reviews()
        QThreadPool.globalInstance().waitForDone()
        sprite.skin.close()
        if sprite.restore_tab: sprite.restore_tab.hide()
        tray.hide()
        instance.close()
        if pid_path.exists() and pid_path.read_text(encoding="ascii") == str(os.getpid()): pid_path.unlink()
