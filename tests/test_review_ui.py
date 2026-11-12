import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from PySide6.QtCore import QCoreApplication, QEvent, QThreadPool
from PySide6.QtGui import QFont, QFontInfo
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel
from tests.test_review import finding, snapshot


class MultiRepositoryUiTests(unittest.TestCase):
    def test_filter_and_deep_link_include_findings_past_old_limit(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp, patch("agent_app.finding_history_ui.FindingHistory.load", lambda self: None):
            service = ReviewService(Path(temp) / "db")
            rid = service.save_repo("example/app")
            items = [{**finding("overengineering" if i < 10 else "structure"),
                      "key": f"issue-{i}", "title": f"문제 {i}"} for i in range(100)]
            service.record(service.repo(rid), snapshot({"app.py": "value = 1\n"}), "fixture", {"findings": items})
            older = next(f for f in service.findings(rid, limit=None) if f["key"] == "issue-0")
            panel = ReviewPanel(Path(temp), service)
            panel.timer.stop()
            try:
                panel.set_category("overengineering")
                def visible_ids():
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                    return [w.property("findingId") for w in panel.findChildren(QLabel) if w.property("findingId") is not None]
                app.processEvents()
                self.assertIn(older["id"], visible_ids())
                self.assertEqual(len(visible_ids()), 10)
                panel.set_category("all")
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
                next(b for b in panel.findChildren(QPushButton) if b.text() == "다음").click()
                self.assertEqual(panel.finding_page, 1)
                panel.show_finding(older)
                app.processEvents()
                self.assertIn(older["id"], visible_ids())
                self.assertIn(older["id"], panel.expanded)
            finally:
                panel.watcher.close()
                panel.deleteLater()
                app.processEvents()

    def test_all_repositories_queue_continues_after_failure_and_starts_hidden(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            service = ReviewService(Path(temp) / "db")
            ids = [service.save_repo("https://github.com/example/" + name) for name in ("first", "second", "third")]
            calls = []
            def review(rid, progress=None):
                calls.append(rid)
                if rid == ids[1]:
                    service.set_error(rid, "fixture unavailable")
                    raise ValueError("fixture unavailable")
                return {"repo_id": rid, "cached": True}
            service.review = review
            panel = ReviewPanel(Path(temp), service)
            panel.timer.stop()
            self.assertFalse(panel.isVisible())
            self.assertIsNone(panel.repo_id)
            self.assertEqual(QFontInfo(QFont("Jua")).family(), "Jua")
            panel.scan_now()
            deadline = time.monotonic() + 5
            while (panel.busy or panel.queue) and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            QThreadPool.globalInstance().waitForDone()
            app.processEvents()
            self.assertEqual(calls, ids)
            self.assertFalse(panel.busy)
            self.assertFalse(panel.isVisible())
            panel.deleteLater()
            app.processEvents()
