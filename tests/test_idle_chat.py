import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import random
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from agent_app.idle_chat import ChatStore,LINES
from agent_app.companion_weather import weather_text,fetch_weather
from agent_app.review_ui import ReviewPanel,CONFIG
from agent_app.review_service import ReviewService
from agent_app.character import DesktopCharacter


class IdleChatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_cooldown_repeat_avoidance_and_quiet_today_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);clock=[time.time()]
            store=ChatStore(root,lambda:clock[0],random.Random(1))
            store.configure(enabled=True)
            self.assertFalse(store.due())
            clock[0]+=121
            self.assertTrue(store.due())
            text=store.choose();store.spoken(text)
            self.assertNotEqual(store.choose(),text)
            self.assertFalse(store.due())
            store.quiet_today()
            reloaded=ChatStore(root,lambda:clock[0])
            self.assertFalse(reloaded.due())
            clock[0]=reloaded.data['muted_until']+1201
            self.assertTrue(reloaded.due())

    def test_weather_never_invents_a_value_or_reuses_stale_data(self):
        now=time.time()
        valid={'stamp':now,'temperature':8.2,'code':61,'label':'시험 지역'}
        text,credit=weather_text(valid,now)
        self.assertIn('8°C',text);self.assertIn('우산',text);self.assertIn('Open-Meteo',credit)
        self.assertIsNone(weather_text(valid,now+3601))
        self.assertIsNone(weather_text(None,now))
        with patch('agent_app.companion_weather.read_json',return_value={'current':{'temperature_2m':None,'weather_code':0}}):
            with self.assertRaises(ValueError):fetch_weather({'latitude':0,'longitude':0})

    def test_chatter_waits_for_work_and_code_notices_preempt_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);service=ReviewService(root/'db')
            panel=ReviewPanel(root,service);panel.timer.stop();panel.idle_chat.timer.stop()
            sprite=DesktopCharacter(CONFIG,root,panel);panel.sprite=sprite;sprite.timer.stop();sprite.show()
            self.app.processEvents()
            chat=panel.idle_chat
            try:
                self.assertTrue(chat.speak(manual=True))
                self.assertEqual(sprite.bubble.mode,'chatter')
                first=sprite.bubble.message.full_text
                self.assertFalse(sprite.bubble.bubble_footer.isVisible())
                menu=sprite.bubble.chatter_menu()
                menu.actions()[0].trigger()
                menu.deleteLater()
                self.assertNotEqual(sprite.bubble.message.full_text,first)
                token=sprite.actions.begin('thinking')
                self.assertFalse(chat.speak(manual=True))
                sprite.actions.finish(token,quiet=True)
                rid=service.save_repo('https://github.com/example/weather-test')
                service.set_error(rid,'connection failed')
                panel.notify(rid)
                self.assertEqual(sprite.bubble.mode,'notice')
                self.assertTrue(sprite.bubble.overview.isVisible())
                self.assertTrue(sprite.bubble.chat_close.isVisible())
                self.assertFalse(chat.speak(manual=True))
                chat.quiet_today()
                self.assertTrue(sprite.bubble.isVisible())
                self.assertEqual(sprite.bubble.mode,'notice')
                sprite.hide()
                self.assertFalse(chat.available(manual=True))
            finally:
                chat.close();panel.watcher.close();sprite.skin.close()
                for w in (sprite.restore_tab,sprite.bubble,sprite,panel):w.hide();w.deleteLater()
                self.app.processEvents()
