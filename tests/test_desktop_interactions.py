import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QRect,QPoint,QPointF,QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication
from agent_app.character import DesktopCharacter,snapped_position
from agent_app.character_dialogue import CharacterDialogue, LINES
from agent_app.idle_chat import ChatStore
from agent_app.review_ui import ReviewPanel,CONFIG
from agent_app.review_service import ReviewService


class DesktopInteractionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.panel=ReviewPanel(self.root,ReviewService(self.root/'db'));self.panel.timer.stop()
        self.panel.idle_chat.timer.stop();self.panel.idle_chat.focus_timer.stop()
        self.sprite=DesktopCharacter(CONFIG,self.root,self.panel);self.panel.sprite=self.sprite
        self.sprite.timer.stop();self.sprite.show();self.app.processEvents()

    def tearDown(self):
        self.sprite.snap_animation.stop();self.sprite.click_timer.stop();self.sprite.hold_timer.stop()
        self.sprite.long_hold_timer.stop();self.sprite.fast_drag_timer.stop()
        self.panel.idle_chat.close();self.panel.watcher.close();self.sprite.skin.close()
        for w in (self.sprite.restore_tab,self.sprite.bubble,self.sprite,self.panel):w.hide();w.deleteLater()
        self.app.processEvents();self.temp.cleanup()

    def mouse(self,kind,point,button=Qt.MouseButton.LeftButton,buttons=Qt.MouseButton.LeftButton):
        return QMouseEvent(kind,QPointF(point),QPointF(self.sprite.mapToGlobal(point)),button,buttons,Qt.KeyboardModifier.NoModifier)

    def test_single_click_only_toggles_bubble_and_double_click_is_compact(self):
        self.sprite.click_cycle()
        self.assertTrue(self.sprite.bubble.isVisible())
        self.assertTrue(self.sprite.bubble.compact())
        self.assertFalse(self.sprite.bubble.bubble_footer.isVisible())
        self.sprite.click_cycle()
        self.assertFalse(self.sprite.bubble.isVisible());self.assertFalse(self.panel.isVisible())
        self.sprite.bubble.show_message('코드 경고',mode='notice');self.sprite.bubble.show()
        with patch.object(self.sprite.bubble,'open_details') as details:
            self.sprite.click_cycle();details.assert_not_called()
        self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
        self.assertEqual(self.sprite.click_timer.interval(),QApplication.doubleClickInterval())
        self.sprite.mouseDoubleClickEvent(self.mouse(QEvent.Type.MouseButtonDblClick,QPoint(60,90)))
        self.assertFalse(self.sprite.click_timer.isActive())
        self.assertEqual(self.sprite.bubble.mode,'interaction');self.assertTrue(self.sprite.bubble.compact())

    def test_drag_says_one_line_and_snap_saves_the_final_position(self):
        self.sprite.move(120,100);self.sprite.preferences['reduced_motion']=True
        self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
        with patch.object(self.sprite.bubble,'show_message',wraps=self.sprite.bubble.show_message) as say:
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(35,90),Qt.MouseButton.NoButton))
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(35,90),Qt.MouseButton.NoButton))
            self.assertEqual(say.call_count,1)
            self.assertIn(self.sprite.bubble.message.full_text,LINES['drag'])
        self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
        self.assertFalse(self.sprite.click_timer.isActive())
        self.assertEqual(self.sprite.bubble.remaining_ms,2500)
        self.assertIn(self.sprite.bubble.message.full_text,LINES['drop'])
        self.assertFalse(self.sprite.long_hold_timer.isActive())
        area=QRect(-1920,0,1920,1040)
        target=snapped_position(area,QRect(-1914,300,168,320))
        self.assertEqual(target,QPoint(-1910,300))
        self.assertEqual(snapped_position(area,QRect(-900,300,168,320)),QPoint(-900,300))
        self.sprite.move(-1914,300);self.sprite.snap_to_edge(area)
        saved=json.loads((self.root/'.data/widget.json').read_text())
        self.assertEqual(saved,{'x':-1910,'y':300})

    def test_fast_drag_waits_for_reading_and_release_cancels_pending_reactions(self):
        self.sprite.preferences['reduced_motion']=True
        with patch('agent_app.character.time.monotonic',return_value=100) as clock:
            self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
            clock.return_value=100.2
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(80,90),Qt.MouseButton.NoButton))
            initial=self.sprite.bubble.message.full_text
            self.assertIn(initial,LINES['drag'])
            clock.return_value=100.24
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(60,210),Qt.MouseButton.NoButton))
            self.assertTrue(self.sprite.fast_drag_timer.isActive())
            self.assertEqual(self.sprite.bubble.message.full_text,initial)
            clock.return_value=101.5
            self.sprite.fast_drag_timer.stop();self.sprite.say_fast_drag()
            self.assertIn(self.sprite.bubble.message.full_text,LINES['drag_fast'])
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(190,90),Qt.MouseButton.NoButton))
            self.assertFalse(self.sprite.fast_drag_timer.isActive())
            self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
            landed=self.sprite.bubble.message.full_text
            self.assertIn(landed,LINES['drop_fast'])
            clock.return_value=110
            self.sprite.say_fast_drag();self.sprite.say_long_hold()
            self.assertEqual(self.sprite.bubble.message.full_text,landed)
            # A quick second gesture must cancel its queued reaction at release too.
            self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
            clock.return_value=110.2
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(80,90),Qt.MouseButton.NoButton))
            clock.return_value=110.24
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(60,210),Qt.MouseButton.NoButton))
            self.assertTrue(self.sprite.fast_drag_timer.isActive())
            self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
            self.assertFalse(self.sprite.fast_drag_timer.isActive())
            self.assertFalse(self.sprite.long_hold_timer.isActive())

    def test_long_hold_distinguishes_pet_from_drag_and_preserves_notices(self):
        self.sprite.preferences['reduced_motion']=True
        with patch('agent_app.character.time.monotonic',return_value=500) as clock:
            self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
            self.assertEqual(self.sprite.long_hold_timer.interval(),4000)
            clock.return_value=500.65;self.sprite.pet_character()
            clock.return_value=504
            self.sprite.long_hold_timer.stop();self.sprite.say_long_hold()
            self.assertIn(self.sprite.bubble.message.full_text,LINES['hold'])
            self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
            self.assertFalse(self.sprite.click_timer.isActive())
            self.assertEqual(self.sprite.bubble.remaining_ms,2500)
            clock.return_value=600
            self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
            clock.return_value=600.5
            self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(90,90),Qt.MouseButton.NoButton))
            self.assertTrue(self.sprite.long_hold_timer.isActive())
            clock.return_value=604
            self.sprite.long_hold_timer.stop();self.sprite.say_long_hold()
            self.assertIn(self.sprite.bubble.message.full_text,LINES['drag_hold'])
            self.sprite.bubble.show_message('코드 경고',mode='notice')
            clock.return_value=606
            self.sprite.say_long_hold()
            self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
            self.assertEqual(self.sprite.bubble.message.full_text,'코드 경고')

    def test_reactions_avoid_recent_lines_and_holding_speaks_only_once(self):
        dialogue=CharacterDialogue(random.Random(42))
        for action in LINES:
            history=[]
            for _ in range(40):
                line=dialogue.choose(action)
                self.assertNotIn(line,history[-4:])
                history.append(line)
        self.sprite.pressed=True
        with patch.object(self.sprite.bubble,'show_message',wraps=self.sprite.bubble.show_message) as say:
            self.sprite.pet_character();self.sprite.pet_character()
            self.assertEqual(say.call_count,1)
        self.sprite.bubble.show_message('분석 중이에요',mode='progress');self.sprite.bubble.show()
        self.sprite.say_touch_reaction()
        self.assertEqual(self.sprite.bubble.message.full_text,'분석 중이에요')

    def test_drag_reactions_keep_selected_move_art_unwarped_and_pet_still_jumps(self):
        from PySide6.QtGui import QImage,QColor
        from agent_app.character_settings import install_pack,save_preferences
        mapping={'idle':'red','dragging':'blue','happy':'green','greeting':'blue','thinking':'blue','attention':'green'}
        for color in set(mapping.values()):
            image=QImage(64,128,QImage.Format.Format_ARGB32);image.fill(QColor(color))
            image.save(str(self.root/(color+'.png')))
        manifest=self.root/'character.json'
        manifest.write_text(json.dumps({'layout':'portrait','motion':'poses','animations':{
            state:{'frames':[color+'.png',color+'.png']} for state,color in mapping.items()}}),encoding='utf-8')
        key=install_pack(self.root,manifest)
        self.sprite.apply_preferences(save_preferences(self.root,{**self.sprite.preferences,'character':key}))
        self.sprite.timer.stop()
        original=self.sprite.skin.frame('idle',0).cacheKey()
        self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
        self.assertTrue(self.sprite.preserve_gesture_art)
        self.sprite.dragged=True
        moving=self.sprite.skin.frame('dragging',0).cacheKey()
        self.sprite.character_art('dragging');self.sprite.frame+=4
        from agent_app.character_render import draw_character
        for action,kind in (('drag_fast','startle'),('drag_hold','wriggle')):
            self.sprite.say_reaction(action)
            self.assertEqual(self.sprite.gesture_motion.kind,kind)
            self.sprite.actions.pulse('dragging',1)
            self.sprite.gesture_pose=self.sprite.gesture_motion.step(.05,held=True)
            with patch('agent_app.character.draw_character',wraps=draw_character) as draw:
                self.sprite.grab()
                options=draw.call_args.kwargs
                self.assertEqual(options['custom'].cacheKey(),moving)
                self.assertEqual(len(options['layers']),1)
                self.assertEqual(options['layers'][0][0].cacheKey(),moving)
                self.assertEqual((options['pose'].angle,options['pose'].stretch),(0,1))
        self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
        self.assertEqual(self.sprite.gesture_motion.kind,'land')
        self.assertTrue(self.sprite.preserve_gesture_art)
        self.sprite.mouseDoubleClickEvent(self.mouse(QEvent.Type.MouseButtonDblClick,QPoint(60,90)))
        self.assertFalse(self.sprite.preserve_gesture_art)
        self.assertEqual(self.sprite.actions.state,'happy')
        custom,_,_=self.sprite.character_art('happy')
        self.assertEqual(custom.cacheKey(),self.sprite.skin.frame('happy',0).cacheKey())
        self.assertNotEqual(custom.cacheKey(),original)

    def test_drag_uses_preview_art_even_while_reviewing_and_notices_do_not_block_motion(self):
        from PySide6.QtGui import QImage,QColor
        from agent_app.character_settings import install_pack,save_preferences
        pack=self.root/'drag-pack';pack.mkdir()
        for name,color in [('idle','blue'),('dragging','orange')]:
            image=QImage(64,128,QImage.Format.Format_ARGB32);image.fill(QColor(color));image.save(str(pack/(name+'.png')))
        animations={state:{'frames':[file+'.png',file+'.png'],'fps':2} for state,file in [('idle','idle'),('thinking','idle'),('attention','idle'),('dragging','dragging')]}
        (pack/'character.json').write_text(json.dumps({'name':'fixture','layout':'portrait','motion':'poses','animations':animations}),encoding='utf-8')
        key=install_pack(self.root,pack/'character.json')
        self.sprite.apply_preferences(save_preferences(self.root,{**self.sprite.preferences,'character':key}))
        self.sprite.timer.stop()
        self.sprite.actions.begin('thinking')
        self.sprite.bubble.show_message('분석 중',mode='progress');self.sprite.bubble.show()
        self.sprite.mousePressEvent(self.mouse(QEvent.Type.MouseButtonPress,QPoint(60,90)))
        self.sprite.mouseMoveEvent(self.mouse(QEvent.Type.MouseMove,QPoint(160,90)))
        self.sprite.actions.transient=None
        self.assertEqual(self.sprite.display_state(),'dragging')
        expected=self.sprite.skin.frame('dragging',0).cacheKey()
        self.sprite.character_art('dragging');self.sprite.frame+=4
        custom,layers,pose=self.sprite.character_art('dragging')
        self.assertEqual(custom.cacheKey(),expected)
        self.assertEqual(layers[-1][0].cacheKey(),expected)
        self.assertNotEqual(expected,self.sprite.skin.frame('idle',0).cacheKey())
        self.sprite.say_reaction('drag_fast')
        self.assertEqual(self.sprite.gesture_motion.kind,'startle')
        self.assertEqual(self.sprite.bubble.message.full_text,'분석 중')
        self.sprite.say_reaction('drag_hold')
        self.assertEqual(self.sprite.gesture_motion.kind,'wriggle')
        self.sprite.mouseReleaseEvent(self.mouse(QEvent.Type.MouseButtonRelease,QPoint(60,90),buttons=Qt.MouseButton.NoButton))
        self.assertEqual(self.sprite.gesture_motion.kind,'land')

    def test_hour_snooze_is_persistent_and_preserves_code_notice(self):
        chat=self.panel.idle_chat
        chat.store.configure(enabled=True)
        self.sprite.bubble.show_message('작업 경고',mode='notice');self.sprite.bubble.show()
        now=time.time();chat.quiet_hour()
        reloaded=ChatStore(self.root)
        self.assertTrue(3599<=reloaded.data['muted_until']-now<=3601)
        self.assertFalse(reloaded.due());self.assertTrue(self.sprite.bubble.isVisible())
        self.assertEqual(self.sprite.bubble.mode,'notice')

    def test_fullscreen_hides_only_automatic_chatter_and_waits_after_exit(self):
        chat=self.panel.idle_chat
        self.assertTrue(chat.speak(manual=True))
        with patch('agent_app.idle_chat.fullscreen_active',return_value=True):
            chat.check_focus()
            self.assertFalse(self.sprite.bubble.isVisible());self.assertFalse(chat.available())
            self.sprite.bubble.show_message('코드 경고',mode='notice');self.sprite.bubble.show()
            chat.check_focus();self.assertTrue(self.sprite.bubble.isVisible())
        self.sprite.bubble.hide()
        with patch('agent_app.idle_chat.fullscreen_active',return_value=False):
            chat.check_focus()
            self.assertGreater(chat.focus_resume_at,time.monotonic()+40)
            self.assertFalse(chat.available())
            chat.focus_resume_at=time.monotonic()-1
            self.assertTrue(chat.available())
