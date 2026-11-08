import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from PySide6.QtWidgets import QApplication,QLabel,QPushButton
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel
from tests.test_review import snapshot,finding


class ProductQaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.service=ReviewService(self.root/'data')
        self.rid=self.service.save_repo('https://github.com/example/app')
        self.repo=self.service.repo(self.rid);self.data=snapshot({'app.py':'value = 1\n'})
        self.service.record(self.repo,self.data,'a',{'findings':[finding(confidence='medium')],'head':self.data['head'],'summary':'처음 분석'})
        self.fid=self.service.findings(self.rid)[0]['id']

    def tearDown(self):self.temp.cleanup()

    def record_state(self,state,letter):
        data={**self.data,'head':letter*40}
        check={'state':state,'reason':'현재 코드 확인','head':data['head'],'method':'codex','evidence':[]}
        self.service.record(self.service.repo(self.rid),data,letter,{'findings':[],'head':data['head'],'followup':{self.fid:check}})
        return self.service.findings(self.rid,True)[0]

    def test_first_fix_survives_incomplete_recheck_but_new_recurrence_gets_new_fix(self):
        first=self.record_state('resolved','b')
        self.record_state('uncertain','c')
        checked=self.record_state('resolved','d')
        self.assertEqual(checked['resolution'],first['resolution'])
        self.assertEqual(len(checked['resolutions']),1)
        self.record_state('open','e')
        new=self.record_state('resolved','f')
        self.assertEqual(new['resolution']['after']['head'],'f'*40)
        self.assertEqual(len(new['resolutions']),2)

    def test_resolved_tags_excluded_actions_and_history_restore(self):
        panel=ReviewPanel(self.root,self.service);panel.timer.stop();panel.watcher.close();panel.idle_chat.close()
        try:
            self.record_state('resolved','b')
            panel.lifecycle='resolved';panel.refresh();panel.show();self.app.processEvents()
            texts=[w.text() for w in panel.findChildren(QLabel) if w.isVisible()]
            self.assertFalse(any(' · 검토 후보' in t for t in texts))
            self.assertFalse(any(w.isVisible() and '전체 수정 지시문 복사' in w.text() for w in panel.findChildren(QPushButton)))
            self.record_state('open','c');self.service.disposition(self.fid,'dismissed')
            panel.expanded={self.fid};panel.open_tab('history');self.app.processEvents()
            buttons=[w for w in panel.findChildren(QPushButton) if w.isVisible()]
            self.assertFalse(any(w.text()=='AI 수정 지시문 복사' for w in buttons))
            log=next(w for w in buttons if w.text().startswith('최근 분석 기록'))
            log.click();self.assertEqual(log.text(),'분석 기록 접기')
            next(w for w in buttons if w.text()=='다시 검토 대상으로').click()
            self.assertEqual(self.service.findings(self.rid)[0]['disposition'],'open')
        finally:
            panel.hide();panel.deleteLater();self.app.processEvents()
