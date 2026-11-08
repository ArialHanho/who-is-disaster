import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QLabel
from agent_app.character import DesktopCharacter
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel, CONFIG
from agent_app.typing_label import graphemes


class PopupExperienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = ReviewService(self.root / "db")
        self.panel = ReviewPanel(self.root, self.service)
        self.panel.timer.stop()
        self.sprite = DesktopCharacter(CONFIG, self.root, self.panel)
        self.panel.sprite = self.sprite
        self.bubble = self.sprite.bubble

    def tearDown(self):
        self.sprite.timer.stop()
        self.sprite.click_timer.stop()
        self.sprite.skin.close()
        for w in (self.sprite.restore_tab, self.bubble, self.sprite, self.panel):
            w.hide()
            w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_typing_keeps_unicode_and_layout_and_new_message_replaces_old(self):
        self.assertEqual(graphemes("한👩‍💻é"), ["한", "👩‍💻", "é"])
        self.bubble.show_message("한👩‍💻é <안내>\n함께 확인해요.")
        message = self.bubble.message
        self.assertEqual(message.visible_text, "")
        self.assertFalse(message.timer.isActive())
        before = message.heightForWidth(348)
        message.advance()
        self.assertEqual(message.visible_text, "한")
        self.assertEqual(message.heightForWidth(348), before)
        self.bubble.show()
        self.app.processEvents()
        self.bubble.show_message("새 소식이에요.")
        message.advance()
        self.assertEqual(message.visible_text, "새")
        self.bubble.skip.click()
        self.assertEqual(message.visible_text, "새 소식이에요.")
        self.assertFalse(message.timer.isActive())
        self.bubble.show_message("다시 말하는 중이에요.")
        self.bubble.hide()
        self.assertFalse(message.timer.isActive())
        self.sprite.preferences["reduced_motion"] = True
        self.bubble.show_message("바로 읽을 수 있어요.")
        self.assertEqual(message.visible_text, message.full_text)

    def test_notice_waits_for_typing_and_pauses_while_hovered(self):
        self.bubble.show_message("읽고 있는 알림이에요.", mode="notice", auto_hide_ms=12000)
        self.bubble.show()
        self.app.processEvents()
        self.bubble.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertFalse(self.bubble.expiry.isActive())
        self.bubble.message.finish()
        self.assertTrue(self.bubble.expiry.isActive())
        self.bubble.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
        self.assertFalse(self.bubble.expiry.isActive())
        self.assertGreater(self.bubble.remaining_ms, 10000)
        self.bubble.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertTrue(self.bubble.expiry.isActive())
        self.bubble.hide()
        self.assertFalse(self.bubble.expiry.isActive())

    def test_short_followup_sentence_stays_on_its_own_line(self):
        self.bubble.show_message('바로 답이 안 나와도 괜찮아요. 잠깐 두고 봐요.',mode='chatter')
        self.assertEqual(self.bubble.message.full_text,'바로 답이 안 나와도 괜찮아요.\n잠깐 두고 봐요.')

    def test_background_progress_preserves_notice_and_details_open_correct_repo(self):
        ids = [self.service.save_repo(f"https://github.com/example/repo-{i}") for i in range(2)]
        self.panel.open_tab("settings")
        self.panel.repo_id = ids[1]
        self.bubble.show_message("API 호출 인자가 빠졌어요.", mode="notice", repo_id=ids[0])
        self.bubble.show()
        self.app.processEvents()
        token = self.sprite.actions.begin("thinking")
        self.panel.progress(token, "example/repo-1", "organizing", "정리 중이에요")
        self.assertEqual(self.bubble.message.full_text, "API 호출 인자가 빠졌어요.")
        self.bubble.open_details()
        self.assertEqual(self.panel.page, "brief")
        self.assertEqual(self.panel.repo_id, ids[0])
        self.assertTrue(self.panel.isVisible())
        self.panel.latest_bubble_message = "오래된 경고"
        self.panel.busy = False
        self.bubble.refresh_text()
        self.assertNotIn("오래된 경고", self.bubble.message.full_text)

    def test_settings_draft_survives_background_refresh_and_tabs_start_at_top(self):
        for i in range(9): self.service.save_repo(f"https://github.com/example/repo-{i}")
        self.panel.open_tab("repos")
        self.panel.show()
        self.app.processEvents()
        bar = self.panel.scroll.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0)
        bar.setValue(bar.maximum())
        self.panel.refresh()  # Queue an old-page scroll restoration.
        self.panel.open_tab("settings")
        self.app.processEvents()
        self.assertEqual(bar.value(), 0)
        editor = self.panel.findChild(QLineEdit, "modelChoice")
        editor.setText("unsaved-model-choice")
        bar.setValue(80)
        self.panel.busy = True
        self.panel.refresh()
        self.assertIs(editor, self.panel.findChild(QLineEdit, "modelChoice"))
        self.assertEqual(editor.text(), "unsaved-model-choice")
        self.assertEqual(bar.value(), 80)
        self.panel.open_tab("repos")
        self.app.processEvents()
        self.panel.open_tab("settings")
        self.app.processEvents()
        self.assertEqual(self.panel.findChild(QLineEdit, "modelChoice").text(), "unsaved-model-choice")

    def test_failed_or_unreviewed_repositories_are_not_presented_as_healthy(self):
        rid = self.service.save_repo("https://github.com/example/app")
        self.panel.refresh()
        def visible_copy(): return " ".join(w.text() for w in self.panel.findChildren(QLabel) if not w.isHidden())
        self.assertIn("아직 분석하지 않은 저장소", visible_copy())
        self.service.set_error(rid, "접근 권한 확인")
        self.panel.refresh()
        self.assertIn("분석을 마치지 못한 저장소", visible_copy())
        self.assertNotIn("알려드릴 문제가 없어요", visible_copy())


if __name__ == "__main__": unittest.main()
