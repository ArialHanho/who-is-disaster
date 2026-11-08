"""Live character chooser; preview stays separate from real analysis state."""
import time
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QDialog, QVBoxLayout, QHBoxLayout, QComboBox, QLineEdit, QCheckBox, QFileDialog
from .character_settings import BUILTINS, SCALES, load_preferences, save_preferences, packs, install_pack
from .character_render import draw_character
from .skin import selected_skin
from .portrait_motion import PortraitMotion, PortraitPose, PhotoGestureMotion, character_geometry
from .review_theme import STYLE, BROWN, MUTED, button, label, size_popup, scroll_body, FlowLayout, RoundedDialog


class CharacterPreview(QWidget):
    def __init__(self, root, config, preferences):
        super().__init__()
        self.root, self.config, self.preferences = root, dict(config), preferences
        self.frame, self.state = 0, "idle"
        self.motion, self.pose, self.last_tick = PortraitMotion(), PortraitPose(), time.monotonic()
        self.gesture_motion = PhotoGestureMotion()
        self.setFixedHeight(214)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("눌러서 토닥여 보세요")
        self.skin = selected_skin(root, self.update, preferences)
        self.setFixedHeight(character_geometry(self.skin.layout, preferences["scale"], self.skin.pose_motion)[2]+10)
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.advance)
        self.timer.start(16 if self.skin.continuous_motion and not preferences["reduced_motion"] else 40)

    def advance(self):
        now = time.monotonic()
        dt, self.last_tick = min(.05, max(0, now-self.last_tick)), now
        if not self.isVisible(): return
        self.frame += dt*10
        if self.skin.continuous_motion:
            self.pose = (self.gesture_motion.step(dt, held=True, moving=True, reduced=self.preferences['reduced_motion'])
                         if self.state == 'dragging' else self.motion.step(dt, self.state, expressive=self.skin.pose_motion))
        self.update()

    def change(self, preferences):
        replace = any(preferences[key] != self.preferences[key] for key in ("character", "reduced_motion"))
        self.preferences = preferences
        if replace:
            self.skin.close()
            self.skin = selected_skin(self.root, self.update, preferences)
            self.motion, self.pose = PortraitMotion(), PortraitPose()
        self.setFixedHeight(character_geometry(self.skin.layout, preferences["scale"], self.skin.pose_motion)[2]+10)
        self.timer.setInterval(16 if self.skin.continuous_motion and not preferences["reduced_motion"] else 40)
        self.config["character_name"] = preferences["name"] or "코드 비서"
        self.update()

    def action(self, state):
        self.frame, self.state = 0, state
        self.motion.age = 0
        self.gesture_motion = PhotoGestureMotion()
        self.skin.restart_pose()
        self.update()

    def mousePressEvent(self, event): self.action("happy")

    def paintEvent(self, event):
        p = QPainter(self)
        height, width, _ = character_geometry(self.skin.layout, self.preferences["scale"], self.skin.pose_motion)
        width = width if self.skin.pose_motion else 142
        scale = min(self.width()/width, self.height()/height, 1 if self.skin.layout == "portrait" else self.preferences["scale"]/100)
        p.translate((self.width()-width*scale)/2, (self.height()-height*scale)/2)
        p.scale(scale, scale)
        p.translate((width-142)/2,0)
        draw_character(p, self.config, self.frame, self.state, style=self.preferences["character"],
                       custom=self.skin.frame(self.state, self.frame), motion=not self.preferences["reduced_motion"], height=height,
                       pose=self.pose if self.skin.continuous_motion else None,
                       layers=self.skin.layers(self.state,self.frame),stage_width=width)
        p.end()


