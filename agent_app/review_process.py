"""Keep CPU-heavy source parsing out of the resident UI's Python interpreter."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

_running = set()
_lock = threading.Lock()


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        process.terminate()


def cancel_reviews():
    with _lock:
        active = list(_running)
    for process in active:
        try: stop_process(process)
        except (OSError, subprocess.SubprocessError): pass


def review(service, repo_id, progress=None, **options):
    from .review_service import ReviewService
    from .review_analysis import analyze
    # Injected providers and fixture services keep their explicit execution contract.
    if (getattr(service.review, '__func__', None) is not ReviewService.review
            or service.analyzer is not analyze):
        return service.review(repo_id, progress=progress, **options)
    jobs = service.root/'jobs'
    jobs.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='review-', dir=jobs) as temporary:
        folder = Path(temporary)
        request = folder/'request.json'
        write_json(request, {'root':str(service.root.resolve()), 'repo_id':repo_id,
                             'options':options, 'parent_pid':os.getpid()})
        command = [sys.executable]
        if not getattr(sys, 'frozen', False):
            command += ['-m', 'agent_app']
        command += ['--review-worker', str(request.resolve())]
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS) if os.name == 'nt' else 0
        process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[1],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, creationflags=flags)
        with _lock: _running.add(process)
        stamp = None
        published = False
        try:
            while True:
                try:
                    changed = (folder/'progress.json').stat().st_mtime_ns
                    if changed != stamp:
                        event = json.loads((folder/'progress.json').read_text(encoding='utf-8'))
                        stamp = changed
                        if progress and event.get('findings_ready') and not published:
                            progress('findings', '브리핑에서 발견한 문제를 볼 수 있어요')
                            published = True
                        if progress and event['stage'] != 'findings':
                            progress(event['stage'], event['text'])
                except (OSError, ValueError): pass
                try:
                    process.wait(timeout=.15)
                    break
                except subprocess.TimeoutExpired: pass
            try:
                response = json.loads((folder/'result.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                raise ValueError('분석 작업이 중단됐어요. 완료한 검토는 다음 분석에서 이어서 사용해요.') from None
            if 'error' in response: raise ValueError(response['error'])
            return response['result']
        finally:
            stop_process(process)
            process.wait(timeout=10)
            with _lock: _running.discard(process)


def watch_parent(parent_pid):
    """An abruptly closed UI must not leave Codex descendants running."""
    if os.name != 'nt': return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, parent_pid)
    if not handle: raise ValueError('비서 창이 종료되어 분석을 시작하지 않았어요.')
    def wait():
        try:
            if kernel.WaitForSingleObject(handle, 0xFFFFFFFF) == 0:
                subprocess.run(['taskkill', '/PID', str(os.getpid()), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
        finally: kernel.CloseHandle(handle)
    threading.Thread(target=wait, name='review-parent', daemon=True).start()


def worker_main(request_path):
    request = Path(request_path)
    try:
        spec = json.loads(request.read_text(encoding='utf-8'))
        watch_parent(spec['parent_pid'])
        from .review_service import ReviewService
        findings_ready = False
        def progress(stage, text):
            nonlocal findings_ready
            findings_ready |= stage == 'findings'
            write_json(request.with_name('progress.json'), {'stage':stage, 'text':text, 'findings_ready':findings_ready})
        result = ReviewService(Path(spec['root'])).review(spec['repo_id'], progress=progress, **spec['options'])
        write_json(request.with_name('result.json'), {'result':result})
        return 0
    except Exception as exc:
        write_json(request.with_name('result.json'), {'error':str(exc)})
        return 1
