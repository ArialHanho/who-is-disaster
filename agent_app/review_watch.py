"""Git metadata events locally; cheap remote revision probes before any code review."""
import hashlib
import json
from pathlib import Path
import time
from PySide6.QtCore import QObject, QFileSystemWatcher, QThreadPool, QTimer, Signal
from .background import Worker
from .review_catalog import branches
from .review_source import git
from .review_parallel import github_origin
from . import github_account


def revision(repo):
    catalog = branches(repo['source'])
    base = repo['branch'] or catalog['default']
    selected = set(json.loads(repo['targets'])) | {base}
    refs = sorted((b['name'], b['head']) for b in catalog['branches'] if repo['all_branches'] or b['name'] in selected)
    pulls = []
    if repo['open_prs']:
        pulls = sorted((p['number'], p['head'], p['base']) for p in github_account.open_pulls(repo['source']))
    else:
        explicit = sorted(t for t in selected if t.startswith('pr:'))
        if explicit:
            web = github_origin(repo['source'])
            if not web: raise ValueError('PR 변경 감지에는 GitHub origin이 필요해요.')
            pulls = git('.', 'ls-remote', '--', web+'.git', *['refs/pull/'+t[3:]+'/head' for t in explicit]).decode().splitlines()
    local_head = '' if repo['source'].startswith('https://') else git(repo['source'], 'rev-parse', 'HEAD').decode().strip()
    options = [repo[k] for k in ('source', 'branch', 'all_branches', 'open_prs', 'targets', 'include_working', 'rules')]
    return hashlib.sha256(json.dumps([options, base, local_head, refs, pulls], sort_keys=True).encode()).hexdigest()


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
        try: self.baselines = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.baselines = {}
        self.repos, self.configs, self.pending, self.next_probe, self.errors = {}, {}, {}, {}, {}
        self.dirty, self.workers = set(), {}
        self.fs = QFileSystemWatcher(self)
        self.fs.fileChanged.connect(self.local_event)
        self.fs.directoryChanged.connect(self.local_event)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(8000)
        self.debounce.timeout.connect(self.poll)
        self.rebuild = False

    def local_event(self, *_):
        if self.closed: return
        self.dirty.update(rid for rid,r in self.repos.items() if not r['source'].startswith('https://'))
        self.rebuild = True
        self.debounce.start()

    def sync(self):
        self.repos = {r['id']:r for r in self.service.repos() if r['auto']}
        self.pending = {rid:token for rid,token in self.pending.items() if rid in self.repos}
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
            if rid in self.workers or rid in self.pending: continue
            remote = repo['source'].startswith('https://') or repo['open_prs'] or 'pr:' in repo['targets']
            if rid not in self.dirty and (not remote and rid not in self.errors or now < self.next_probe.get(rid, 0)): continue
            self.dirty.discard(rid)
            self.next_probe[rid] = now+60
            worker = Worker(lambda r=repo:revision(r))
            self.workers[rid] = worker
            worker.signals.result.connect(lambda token, i=rid:self.detected(i, token))
            worker.signals.error.connect(lambda error, i=rid:self.problem(i, error))
            worker.signals.finished.connect(lambda i=rid:self.workers.pop(i, None))
            QThreadPool.globalInstance().start(worker)

    def detected(self, rid, token):
        if self.closed or rid not in self.repos: return
        self.errors.pop(rid, None)
        if self.baselines.get(str(rid)) != token:
            self.pending[rid] = token
            self.changed.emit(rid)

    def problem(self, rid, message):
        if self.errors.get(rid) != message: self.failed.emit(rid, message)
        self.errors[rid] = message

    def completed(self, rid, success):
        token = self.pending.pop(rid, None)
        if token is None: return
        if success:
            self.baselines[str(rid)] = token
            temp = self.path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.baselines), encoding='utf-8')
            temp.replace(self.path)
        else:
            self.errors[rid] = '분석을 마치지 못해 변경 확인을 다시 시도해요.'

    def close(self):
        self.closed = True
        self.debounce.stop()
        paths = self.fs.files()+self.fs.directories()
        if paths: self.fs.removePaths(paths)
