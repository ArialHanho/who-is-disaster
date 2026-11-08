import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QFont, QFontInfo
from agent_app.review_service import ReviewService
from agent_app.review_ui import ReviewPanel


class MultiRepositoryUiTests(unittest.TestCase):
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
