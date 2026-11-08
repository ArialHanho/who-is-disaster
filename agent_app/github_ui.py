"""Account sign-in and multi-select repository picker, inside the resident popover."""
import re
from PySide6.QtCore import Qt, QProcess, QProcessEnvironment, QTimer, QThreadPool, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QLineEdit,
    QListWidget, QListWidgetItem, QComboBox, QAbstractItemView, QWidget)
from . import github_account
from .background import Worker
from .review_theme import STYLE, BROWN, MUTED, label, button, size_popup, RoundedDialog


class GitHubDialog(RoundedDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel, self.repositories, self.selected, self.saved_ids = panel, [], set(), []
        self.loading, self.closing, self.auth_output, self.code = False, False, "", ""
        self.auth = None
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        size_popup(self)
        self.move(panel.pos())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.addWidget(label("GitHub에서 함께 볼 저장소", 16, BROWN, True), 1)
        header.addWidget(button("✕", self.reject, role="icon"))
        layout.addLayout(header)
        self.account_label = label("GitHub 연결을 준비하고 있어요…", 13, MUTED)
        layout.addWidget(self.account_label)
        self.login = button("GitHub 로그인", self.start_login, True)
        self.login.hide()
        layout.addWidget(self.login)
        self.signin_help = label("GitHub 인증은 브라우저에서 진행해요. 연결 후 목록에서 함께 볼 저장소를 직접 고를 수 있어요.", 13, MUTED)
        layout.addWidget(self.signin_help)
        self.code_input = QLineEdit()
        self.code_input.setReadOnly(True)
        self.code_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.code_input.setAccessibleName("GitHub 일회용 로그인 코드")
        self.code_input.setStyleSheet("font-family:'Consolas','Malgun Gothic';font-size:22px;padding:10px;")
        self.code_input.hide()
        layout.addWidget(self.code_input)
        self.auth_actions = QHBoxLayout()
        self.copy_code = button("코드 복사", lambda:QApplication.clipboard().setText(self.code))
        self.open_login = button("인증 페이지 열기", lambda:QDesktopServices.openUrl(QUrl("https://github.com/login/device")))
        self.copy_code.hide()
        self.open_login.hide()
        self.auth_actions.addWidget(self.copy_code)
        self.auth_actions.addWidget(self.open_login)
        layout.addLayout(self.auth_actions)
        self.search = QLineEdit()
        self.search.setPlaceholderText("저장소 이름이나 설명으로 찾기")
        self.search.setAccessibleName("내 GitHub 저장소 검색")
        self.search.textChanged.connect(self.filter)
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.owner = QComboBox()
        self.owner.addItem("모든 계정·조직", "")
        self.owner.currentIndexChanged.connect(self.filter)
        row.addWidget(self.owner, 1)
        self.reload = button("새로고침", self.load, role="icon")
        row.addWidget(self.reload)
        layout.addLayout(row)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setAccessibleName("GitHub 저장소 여러 개 선택")
        self.list.itemChanged.connect(self.selection_changed)
        layout.addWidget(self.list, 1)
        self.signin_space = QWidget()
        layout.addWidget(self.signin_space, 1)
        row = QHBoxLayout()
        self.count = label("", 12, MUTED)
        row.addWidget(self.count, 1)
        self.select_all = button("보이는 항목 선택", self.select_visible, role="icon")
        self.clear_button = button("선택 해제", self.clear_selection, role="icon")
        row.addWidget(self.select_all)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        self.scope_help = label("전체 브랜치와 열린 PR을 함께 봐요. 커밋·PR이 달라졌을 때만 AI로 분석하며, 저장소 설정에서 자동 점검을 끌 수 있어요.", 12, MUTED)
        layout.addWidget(self.scope_help)
        self.error = label("", 12, "#d65151")
        layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addWidget(button("로컬 폴더·주소 연결", self.manual, role="icon"))
        row.addStretch()
        self.confirm = button("선택한 저장소 연결", self.save, True)
        self.confirm.setEnabled(False)
        row.addWidget(self.confirm)
        layout.addLayout(row)
        self.login_timeout = QTimer(self)
        self.login_timeout.setSingleShot(True)
        self.login_timeout.timeout.connect(self.expired)
        self.picker_visible(False)
        QTimer.singleShot(0, self.load)

    def picker_visible(self, visible):
        size_popup(self, height=720 if visible else 480)
        for widget in (self.search, self.owner, self.reload, self.list, self.count, self.select_all, self.clear_button, self.scope_help, self.confirm):
            widget.setVisible(visible)
        self.signin_help.setVisible(not visible)
        self.signin_space.setVisible(not visible)

    def load(self):
        if self.loading or self.closing:
            return
        self.loading = True
        self.reload.setEnabled(False)
        self.confirm.setEnabled(False)
        self.error.setText("")
        def fetch():
            github_account.ensure_cli()
            return github_account.repositories()
        worker = Worker(fetch)
        self.panel.workers.append(worker)
        worker.signals.result.connect(self.received)
        worker.signals.error.connect(self.failed)
        worker.signals.finished.connect(lambda:self.finished(worker))
        QThreadPool.globalInstance().start(worker)

    def finished(self, worker):
        self.loading = False
        self.reload.setEnabled(True)
        if worker in self.panel.workers:
            self.panel.workers.remove(worker)
        self.selection_changed()

    def failed(self, message):
        if not self.closing:
            self.error.setText(message)
            self.login.show()

    def received(self, data):
        if self.closing:
            return
        self.picker_visible(data["connected"])
        if not data["connected"]:
            self.repositories, self.selected = [], set()
            self.list.clear()
            self.account_label.setText("GitHub에 한 번 로그인하면 접근 가능한 저장소가 모두 나타나요.")
            self.login.show()
            self.search.setEnabled(False)
            self.owner.setEnabled(False)
            self.list.setEnabled(False)
            return
        self.account_label.setText(data["login"] + " 계정으로 연결됐어요")
        self.login.setText("다른 계정으로 연결")
        self.login.show()
        self.repositories = data["repositories"]
        self.search.setEnabled(True)
        self.owner.setEnabled(True)
        self.list.setEnabled(True)
        self.owner.blockSignals(True)
        self.owner.clear()
        self.owner.addItem("모든 계정·조직", "")
        for owner in sorted({r["owner"] for r in self.repositories}, key=str.casefold):
            self.owner.addItem(owner, owner)
        self.owner.blockSignals(False)
        self.list.blockSignals(True)
        self.list.clear()
        existing = {r["source"] for r in self.panel.service.repos()}
        for repo in self.repositories:
            badges = ["비공개" if repo["private"] else "공개"]
            if repo["archived"]: badges.append("보관됨")
            if repo["source"] in existing: badges.append("연결됨")
            item = QListWidgetItem(repo["name"] + "\n" + " · ".join(badges))
            item.setToolTip(repo["description"] or repo["name"])
            item.setData(Qt.ItemDataRole.UserRole, repo["id"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if repo["id"] in self.selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.selected.intersection_update(r["id"] for r in self.repositories)
        self.list.blockSignals(False)
        self.filter()
        self.selection_changed()

    def filter(self, *_):
        text, owner = self.search.text().strip().casefold(), self.owner.currentData()
        for i, repo in enumerate(self.repositories):
            self.list.item(i).setHidden(bool(owner and owner != repo["owner"]) or text not in (repo["name"] + " " + repo["description"]).casefold())

    def selection_changed(self, *_):
        self.selected = {self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count()) if self.list.item(i).checkState() == Qt.CheckState.Checked}
        self.count.setText(f"전체 {len(self.repositories)}개 · 선택 {len(self.selected)}개")
        self.confirm.setEnabled(bool(self.selected) and not self.loading)

    def select_visible(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()):
            if not self.list.item(i).isHidden():
                self.list.item(i).setCheckState(Qt.CheckState.Checked)
        self.list.blockSignals(False)
        self.selection_changed()

    def clear_selection(self):
        self.list.blockSignals(True)
        for i in range(self.list.count()): self.list.item(i).setCheckState(Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self.selection_changed()

    def start_login(self):
        if self.auth and self.auth.state() != QProcess.ProcessState.NotRunning:
            return
        executable = github_account.cli_path()
        if not executable:
            self.load()
            return
        self.auth_output, self.code = "", ""
        self.picker_visible(False)
        self.selected.clear()
        self.repositories = []
        self.list.clear()
        self.confirm.setEnabled(False)
        self.login.setEnabled(False)
        self.reload.setEnabled(False)
        self.error.setText("")
        self.account_label.setText("일회용 로그인 코드를 준비하고 있어요…")
        self.auth = QProcess(self)
        env = QProcessEnvironment()
        for key, value in github_account.environment().items(): env.insert(key, value)
        self.auth.setProcessEnvironment(env)
        self.auth.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.auth.readyReadStandardOutput.connect(self.login_output)
        self.auth.finished.connect(self.login_finished)
        self.auth.errorOccurred.connect(self.login_error)
        self.auth.setProgram(str(executable))
        self.auth.setArguments(["auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web", "--skip-ssh-key"])
        self.auth.start()
        self.login_timeout.start(15 * 60 * 1000)

    def login_output(self):
        if self.closing: return
        self.auth_output = (self.auth_output + bytes(self.auth.readAllStandardOutput()).decode("utf-8", "replace"))[-8000:]
        match = re.search(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b", self.auth_output)
        if match and not self.code:
            self.code = match[1]
            self.code_input.setText(self.code)
            self.code_input.show()
            self.copy_code.show()
            self.open_login.show()
            self.account_label.setText("GitHub 인증 페이지에서 이 코드를 입력하고 연결을 승인해 주세요.")
            # gh with piped I/O prints the URL without opening it; use the desktop browser.
            QDesktopServices.openUrl(QUrl("https://github.com/login/device"))

    def login_finished(self, exit_code, *_):
        self.login_timeout.stop()
        self.auth_output, self.code = "", ""
        self.code_input.clear()
        for w in (self.code_input, self.copy_code, self.open_login): w.hide()
        self.login.setEnabled(True)
        self.reload.setEnabled(True)
        if self.closing: return
        if exit_code == 0:
            self.account_label.setText("저장소 목록을 가져오고 있어요…")
            self.load()
        else:
            self.error.setText("GitHub 연결이 완료되지 않았어요. 로그인을 다시 시도해 주세요.")

    def login_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.login_timeout.stop()
            self.login.setEnabled(True)
            self.reload.setEnabled(True)
            self.failed("GitHub 로그인을 시작하지 못했어요. 다시 연결해 주세요.")

    def expired(self):
        if self.auth and self.auth.state() != QProcess.ProcessState.NotRunning: self.auth.kill()
        self.error.setText("로그인 코드가 만료됐어요. 새 코드를 받아 다시 연결해 주세요.")

    def save(self):
        try:
            chosen = [r for r in self.repositories if r["id"] in self.selected]
            self.saved_ids = self.panel.service.connect_github(chosen)
            self.accept()
        except Exception as exc:
            self.error.setText(str(exc))

    def manual(self):
        self.reject()
        QTimer.singleShot(0, self.panel.add_manual_repo)

    def reject(self):
        self.closing = True
        self.login_timeout.stop()
        if self.auth and self.auth.state() != QProcess.ProcessState.NotRunning:
            self.auth.kill()
            self.auth.waitForFinished(1000)
        super().reject()
