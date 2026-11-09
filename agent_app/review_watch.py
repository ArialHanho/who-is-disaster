"""Git metadata events locally; cheap remote revision probes before any code review."""
import json
from pathlib import Path
import time
from PySide6.QtCore import QObject, QFileSystemWatcher, QThreadPool, QTimer, Signal
from .background import Worker
from .review_catalog import branches
from .review_source import git
from .review_parallel import github_origin
from . import github_account
from .review_schedule import ReviewSchedule
from .review_revision import revision_token


def revision(repo):
    catalog = branches(repo['source'])
    base = repo['branch'] or catalog['default']
    selected = set(json.loads(repo['targets'])) | {base}
    pulls = []
    if repo['open_prs']:
        pulls = sorted((p['number'], p['head'], p['base']) for p in github_account.open_pulls(repo['source']))
    else:
        explicit = sorted(t for t in selected if t.startswith('pr:'))
        if explicit:
            web = github_origin(repo['source'])
            if not web: raise ValueError('PR 변경 감지에는 GitHub origin이 필요해요.')
            output = git('.', 'ls-remote', '--', web+'.git', *['refs/pull/'+t[3:]+'/head' for t in explicit]).decode().splitlines()
            pulls = [(int(ref.split('/')[2]), head, '') for head, ref in (row.split() for row in output)]
    local_head = '' if repo['source'].startswith('https://') else git(repo['source'], 'rev-parse', 'HEAD').decode().strip()
    return revision_token(repo, catalog, pulls, local_head)


def git_watch_paths(source):
    output = git(source, 'rev-parse', '--path-format=absolute', '--git-dir', '--git-common-dir').decode().splitlines()
    paths = set()
    for entry in output:
        root = Path(entry)
        paths.add(str(root))
        for name in ('HEAD', 'packed-refs', 'refs', 'logs', 'worktrees'):
            path = root/name
            if path.exists(): paths.add(str(path))
            if path.is_dir():
                paths.update(str(p) for p in path.rglob('*') if p.is_dir() or p.name == 'HEAD')
    return sorted(paths)


class ChangeWatcher(QObject):
    changed = Signal(int)
    failed = Signal(int, str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self.closed = False
        self.path = service.root/'watch-state.json'
        try: state = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError): state = {}
        if state.get('version') != 2: state = {'baselines': state}
        self.schedule = ReviewSchedule(state)
        self.baselines, self.pending = self.schedule.baselines, self.schedule.pending
        self.repos, self.configs, self.next_probe, self.errors = {}, {}, {}, {}
        self.dirty, self.workers = set(), {}
        self.fs = QFileSystemWatcher(self)
        self.fs.fileChanged.connect(self.local_event)
        self.fs.directoryChanged.connect(self.local_event)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(8000)
        self.debounce.timeout.connect(self.poll)
        self.dispatch = QTimer(self)
        self.dispatch.setInterval(5000)
        self.dispatch.timeout.connect(self.flush)
        self.dispatch.start()
        self.rebuild = False

    def local_event(self, *_):
        if self.closed: return
        self.dirty.update(rid for rid,r in self.repos.items() if not r['source'].startswith('https://'))
        self.rebuild = True
        self.debounce.start()

    def sync(self):
        self.repos = {r['id']:r for r in self.service.repos() if r['auto']}
        self.schedule.retain(self.repos)
        for rid, repo in self.repos.items():
            # Existing installations also honour the last completed review.
            if repo.get('checked'): self.schedule.started.setdefault(str(rid), repo['checked'])
        # Review timestamps change frequently; only watch configuration belongs here.
        config = {rid:json.dumps([r[k] for k in ('source','branch','targets','all_branches','open_prs','include_working','rules')]) for rid,r in self.repos.items()}
        if config != self.configs:
            self.dirty.update(rid for rid in config if self.configs.get(rid)!=config[rid])
            self.configs, self.rebuild = config, True
        if self.rebuild:
            paths = set()
            for rid,r in self.repos.items():
                if not r['source'].startswith('https://'):
                    try: paths.update(git_watch_paths(r['source']))
                    except (ValueError, OSError) as exc: self.problem(rid, str(exc))
            old = set(self.fs.files()+self.fs.directories())
            if old-paths: self.fs.removePaths(sorted(old-paths))
            if paths-old: self.fs.addPaths(sorted(paths-old))
            self.rebuild = False

    def poll(self):
        if self.closed: return
        self.sync()
        now = time.monotonic()
        for rid,repo in self.repos.items():
            if rid in self.workers: continue
            remote = repo['source'].startswith('https://') or repo['open_prs'] or 'pr:' in repo['targets']
            if rid not in self.dirty and (not remote and rid not in self.errors or now < self.next_probe.get(rid, 0)): continue
            self.dirty.discard(rid)
            self.next_probe[rid] = now+60
            worker = Worker(lambda r=repo:revision(r))
            self.workers[rid] = worker
            worker.signals.result.connect(lambda token, i=rid:self.detected(i, token))
            worker.signals.error.connect(lambda error, i=rid:self.problem(i, error))
            worker.signals.finished.connect(lambda i=rid:self.probed(i))
            QThreadPool.globalInstance().start(worker)
        self.flush()

    def detected(self, rid, token):
        if self.closed or rid not in self.repos: return
        self.errors.pop(rid, None)
        self.schedule.observe(rid, token)
        self.flush()

    def ready(self, rid):
        return (rid in self.repos and rid not in self.workers
                and rid not in self.errors and self.schedule.ready(rid))

    def probed(self, rid):
        self.workers.pop(rid, None)
        self.flush()

    def flush(self):
        if self.closed: return
        for rid in list(self.pending):
            if self.ready(rid): self.changed.emit(rid)

    def save(self):
        try:
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.schedule.state()), encoding='utf-8')
            temp.replace(self.path)
        except OSError:
            pass  # A checkpoint failure must not discard the review result.

    def started(self, rid):
        self.schedule.begin(rid)
        self.save()

    def problem(self, rid, message):
        if self.errors.get(rid) != message: self.failed.emit(rid, message)
        self.errors[rid] = message

    def completed(self, rid, success, token=None, *, reviewed=True):
        self.schedule.complete(rid, success, token, reviewed=reviewed)
        self.save()
        if not success:
            self.errors[rid] = '분석을 마치지 못해 변경 확인을 다시 시도해요.'

    def close(self):
        self.closed = True
        self.debounce.stop()
        self.dispatch.stop()
        paths = self.fs.files()+self.fs.directories()
        if paths: self.fs.removePaths(paths)
