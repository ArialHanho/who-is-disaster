import json
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from agent_app.character_settings import install_pack,load_preferences
from agent_app.skin import selected_skin


class PoseAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])

    def test_distinct_poses_blend_return_from_jump_and_reuse_decoded_sources(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for name in ('red','blue','green'):
                pic=QImage(32,64,QImage.Format.Format_ARGB32)
                pic.fill(QColor(name))
                pic.save(str(root/(name+'.png')))
            mapping={'idle':'red','thinking':'blue','attention':'green','happy':'green','sleep':'blue'}
            data={'layout':'portrait','motion':'poses','animations':{
                s:{'frames':[n+'.png',n+'.png'],'display_scale':.8 if s=='sleep' else 1} for s,n in mapping.items()}}
            manifest=root/'character.json'
            manifest.write_text(json.dumps(data),encoding='utf-8')
            key=install_pack(root,manifest)
            prefs={**load_preferences(root),'character':key}
            skin=selected_skin(root,lambda:None,prefs)
            self.assertTrue(skin.pose_motion)
            self.assertNotEqual(skin.frame('idle',0).cacheKey(),skin.frame('thinking',0).cacheKey())
            self.assertEqual(skin.frame('attention',0).cacheKey(),skin.frame('happy',0).cacheKey())
            with patch('agent_app.skin.QPixmap') as reads:
                skin.layers('idle',0)
                start=skin.layers('thinking',1)
                middle=skin.layers('thinking',2.2)
                end=skin.layers('thinking',3.5)
                self.assertEqual([round(x[1],2) for x in start],[1,0])
                self.assertEqual([round(x[1],2) for x in middle],[.5,.5])
                self.assertEqual(len(end),1)
                skin.layers('happy',10)
                skin.layers('happy',24)
                landed=skin.layers('happy',27)
                self.assertEqual(landed[0][0].cacheKey(),skin.frame('idle',0).cacheKey())
                reads.assert_not_called()
            skin.close()
            reduced=selected_skin(root,lambda:None,{**prefs,'reduced_motion':True})
            reduced.layers('idle',0)
            self.assertEqual(len(reduced.layers('thinking',1)),1)
            reduced.close()
