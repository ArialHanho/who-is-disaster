import json
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QUrl
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication, QPushButton
from agent_app.actions import ActionState
from agent_app.character import DesktopCharacter
from agent_app.character_settings import install_pack, load_preferences, save_preferences, pack_path
from agent_app.character_ui import CharacterDialog
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel, RepoDialog, CONFIG
from agent_app.skin import selected_skin


class CharacterExperienceTests(unittest.TestCase):
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

    def tearDown(self):
        self.sprite.timer.stop()
        self.sprite.click_timer.stop()
        self.sprite.skin.close()
        for w in (self.sprite.restore_tab, self.sprite.bubble, self.sprite, self.panel):
            w.hide()
            w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_hide_has_visible_restore_handle_and_reopens_companion(self):
        self.sprite.show()
        self.sprite.open_panel()
        self.sprite.hide_character()
        self.app.processEvents()
        self.assertFalse(self.sprite.isVisible())
        self.assertFalse(self.panel.isVisible())
        self.assertTrue(self.sprite.restore_tab.isVisible())
        self.sprite.restore_tab.findChild(QPushButton).click()
        self.assertTrue(self.sprite.isVisible())
        self.assertTrue(self.sprite.bubble.isVisible())
        self.assertFalse(self.sprite.restore_tab.isVisible())
        self.sprite.hide_character()
        self.sprite.open_panel()
        self.assertTrue(self.sprite.isVisible())
        self.assertTrue(self.panel.isVisible())
        self.assertFalse(self.sprite.restore_tab.isVisible())

    def test_original_atlas_regions_play_in_order_and_out_of_bounds_is_rejected(self):
        source = self.root/'source'
        source.mkdir()
        image = QImage(64, 64, QImage.Format.Format_ARGB32)
        image.fill(QColor('red'))
        for y in range(32, 64):
            for x in range(32, 64): image.setPixelColor(x, y, QColor('blue'))
        image.save(str(source/'atlas.png'))
        frames = [{'file':'atlas.png','rect':[0,0,32,32]}, {'file':'atlas.png','rect':[32,32,32,32]}]
        manifest = source/'character.json'
        data = {'name':'Atlas','layout':'portrait','animations':{s:{'frames':frames,'fps':10,'durations_ms':[1000,100]} for s in ('idle','thinking','attention')}}
        manifest.write_text(json.dumps(data), encoding='utf-8')
        key = install_pack(self.root, manifest)
        installed = pack_path(self.root,key)
        self.assertEqual(len(list(installed.parent.glob('*.png'))), 1)
        skin = selected_skin(self.root, lambda:None, {**load_preferences(self.root),'character':key})
        self.assertEqual(skin.layout, 'portrait')
        self.assertEqual(skin.frame('idle',0).toImage().pixelColor(0,0), QColor('red'))
        self.assertEqual(skin.frame('idle',9).toImage().pixelColor(0,0), QColor('red'))
        self.assertEqual(skin.frame('idle',10).toImage().pixelColor(0,0), QColor('blue'))
        self.assertEqual(skin.frame('idle',11).toImage().pixelColor(0,0), QColor('red'))
        skin.frame('thinking',0)
        self.assertNotIn('idle',skin.cache)
        skin.close()
        frames[1]['rect'] = [40,40,32,32]
        manifest.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '밖으로'):
            install_pack(self.root,manifest)

    def test_character_preview_cancel_and_apply_preserve_real_work(self):
        token = self.sprite.actions.begin("thinking")
        dialog = CharacterDialog(self.panel)
        self.assertEqual([dialog.choice.itemData(i) for i in range(dialog.choice.count())], ["ember_fox"])
        dialog.name.setText("코코")
        dialog.action("happy")
        self.assertEqual(self.sprite.preferences["character"], "ember_fox")
        frame = self.sprite.skin.frame("idle", 0)
        self.assertFalse(frame.isNull())
        self.assertTrue(frame.hasAlphaChannel())
        states = ["idle", "searching", "thinking", "organizing", "attention", "sleep"]
        frames = [self.sprite.skin.frame(state, 0) for state in states]
        self.assertTrue(all(not image.isNull() and image.hasAlphaChannel() for image in frames))
        self.assertEqual(len({image.cacheKey() for image in frames}), len(states))
        self.assertEqual(self.sprite.actions.state, "thinking")
        dialog.reject()
        self.assertFalse((self.root / ".data/character/settings.json").exists())
        dialog.deleteLater()
        dialog = CharacterDialog(self.panel)
        dialog.scale.setCurrentIndex(dialog.scale.findData(125))
        dialog.name.setText("코코")
        dialog.save()
        self.assertEqual(load_preferences(self.root)["character"], "ember_fox")
        self.assertEqual(self.sprite.config["character_name"], "코코")
        self.assertEqual(self.sprite.width(), round(142*1.25))
        self.assertIn(token, self.sprite.actions.activities)
        dialog.deleteLater()

    def test_continuous_portrait_has_no_image_reads_on_touch_or_resize(self):
        source = self.root/'motion-pack'
        source.mkdir()
        pic = QImage(32,64,QImage.Format.Format_ARGB32)
        pic.fill(QColor('red'))
        pic.save(str(source/'body.png'))
        clip = {'frames':['body.png','body.png'],'fps':8}
        manifest = source/'character.json'
        manifest.write_text(json.dumps({'layout':'portrait','motion':'portrait',
            'animations':{s:clip for s in ('idle','thinking','attention')}}),encoding='utf-8')
        key = install_pack(self.root,manifest)
        self.sprite.apply_preferences(save_preferences(self.root,{'character':key,'scale':65}))
        skin = self.sprite.skin
        original = skin.frame('idle',0).cacheKey()
        anchor = self.sprite.geometry().bottomRight()
        self.assertEqual((self.sprite.width(),self.sprite.height()),(142,225))
        with patch('agent_app.skin.QPixmap') as image_read:
            for state in ('dragging','happy','thinking','attention','idle'):
                self.assertEqual(skin.frame(state,400).cacheKey(),original)
            self.sprite.apply_preferences(save_preferences(self.root,{**self.sprite.preferences,'scale':50}))
            image_read.assert_not_called()
        self.assertIs(self.sprite.skin,skin)
        self.assertEqual((self.sprite.width(),self.sprite.height()),(142,184))
        self.assertEqual(self.sprite.geometry().bottomRight(),anchor)

    def test_long_press_feedback_does_not_replace_running_analysis(self):
        from PySide6.QtCore import Qt, QPoint
        from PySide6.QtTest import QTest
        token = self.sprite.actions.begin('thinking')
        self.sprite.show()
        QTest.mousePress(self.sprite,Qt.MouseButton.LeftButton,pos=QPoint(70,70))
        self.sprite.pet_character()
        self.assertTrue(self.sprite.held)
        self.assertEqual(self.sprite.actions.state,'happy')
        self.assertTrue(self.sprite.feedback.events)
        QTest.mouseRelease(self.sprite,Qt.MouseButton.LeftButton,pos=QPoint(70,70))
        self.assertFalse(self.sprite.hold_timer.isActive())
        self.assertFalse(self.sprite.click_timer.isActive())
        self.assertIn(token,self.sprite.actions.activities)

    def test_pack_import_copies_only_animated_assets_and_rejects_escape_or_still_image(self):
        source = self.root / "pack-source"
        source.mkdir()
        for i, color in enumerate(("red", "blue")):
            image = QImage(16, 16, QImage.Format.Format_ARGB32)
            image.fill(QColor(color))
            image.save(str(source / f"{i}.png"))
        (source / "unrelated.txt").write_text("not a character asset")
        manifest = source / "character.json"
        def write(spec): manifest.write_text(json.dumps({"name": "내 비서", "animations": {key: spec for key in ("idle", "thinking", "attention")}}))
        write({"frames": ["0.png", "1.png"], "fps": 10})
        key = install_pack(self.root, manifest)
        target = pack_path(self.root, key)
        self.assertEqual(len(list(target.parent.iterdir())), 3)
        prefs = save_preferences(self.root, {"character": key})
        skin = selected_skin(self.root, lambda:None, prefs)
        self.assertNotEqual(skin.frame("idle", 0).toImage(), skin.frame("idle", 1).toImage())
        self.assertEqual(skin.frame("greeting", 0).toImage(), skin.frame("idle", 0).toImage())
        skin.close()
        write({"file": "0.png"})
        with self.assertRaisesRegex(ValueError, "애니메이션"): install_pack(self.root, manifest)
        write({"frames": ["../outside.png", "1.png"]})
        with self.assertRaises(ValueError): install_pack(self.root, manifest)
        self.assertEqual(len(list((self.root / ".data/character/packs").iterdir())), 1)

    def test_repository_disconnect_in_settings_is_scoped_and_reconnectable(self):
        ids = [self.service.save_repo(f"https://github.com/example/{name}", auto=True) for name in ("one", "two")]
        with self.service.db() as db:
            for rid in ids:
                db.execute("INSERT INTO runs(repo_id,head,stamp,payload) VALUES(?,?,?,?)", (rid, "a", 1, '{}'))
                db.execute("INSERT INTO catalogs VALUES(?,?,?)", (rid, 1, '{"branches":[],"default":"main"}'))
                db.execute("INSERT INTO findings(repo_id,fingerprint,payload,first_seen,last_seen) VALUES(?,?,?,?,?)", (rid, "test", '{}', 1, 1))
        dialog = RepoDialog(self.panel, self.service.repo(ids[0]))
        dialog.remove_repo()
        self.assertEqual(len(self.service.repos()), 2)
        dialog.cancel()
        self.assertFalse(dialog.remove_pending)
        self.panel.queue = ids.copy()
        with patch("agent_app.review_source.git") as git:
            dialog.remove_repo()
            dialog.remove_repo()
            git.assert_not_called()
        self.assertEqual([r["id"] for r in self.service.repos()], [ids[1]])
        self.assertEqual(self.panel.queue, [ids[1]])
        self.assertEqual(self.service.runs(ids[0]), [])
        self.assertEqual(self.service.findings(ids[0]), [])
        self.assertFalse(self.service.catalog(ids[0])["checked"])
        self.assertEqual(len(self.service.runs(ids[1])), 1)
        self.assertEqual(len(self.service.connect_github([{"source": "https://github.com/example/one"}])), 1)
        dialog.deleteLater()

    def test_folder_entry_prefills_working_tree_and_interaction_resumes_real_job(self):
        with patch("agent_app.review_ui.QFileDialog.getExistingDirectory", return_value=str(self.root)), patch.object(self.panel, "add_manual_repo") as manual:
            self.panel.add_local_repo()
            manual.assert_called_once_with(source=str(self.root), local=True)
        dialog = RepoDialog(self.panel, source=str(self.root), local=True)
        self.assertEqual(dialog.source.toPlainText(), str(self.root))
        self.assertTrue(dialog.working.isChecked())
        dialog.deleteLater()
        stamp = [0]
        actions = ActionState(clock=lambda:stamp[0])
        token = actions.begin("searching")
        actions.pulse("dragging", .3)
        actions.update(token, "thinking")
        self.assertEqual(actions.state, "dragging")
        stamp[0] = 1
        self.assertEqual(actions.state, "thinking")
        actions.finish(token, quiet=True)
        self.assertEqual(actions.state, "idle")


if __name__ == "__main__": unittest.main()
