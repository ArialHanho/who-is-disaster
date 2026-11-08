import json
from pathlib import Path
import tempfile
import threading
import unittest
from agent_app.review_runner import ReviewCache, review_batches


def response():
    return {'result':{'findings':[], 'summary':'확인 완료'}, 'usage':{'input_tokens':10}}


class ReviewRunnerTests(unittest.TestCase):
    def test_four_requests_overlap_but_never_exceed_four(self):
        gate = threading.Barrier(4)
        lock = threading.Lock()
        counts = {'active':0, 'peak':0}
        def generate(context):
            with lock:
                counts['active'] += 1
                counts['peak'] = max(counts['peak'], counts['active'])
            gate.wait(timeout=5)
            with lock: counts['active'] -= 1
            return response()
        results = list(review_batches([{'id':n} for n in range(8)], generate, max_workers=8))
        self.assertEqual(counts['peak'], 4)
        self.assertEqual(sorted(row[0] for row in results), list(range(8)))
        self.assertFalse(any(row[3] for row in results))

    def test_failed_scan_reuses_completed_requests_and_still_runs_missing_work(self):
        contexts = [{'commit':'a', 'files':[{'path':f'{n}.py', 'code':str(n)}]} for n in range(3)]
        with tempfile.TemporaryDirectory() as folder:
            cache = ReviewCache(folder, ['version', 'model', 'rules'])
            calls = []
            def failing(context):
                calls.append(context)
                if len(calls) == 2: raise ValueError('fixture timeout')
                return response()
            first = list(review_batches(contexts, failing, cache=cache, max_workers=1))
            self.assertEqual(len(first), 2)
            self.assertEqual(first[-1][3], 'fixture timeout')
            calls.clear()
            def succeeding(context): calls.append(context); return response()
            second = list(review_batches(contexts, succeeding, cache=cache))
            self.assertEqual(len(calls), 2)
            self.assertEqual(sum(row[2] for row in second), 1)
            self.assertEqual(sum(row[1]['usage'].get('input_tokens',0) for row in second), 20)
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 3)

    def test_evidence_rules_refs_and_model_invalidate_cache_but_progress_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            cache = ReviewCache(folder, ['v1', 'model-a'])
            context = {'commit':'a', 'project_rules':'small app', 'files':[{'path':'a.py','code':'x = 1'}], 'coverage':{'done':0}}
            cache.put(context, response())
            self.assertIsNotNone(cache.get({**context, 'coverage':{'done':9}, 'batch':{'index':8}}))
            for changed in ({'commit':'b'}, {'project_rules':'different design'}, {'files':[{'path':'a.py','code':'x = 2'}]}):
                self.assertIsNone(cache.get({**context, **changed}))
            self.assertIsNone(ReviewCache(folder, ['v1','model-b']).get(context))
            self.assertIsNone(ReviewCache(folder, ['v2','model-a']).get(context))
            path = Path(folder)/(cache.key(context)+'.json')
            path.write_text('{broken', encoding='utf-8')
            self.assertIsNone(cache.get(context))
            cache.put(context, response())
            stored = json.loads(path.read_text(encoding='utf-8')); stored['created'] = 0
            path.write_text(json.dumps(stored), encoding='utf-8')
            self.assertIsNone(cache.get(context))
