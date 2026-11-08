"""Bounded concurrent review and local checkpoints for identical requests."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import deque
import hashlib
import json
from pathlib import Path
import tempfile
import time


class ReviewCache:
    """Cache exact input only: changed code, refs, rules or model invalidate it."""
    def __init__(self, folder, policy, *, ttl=7*24*3600, fields=None):
        self.folder = Path(folder)
        self.policy, self.ttl = policy, ttl
        self.fields = fields or {'summary':str, 'findings':list}

    def valid(self, result):
        return isinstance(result, dict) and all(isinstance(result.get(k), t) for k, t in self.fields.items())

    def key(self, context):
        # UI counters are not model evidence and change while a scan is running.
        evidence = {k:v for k,v in context.items() if k not in {'batch', 'coverage'}}
        raw = json.dumps([self.policy, evidence], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, context):
        key = self.key(context)
        try:
            stored = json.loads((self.folder / (key+'.json')).read_text(encoding='utf-8'))
            result = stored['result']
            if (stored['key'] == key and 0 <= time.time()-stored['created'] < self.ttl
                    and self.valid(result)):
                return {'result':result, 'usage':{}, 'elapsed_ms':0}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        return None

    def put(self, context, response):
        result = response.get('result')
        if not self.valid(result):
            return
        key, temporary = self.key(context), None
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.folder, suffix='.tmp', delete=False) as output:
                temporary = Path(output.name)
                json.dump({'key':key, 'created':time.time(), 'result':result}, output, ensure_ascii=False)
            temporary.replace(self.folder / (key+'.json'))
        except OSError:
            # Optional checkpoint storage must not discard a successful review.
            pass
        finally:
            if temporary is not None:
                try: temporary.unlink(missing_ok=True)
                except OSError: pass


def review_batches(contexts, generate, *, cache=None, max_workers=4):
    """Continue past one timeout and retry it once after pending work.

    Authentication, quota and other fatal errors still stop new requests.
    Successful in-flight requests are always retained.
    """
    def request(index):
        context = contexts[index]
        cached = cache.get(context) if cache else None
        if cached is not None:
            return cached, True
        response = generate(context)
        if cache:
            cache.put(context, response)
        return response, False

    workers = max(1, min(4, max_workers))
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='code-review') as pool:
        pending, queued, failed = {}, deque((i, 0) for i in range(len(contexts))), False
        while pending or (queued and not failed):
            while not failed and queued and len(pending) < workers:
                index, attempt = queued.popleft()
                pending[pool.submit(request, index)] = (index, attempt)
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in sorted(done, key=lambda f:pending[f]):
                index, attempt = pending.pop(future)
                try:
                    response, reused = future.result()
                except (ValueError, OSError) as exc:
                    if getattr(exc, 'retryable', False):
                        if not attempt and not failed:
                            queued.append((index, 1))
                            continue
                    else:
                        failed = True
                    yield index, None, False, str(exc)
                else:
                    yield index, response, reused, ''
