"""An animated, draggable desktop character rendered with native Qt painting."""
import json
import math
import time
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QMenu, QWidget, QVBoxLayout
from .actions import ActionState
from .skin import selected_skin
from .character_settings import SCALES, load_preferences, save_preferences
from .character_render import draw_character
from .portrait_motion import PortraitMotion, PortraitPose, PhotoGestureMotion, character_geometry
from .character_effects import TouchFeedback
from .character_dialogue import CharacterDialogue
from .sprite_raster import visible_subject_y


def popup_position(area, character, size, *, centered=False, gap=12):
    """Keep the readable popup and character separate, including near screen edges."""
    width, height = size.width(), size.height()
    left, top = area.left()+8, area.top()+8
    right, bottom = max(left, area.right()-width-7), max(top, area.bottom()-height-7)
    anchor_x = character.center().x()-width//2 if centered else character.center().x()-width+70
    proposals = [(anchor_x, character.top()-height-gap),
                 (character.left()-width-gap, character.bottom()-height),
                 (character.right()+gap, character.bottom()-height),
                 (anchor_x, character.bottom()+gap)]
    candidates = [QRect(max(left, min(x, right)), max(top, min(y, bottom)), width, height) for x, y in proposals]
    exclusion = character.adjusted(-6, -6, 6, 6)
    best = min(candidates, key=lambda r: r.intersected(exclusion).width()*r.intersected(exclusion).height())
    return best.topLeft()


def snapped_position(area, rect, threshold=24, margin=10):
    """Magnetize a nearby edge without pulling a character across the screen."""
    left,top=area.left()+margin,area.top()+margin
    right=max(left,area.right()-rect.width()+1-margin)
    bottom=max(top,area.bottom()-rect.height()+1-margin)
    x,y=rect.x(),rect.y()
    if abs(x-left)<=threshold:x=left
    elif abs(x-right)<=threshold:x=right
    if abs(y-top)<=threshold:y=top
    elif abs(y-bottom)<=threshold:y=bottom
    return QPoint(max(area.left(),min(x,area.right()-rect.width()+1)),
                  max(area.top(),min(y,area.bottom()-rect.height()+1)))