class CharacterDialog(RoundedDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self.panel, self.preferences = panel, load_preferences(panel.root)
        self.setWindowTitle("코드 비서 · 캐릭터 바꾸기")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        size_popup(self)
        self.move(panel.pos())
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 14, 20, 16)
        header = QHBoxLayout()
        header.addWidget(label("나와 함께할 비서", 17, BROWN, True), 1)
        header.addWidget(button("✕", self.reject, role="icon"))
        outer.addLayout(header)
        _, body = scroll_body(outer)
        body.setContentsMargins(0, 0, 0, 8)
        self.choice = QComboBox()
        for key, (name, _) in BUILTINS.items(): self.choice.addItem(name, key)
        if (panel.root / "character.json").exists(): self.choice.addItem("기존 사용자 캐릭터", "legacy")
        for key, name in packs(panel.root): self.choice.addItem(name, key)
        self.choice.setCurrentIndex(max(0, self.choice.findData(self.preferences["character"])))
        body.addWidget(self.choice)
        config = panel.sprite.config if panel.sprite else {"kind": "code", "character_name": "코드 비서", "accent": "#e8865a"}
        self.preview = CharacterPreview(panel.root, config, self.preferences)
        body.addWidget(self.preview)
        body.addWidget(label("행동 미리보기 · 실제 분석 결과가 아니에요", 12, MUTED))
        chips = QWidget()
        flow = FlowLayout(chips)
        self.action_buttons = {}
        for key, title in [("idle", "대기"), ("greeting", "인사"), ("happy", "만세 점프"), ("thinking", "생각"), ("organizing", "작업"), ("searching", "찾기"), ("attention", "놀람"), ("dragging", "이동"), ("sleep", "잠자기")]:
            b = button(title, lambda k=key:self.action(k), role="chip")
            b.setCheckable(True)
            b.setChecked(key == "idle")
            self.action_buttons[key] = b
            flow.addWidget(b)
        body.addWidget(chips)
        row = QHBoxLayout()
        self.name = QLineEdit(self.preferences["name"])
        self.name.setMaxLength(10)
        self.name.setPlaceholderText("비서 이름")
        self.name.setAccessibleName("비서 이름")
        row.addWidget(self.name, 1)
        self.scale = QComboBox()
        for n in SCALES: self.scale.addItem(f"크기 {n}%", n)
        self.scale.setCurrentIndex(max(0, self.scale.findData(self.preferences["scale"])))
        row.addWidget(self.scale)
        body.addLayout(row)
        self.reduced = QCheckBox("움직임 줄이기")
        self.reduced.setToolTip("캐릭터 움직임을 줄이고 말풍선 글자도 한 번에 표시해요.")
        self.reduced.setChecked(self.preferences["reduced_motion"])
        body.addWidget(self.reduced)
        body.addWidget(button("내 애니메이션 팩 가져오기", self.import_pack))
        body.addWidget(label("대기·분석·알림 동작을 담은 character.json을 고르세요. GIF, WebP, PNG 프레임·시트를 지원해요.", 12, MUTED))
        self.error = label("", 12, "#b75f4b")
        body.addWidget(self.error)
        for signal in (self.choice.currentIndexChanged, self.name.textChanged, self.scale.currentIndexChanged, self.reduced.toggled): signal.connect(self.changed)
        row = QHBoxLayout()
        row.addWidget(button("취소", self.reject, role="icon"))
        row.addStretch()
        row.addWidget(button("이 비서로 함께하기", self.save, True))
        outer.addLayout(row)

    def action(self, key):
        self.preview.action(key)
        for name, b in self.action_buttons.items(): b.setChecked(name == key)

    def changed(self, *_):
        self.preferences = {"character": self.choice.currentData(), "name": self.name.text(), "scale": self.scale.currentData(), "reduced_motion": self.reduced.isChecked()}
        self.preview.change(self.preferences)

    def import_pack(self):
        path, _ = QFileDialog.getOpenFileName(self, "캐릭터 애니메이션 팩", "", "캐릭터 설정 (*.json)")
        if not path: return
        try:
            key = install_pack(self.panel.root, path)
            name = dict(packs(self.panel.root))[key]
            self.choice.addItem(name, key)
            self.choice.setCurrentIndex(self.choice.findData(key))
            self.error.setText("팩을 가져왔어요. 동작을 살펴보고 아래 적용 버튼을 눌러 주세요.")
        except (ValueError, OSError, TypeError, KeyError) as exc:
            self.error.setText(str(exc))

    def save(self):
        try:
            preferences = save_preferences(self.panel.root, self.preferences)
            if self.panel.sprite: self.panel.sprite.apply_preferences(preferences)
            self.accept()
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))

    def done(self, result):
        self.preview.timer.stop()
        self.preview.skin.close()
        super().done(result)
