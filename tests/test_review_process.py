from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_app import review_process
from agent_app.review_service import ReviewService


class IsolatedReviewTests(unittest.TestCase):
    def test_headless_worker_reports_errors_without_opening_a_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ReviewService(Path(folder)/'db')
            with self.assertRaises(ValueError):
                review_process.review(service, 999)
            self.assertFalse(review_process._running)
            self.assertEqual(list((service.root/'jobs').iterdir()), [])

    def test_cancel_stops_owned_worker_without_waiting_for_model_timeout(self):
        process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],
                                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
        with review_process._lock: review_process._running.add(process)
        try:
            review_process.cancel_reviews()
            process.wait(timeout=5)
            self.assertIsNotNone(process.returncode)
        finally:
            if process.poll() is None: process.kill()
            process.wait(timeout=5)
            with review_process._lock: review_process._running.discard(process)

    def test_analysis_work_runs_in_a_separate_process(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ReviewService(Path(folder)/'db')
            launch = subprocess.Popen
            seen = []
            def worker(command, **kwargs):
                request = Path(command[-1])
                script = ('import json,os,time; from pathlib import Path; '
                          'time.sleep(.3); '
                          'Path(__import__("sys").argv[1]).with_name("result.json").write_text(json.dumps({"result":{"pid":os.getpid()}}))')
                child = launch([sys.executable, '-c', script, str(request)], **kwargs)
                seen.append(child.pid)
                return child
            with patch.object(review_process.subprocess, 'Popen', side_effect=worker):
                result = review_process.review(service, 1)
            import os
            self.assertNotEqual(result['pid'], os.getpid())
            self.assertEqual(len(seen), 1)
            self.assertNotEqual(seen[0], os.getpid())
            self.assertFalse(review_process._running)