class DesktopCharacter(QWidget):
    def __init__(self, config, root, panel):
        super().__init__()
        self.config, self.root, self.panel = dict(config), root, panel
        self.preferences = load_preferences(root) if config["kind"] == "code" else {"character": "legacy", "reduced_motion": False, "scale": 100, "name": config["character_name"]}
        self.config["character_name"] = self.preferences["name"]
        self.frame, self.dragged = 0, False
        self.look, self.target_look, self.hover, self.hovering, self.pressed = (0, 0), (0, 0), 0, False, False
        self.actions = ActionState()
        self.dialogue = CharacterDialogue()
        self.last_reaction_at, self.fast_dragged = 0, False
        self.skin = selected_skin(root, self.update, self.preferences)
        self.canvas_height, width, height = character_geometry(self.skin.layout, self.preferences["scale"], self.skin.pose_motion)
        self.canvas_width = width if self.skin.pose_motion else 142
        self.pose_demo = None
        self.motion, self.pose, self.drag_lean = PortraitMotion(), PortraitPose(), 0
        self.gesture_motion, self.gesture_pose = PhotoGestureMotion(), PortraitPose()
        self.preserve_gesture_art = False
        self.feedback, self.held, self.wheel_delta = TouchFeedback(), False, 0
        self.last_tick = time.monotonic()
        self.last_action, self.action_frame = "idle", 0
        self.setWindowTitle(config["name"] + " · 캐릭터")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(width, height)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("클릭: 말풍선 · 두 번 클릭: 토닥이기 · 우클릭: 메뉴 · 드래그: 이동")
        self.bubble = panel.create_bubble(self)
        for surface in (self.panel, self.bubble):
            if hasattr(surface, "tag"): surface.tag.setText(self.config["character_name"])
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.timeout.connect(self.animate)
        self.timer.start(16 if self.skin.continuous_motion and not self.preferences["reduced_motion"] else 40)
        self.click_timer = QTimer(self)
        self.click_timer.setSingleShot(True)
        self.click_timer.timeout.connect(self.click_cycle)
        self.snap_animation=QPropertyAnimation(self,b'pos',self)
        self.snap_animation.setDuration(160)
        self.snap_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.snap_animation.finished.connect(self.save_position)
        self.hold_timer = QTimer(self)
        self.hold_timer.setInterval(650)
        self.hold_timer.timeout.connect(self.pet_character)
        self.long_hold_timer = QTimer(self)
        self.long_hold_timer.setSingleShot(True)
        self.long_hold_timer.timeout.connect(self.say_long_hold)
        self.fast_drag_timer = QTimer(self)
        self.fast_drag_timer.setSingleShot(True)
        self.fast_drag_timer.timeout.connect(self.say_fast_drag)
        self.restore_tab = RestoreTab(self) if config["kind"] == "code" else None
        area = QApplication.primaryScreen().availableGeometry()
        offset = {"research": 0, "team": 1, "automation": 2}.get(config["kind"], 0)
        self.move(area.right() - 167 - offset * 150, area.bottom() - self.height() - 20)
        try:
            saved = json.loads((root / ".data/widget.json").read_text())
            x, y = saved["x"], saved["y"]
            if any(s.availableGeometry().contains(x + 60, y + 60) for s in QApplication.screens()):
                self.move(x, y)
        except (OSError, ValueError, KeyError):
            pass
        self.clamp_position()

    def animate(self):
        now = time.monotonic()
        dt, self.last_tick = min(.05, max(0, now-self.last_tick)), now
        if not self.isVisible(): return
        self.frame += dt * 10
        ease = 1 - math.exp(-dt / .14)
        self.look = tuple(a+(b-a)*ease for a,b in zip(self.look, self.target_look))
        self.hover += ((1 if self.hovering else 0) - self.hover)*ease
        self.drag_lean *= math.exp(-dt / .18)
        state = self.display_state()
        if state != self.last_action:
            self.last_action, self.action_frame = state, self.frame
        if self.preserve_gesture_art:
            self.gesture_pose = self.gesture_motion.step(dt, held=self.pressed, moving=self.dragged, drag=self.drag_lean, reduced=self.preferences['reduced_motion'])
            if not self.pressed and not self.gesture_motion.active and state not in {'dragging','greeting','happy'}:
                self.preserve_gesture_art = False
                self.motion, self.pose = PortraitMotion(), PortraitPose()
        elif self.skin.continuous_motion:
            expressive = self.skin.pose_motion or self.preferences["character"] == "ember_fox"
            self.pose = self.motion.step(dt, state, pressed=self.pressed, look=self.look[0], drag=self.drag_lean, expressive=expressive)
        if self.bubble.isVisible(): self.place_popup(self.bubble)
        self.update()

    def character_art(self, state):
        # Use the same existing action art as the settings preview while dragging.
        art_state = ('dragging' if self.dragged else 'idle') if self.preserve_gesture_art else state
        custom = self.skin.frame(art_state, self.frame - self.action_frame)
        layers = self.skin.layers(art_state, self.frame)
        pose = (self.gesture_pose if self.preserve_gesture_art else self.pose) if self.skin.continuous_motion else None
        return custom, layers, pose

    def paintEvent(self, event):
        p = QPainter(self)
        p.scale(self.width() / self.canvas_width, self.height() / self.canvas_height)
        p.translate((self.canvas_width-142)/2, 0)
        state = self.display_state()
        custom, layers, pose = self.character_art(state)
        draw_character(p, self.config, self.frame, state,
                       style=self.preferences["character"], custom=custom,
                       look=self.look, hover=self.hover, pressed=self.pressed,
                       motion=not self.preferences["reduced_motion"], height=self.canvas_height,
                       pose=pose,
                       layers=layers,stage_width=self.canvas_width,
                       title_override="동작 미리보기" if self.pose_demo else None,
                       show_nameplate=not self.bubble.isVisible())
        if not self.preferences["reduced_motion"]: self.feedback.paint(p)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.snap_animation.stop()
            self.click_timer.stop()
            self.pose_demo = None
            self.press_position = event.globalPosition().toPoint()
            self.window_start = self.pos()
            self.last_drag_x = self.press_position.x()
            self.speed_position = self.press_position
            self.speed_started = time.monotonic()
            self.speed_distance, self.fast_dragged = 0, False
            self.fast_drag_timer.stop()
            self.long_hold_timer.start(4000)
            self.dragged = False
            self.pressed = True
            if self.skin.continuous_motion:
                self.preserve_gesture_art = True
                self.gesture_motion.trigger('lift')
                self.skin.reset_pose_to_idle()
            self.held = False
            self.hold_timer.start()
            self.feedback.add(event.position().x()*self.canvas_width/self.width()-(self.canvas_width-142)/2, event.position().y()*self.canvas_height/self.height())
            self.actions.touch()

    def mouseMoveEvent(self, event):
        self.target_look = (max(-3, min(3, (event.position().x()/self.width()-.5)*7)), max(-2, min(2, (event.position().y()/self.height()-.5)*5)))
        if self.pressed and event.buttons() & Qt.MouseButton.LeftButton and hasattr(self, "press_position"):
            position = event.globalPosition().toPoint()
            now = time.monotonic()
            step = position - self.speed_position
            self.speed_position = position
            self.speed_distance += math.hypot(step.x(), step.y())
            elapsed = now - self.speed_started
            fast = False
            if elapsed >= .08 or self.speed_distance >= 96:
                fast = self.speed_distance >= 70 and self.speed_distance / max(.025, elapsed) >= 1100
                self.speed_started, self.speed_distance = now, 0
            delta = position - self.press_position
            if delta.manhattanLength() > 5:
                starting=not self.dragged
                self.drag_lean = max(-2, min(2, (event.globalPosition().x()-self.last_drag_x)*.16))
                self.last_drag_x = event.globalPosition().x()
                self.dragged = True
                self.click_timer.stop()
                self.hold_timer.stop()
                if starting:
                    if self.skin.continuous_motion:
                        self.preserve_gesture_art = True
                        self.gesture_motion.trigger('lift')
                        self.skin.reset_pose_to_idle()
                    if not (self.config['kind']=='code' and self.bubble.mode in {'notice','progress'}):
                        self.bubble.hide()
                    self.panel.hide()
                self.actions.pulse("dragging", .3)
                self.move(self.window_start + delta)
                if fast and not self.fast_dragged:
                    self.fast_dragged = True
                    self.react_motion('drag_fast')
                    if not starting and self.config['kind']=='code':
                        self.fast_drag_timer.start(max(1, math.ceil((1.2-(now-self.last_reaction_at))*1000)))
                if starting and self.config['kind']=='code':
                    self.say_reaction('drag_fast' if self.fast_dragged else 'drag', auto_hide_ms=0)
                elif self.bubble.isVisible():self.place_popup(self.bubble)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.pressed = False
            self.hold_timer.stop()
            self.long_hold_timer.stop()
            self.fast_drag_timer.stop()
            if self.dragged:
                if self.preserve_gesture_art: self.gesture_motion.trigger('land')
                screen=QApplication.screenAt(event.globalPosition().toPoint()) or self.screen()
                self.snap_to_edge(screen.availableGeometry())
                self.actions.pulse("greeting", 1)
                if self.config['kind']=='code':
                    self.say_reaction('drop_fast' if self.fast_dragged else 'drop', auto_hide_ms=2500)
            elif self.held or getattr(self, "double_clicked", False):
                if self.preserve_gesture_art: self.gesture_motion.trigger('rest')
                if self.held and self.config['kind']=='code' and self.bubble.mode=='interaction':
                    self.bubble.remaining_ms=2500
                    self.bubble.arm_expiry()
                self.held = self.double_clicked = False
            else:
                self.click_timer.start(QApplication.doubleClickInterval())

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.click_timer.stop()
            self.hold_timer.stop()
            self.long_hold_timer.stop()
            self.fast_drag_timer.stop()
            self.double_clicked = True
            self.motion.age = 0
            self.skin.restart_pose()
            self.actions.pulse("happy", 2)
            self.feedback.add(71, 48, 'heart')
            self.say_touch_reaction()

    def say_touch_reaction(self):
        if self.skin.pose_motion:
            self.preserve_gesture_art = False
            self.gesture_motion, self.gesture_pose = PhotoGestureMotion(), PortraitPose()
            self.motion.age = 0
            self.skin.restart_pose()
        self.say_reaction('pet')

    def react_motion(self, action):
        if action in {'hold','drag_hold'} and self.skin.continuous_motion and not self.preserve_gesture_art:
            self.preserve_gesture_art = True
            self.skin.reset_pose_to_idle()
        if self.preserve_gesture_art:
            reaction = {'drag_fast':'startle', 'hold':'wriggle', 'drag_hold':'wriggle', 'pet':'pet'}.get(action)
            if reaction: self.gesture_motion.trigger(reaction)

    def say_reaction(self, action, *, auto_hide_ms=5000, react=True):
        # Important notices may suppress chatter, but must not suppress movement.
        if react: self.react_motion(action)
        important=self.config['kind']=='code' and self.bubble.isVisible() and self.bubble.mode in {'notice','progress'}
        if self.isVisible() and hasattr(self.bubble, "show_message") and not important and not self.panel.isVisible() and not QApplication.activeModalWidget():
            text = self.dialogue.choose(action)
            self.last_reaction_at = time.monotonic()
            if self.config["kind"] == "code": self.bubble.show_message(text, mode="interaction", auto_hide_ms=auto_hide_ms)
            else: self.bubble.show_message(text)
            self.place_popup(self.bubble)
            self.bubble.show()

    def say_fast_drag(self):
        if not (self.pressed and self.dragged): return
        remaining = 1.2 - (time.monotonic()-self.last_reaction_at)
        if remaining > 0:
            self.fast_drag_timer.start(math.ceil(remaining*1000))
            return
        self.say_reaction('drag_fast', auto_hide_ms=0, react=False)

    def say_long_hold(self):
        if not self.pressed or self.config['kind']!='code': return
        remaining = 1.2 - (time.monotonic()-self.last_reaction_at)
        if remaining > 0:
            self.long_hold_timer.start(math.ceil(remaining*1000))
            return
        self.say_reaction('drag_hold' if self.dragged else 'hold', auto_hide_ms=0)

    def pet_character(self):
        if not self.pressed or self.dragged:
            self.hold_timer.stop()
            return
        first = not self.held
        self.held = True
        self.click_timer.stop()
        self.actions.pulse("happy", 1.5)
        self.feedback.add(71, 48, 'heart')
        if first: self.say_touch_reaction()

    def display_state(self):
        if self.pressed and self.dragged: return 'dragging'
        if self.pose_demo and time.monotonic() < self.pose_demo[1]: return self.pose_demo[0]
        self.pose_demo = None
        return self.actions.state

    def play_pose(self, state):
        self.preserve_gesture_art = False
        self.gesture_motion, self.gesture_pose = PhotoGestureMotion(), PortraitPose()
        self.pose_demo = (state, time.monotonic()+5)
        self.motion.age = 0
        self.skin.restart_pose()
        self.show()
        self.update()

    def wheelEvent(self, event):
        if self.config["kind"] != "code": return super().wheelEvent(event)
        self.wheel_delta += event.angleDelta().y()
        steps = int(self.wheel_delta / 120)
        if steps:
            self.wheel_delta -= steps*120
            index = max(0, min(len(SCALES)-1, SCALES.index(self.preferences["scale"])+steps))
            if SCALES[index] != self.preferences["scale"]:
                self.apply_preferences(save_preferences(self.root, {**self.preferences, "scale":SCALES[index]}))
                self.feedback.events.clear()
        event.accept()

    def enterEvent(self, event):
        self.hovering = True
        self.actions.touch()

    def leaveEvent(self, event):
        self.hovering, self.target_look = False, (0, 0)

    def clamp_position(self):
        area = self.screen().availableGeometry()
        self.move(max(area.left(), min(self.x(), area.right()-self.width()+1)), max(area.top(), min(self.y(), area.bottom()-self.height()+1)))

    def save_position(self):
        path=self.root/'.data/widget.json'
        path.parent.mkdir(exist_ok=True)
        pending=path.with_suffix('.tmp')
        pending.write_text(json.dumps({'x':self.x(),'y':self.y()}),encoding='utf-8')
        pending.replace(path)

    def snap_to_edge(self,area):
        self.snap_animation.stop()
        target=snapped_position(area,self.geometry())
        if self.preferences['reduced_motion'] or target==self.pos():
            self.move(target)
            self.save_position()
            return
        self.snap_animation.setStartValue(self.pos())
        self.snap_animation.setEndValue(target)
        self.snap_animation.start()

    def apply_preferences(self, preferences):
        self.snap_animation.stop()
        anchor = self.geometry().bottomRight()
        replace = any(preferences[key] != self.preferences[key] for key in ("character", "reduced_motion"))
        if replace: self.skin.close()
        self.preferences = preferences
        self.config["character_name"] = preferences["name"]
        for surface in (self.panel, self.bubble):
            if hasattr(surface, "tag"): surface.tag.setText(preferences["name"])
        if replace: self.skin = selected_skin(self.root, self.update, preferences)
        self.canvas_height, width, height = character_geometry(self.skin.layout, preferences["scale"], self.skin.pose_motion)
        self.canvas_width = width if self.skin.pose_motion else 142
        self.setFixedSize(width, height)
        self.move(anchor.x()-width+1, anchor.y()-height+1)
        if replace:
            self.motion, self.pose = PortraitMotion(), PortraitPose()
            self.gesture_motion, self.gesture_pose = PhotoGestureMotion(), PortraitPose()
            self.preserve_gesture_art = False
        self.timer.setInterval(16 if self.skin.continuous_motion and not preferences["reduced_motion"] else 40)
        self.clamp_position()
        if replace: self.actions.pulse("greeting", 2)
        self.update()

    def dragEnterEvent(self, event):
        if hasattr(self.panel, "folder_drop") and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if hasattr(self.panel, "folder_drop"):
            self.panel.folder_drop(event.mimeData().urls())
            event.acceptProposedAction()

    def place_popup(self, popup):
        area = self.screen().availableGeometry()
        if self.config["kind"] == "code":
            if popup is self.bubble:
                # Match the art actually on screen, not the tallest possible pose.
                top=0
                if self.skin.continuous_motion and self.skin.layout == "portrait":
                    custom,layers,pose=self.character_art(self.display_state())
                    top=visible_subject_y(custom,layers,self.canvas_height,self.canvas_width)
                    if pose is not None and not self.preferences['reduced_motion']:
                        bottom=self.canvas_height-12
                        top=bottom+(top-bottom)*pose.stretch+pose.lift
                elif self.skin.continuous_motion:
                    # Keep the speech anchored while the compact sprite hops.
                    top = 36
                subject=QRect(self.geometry())
                subject.setTop(self.y()+max(0,round(top*self.height()/self.canvas_height)))
                position=popup_position(area,subject,popup.size(),centered=True,gap=8)
                above=position.y()+popup.height()<=subject.top()-6
                if popup.pos()!=position: popup.move(position)
                if hasattr(popup,'set_speech_tail'): popup.set_speech_tail(self.geometry().center().x()-position.x() if above else None)
                return
            if popup is self.panel:
                from .review_theme import size_popup
                size_popup(popup, available=area)
            position = popup_position(area, self.geometry(), popup.size())
            if popup is self.panel and QRect(position, popup.size()).intersects(self.geometry()):
                room = max(self.x()-area.left()-20, area.right()-self.geometry().right()-20)
                if room >= 420:
                    size_popup(popup, width=min(480, room), available=area)
                    position = popup_position(area, self.geometry(), popup.size())
            popup.move(position)
            return
        x = self.x() - popup.width() + 85
        x = max(area.left()+8, min(x, area.right()-popup.width()-8))
        y = max(area.top()+8, min(self.y()-popup.height()+70, area.bottom()-popup.height()-8))
        popup.move(x, y)

    def click_cycle(self):
        if QApplication.activeModalWidget():return
        if self.bubble.isVisible():
            self.bubble.hide()
        else:
            self.panel.hide()
            self.actions.pulse("greeting", 1)
            self.bubble.refresh_text()
            self.place_popup(self.bubble)
            self.bubble.show()
            self.bubble.raise_()
            if self.config["kind"] == "code":
                from .review_theme import reveal
                reveal(self.bubble, self.preferences["reduced_motion"])

    def open_panel(self):
        self.pose_demo = None
        if self.restore_tab: self.restore_tab.hide()
        self.show()
        self.actions.touch()
        self.bubble.hide()
        self.place_popup(self.panel)
        self.panel.show()
        self.panel.raise_()
        self.panel.activateWindow()
        if self.config["kind"] == "code":
            from .review_theme import reveal
            reveal(self.panel, self.preferences["reduced_motion"])

    def hide_character(self):
        self.hide()
        if self.restore_tab:
            self.restore_tab.place()
            self.restore_tab.show()
            self.restore_tab.raise_()

    def restore(self):
        if self.restore_tab: self.restore_tab.hide()
        self.show()
        self.raise_()
        self.actions.pulse("greeting", 2)
        modal = QApplication.activeModalWidget()
        if modal:
            modal.raise_()
            modal.activateWindow()
        elif self.panel.isVisible():
            self.panel.raise_()
            self.panel.activateWindow()
        elif self.bubble.isVisible():
            self.bubble.raise_()
            self.bubble.activateWindow()
        else: self.click_cycle()

    def contextMenuEvent(self, event):
        self.click_timer.stop()
        self.hold_timer.stop()
        self.long_hold_timer.stop()
        self.fast_drag_timer.stop()
        menu = QMenu(self)
        menu.addAction("비서 열기", self.open_panel)
        if self.skin.pose_motion:
            poses = menu.addMenu("동작 미리보기")
            for key, title in [("idle","서 있기"),("happy","만세 점프"),("greeting","손 흔들기"),("organizing","노트북 작업"),("thinking","생각하기"),("attention","깜짝 놀라기"),("sleep","잠자기")]:
                poses.addAction(title,lambda checked=False,k=key:self.play_pose(k))
        if hasattr(self.panel, "add_local_repo"):
            menu.addAction("로컬 폴더 연결", self.panel.add_local_repo)
            menu.addAction("저장소 주소 입력", self.panel.add_manual_repo)
            menu.addAction("캐릭터 바꾸기", self.panel.customize_character)
            if hasattr(self.panel,'other_connections'):
                menu.addAction('기타 연결',self.panel.other_connections)
                menu.addAction('한마디 듣기',lambda:self.panel.idle_chat.speak(manual=True))
                menu.addAction('1시간 조용히',self.panel.idle_chat.quiet_hour)
                menu.addAction('오늘은 조용히',self.panel.idle_chat.quiet_today)
        menu.addAction("비서 숨기기 · 열기 탭 유지", self.hide_character)
        menu.addSeparator()
        menu.addAction("종료", QApplication.instance().quit)
        menu.exec(event.globalPos())

    def hideEvent(self, event):
        self.pressed = False
        self.preserve_gesture_art = False
        self.gesture_motion, self.gesture_pose = PhotoGestureMotion(), PortraitPose()
        self.hold_timer.stop()
        self.long_hold_timer.stop()
        self.fast_drag_timer.stop()
        self.bubble.hide()
        self.panel.hide()


class RestoreTab(QWidget):
    def __init__(self, sprite):
        super().__init__()
        from .review_theme import STYLE, button
        self.sprite = sprite
        self.setWindowTitle("Who is disaster · 다시 열기")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)
        self.setFixedSize(112, 42)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.addWidget(button("비서 열기", sprite.restore, True))

    def place(self):
        area = self.sprite.screen().availableGeometry()
        x = area.right()-self.width()-6 if self.sprite.x() > area.center().x() else area.left()+6
        self.move(x, max(area.top()+8, min(self.sprite.y(), area.bottom()-self.height()-8)))
