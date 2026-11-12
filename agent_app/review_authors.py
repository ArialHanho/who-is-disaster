"""Read-only line attribution; never infer identity from the current login or HEAD."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import threading
import time
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from . import github_account
from .review_source import allowed_file, git, github_origin, redact


class AuthorLookup:
    def __init__(self, git_cache, cache):
        self.git_cache, self.cache = Path(git_cache), Path(cache)
        self.lock = threading.Lock()
        self.replies = {}

    def key(self, repo, finding):
        data = [repo['source'], finding.get('head'), finding.get('sources'), finding.get('evidence')]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

    def lookup(self, repo, finding):
        key = self.key(repo, finding)
        with self.lock:
            old = self.replies.get(key)
            if old and time.monotonic() - old[0] < 300:
                return old[1]
            saved = self.cache / 'evidence' / (key + '.json')
            try:
                if time.time() - saved.stat().st_mtime < 86400:
                    result = json.loads(saved.read_text(encoding='utf-8'))
                    if isinstance(result, list) and result:
                        self.replies[key] = (time.monotonic(), result)
                        return result
            except (ValueError, OSError): pass
            result = self._lookup(repo, finding)
            self.replies[key] = (time.monotonic(), result)
            if result and all(row.get('sha') and (not row.get('commit_url') or row.get('author')) for row in result):
                try:
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    saved.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
                except OSError: pass
            return result

    def _lookup(self, repo, finding):
        source = repo['source']
        folder = self.git_cache / hashlib.sha256(source.encode()).hexdigest()[:20] if source.startswith('https://') else Path(source)
        web = github_origin(source)
        sources = finding.get('sources') or {'base': {'head': finding.get('head', ''), 'label': repo.get('branch') or '기준 코드', 'kind': 'base'}}
        rows, seen, blobs, blamed = [], {}, {}, {}
        for evidence in finding.get('evidence', []):
            path, quote = evidence['path'], evidence['quote'].strip()
            if not allowed_file(path) or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts or not quote:
                continue
            view = evidence.get('view', 'combined')
            candidates = {view: sources[view]} if view in sources else sources if view == 'combined' else {}
            matched = False
            for name, state in candidates.items():
                base = {'path': path, 'line': evidence['line'], 'view': state.get('label') or name}
                checkout = Path(state.get('folder') or folder)
                if state.get('kind') == 'worktree' and state.get('uncommitted', True):
                    rows.append({**base, 'note': '미커밋 변경 · 수정자 미확인'})
                    matched = True
                    continue
                head = state.get('head', '')
                if not re.fullmatch(r'[0-9a-f]{40,64}', head):
                    continue
                try:
                    blob_key = (str(checkout), head, path)
                    if blob_key not in blobs:
                        size = int(git(checkout, 'cat-file', '-s', head + ':' + path, timeout=10))
                        if size > 1_000_000: continue
                        blobs[blob_key] = redact(git(checkout, 'show', head + ':' + path, timeout=10).decode('utf-8', 'replace')).splitlines()
                    lines = blobs[blob_key]
                    line = evidence['line']
                    if view in sources or not finding.get('sources'):
                        if not 0 < line <= len(lines) or quote not in lines[line-1]: continue
                    else:
                        matches = [i for i, text in enumerate(lines, 1) if quote in text]
                        if len(matches) != 1: continue
                        line = matches[0]
                    blame_key = (str(checkout), head, path, line)
                    if blame_key not in blamed:
                        blamed[blame_key] = self._blame(checkout, head, path, line)
                    author = blamed[blame_key]
                    matched = True
                    identity = (author.get('sha'), path, line, author.get('note'))
                    if identity in seen:
                        if base['view'] not in seen[identity]['views']: seen[identity]['views'].append(base['view'])
                        continue
                    row = {**base, **author, 'line': line, 'views': [base['view']]}
                    seen[identity] = row
                    if author.get('sha'):
                        row.update(self._accounts(web, author['sha']))
                        row['commit_url'] = web + '/commit/' + author['sha'] if web else ''
                    rows.append(row)
                except (ValueError, OSError):
                    continue
            if not matched:
                rows.append({'path': path, 'line': evidence['line'], 'view': view,
                             'note': '원문 이력을 확인하지 못했어요.'})
        return rows

    def _blame(self, folder, head, path, line):
        text = git(folder, 'blame', '--no-textconv', '--line-porcelain', '-L', f'{line},{line}', head, '--', path, timeout=15).decode('utf-8', 'replace')
        parts = text.splitlines()
        sha = parts[0].split()[0].lstrip('^')
        fields = dict(s.split(' ', 1) for s in parts[1:] if not s.startswith('\t') and ' ' in s)
        if 'boundary' in parts and git(folder, 'rev-parse', '--is-shallow-repository').strip() == b'true':
            return {'note': '가져온 이력의 경계라 수정자를 확정하지 못했어요.'}
        if not re.fullmatch(r'[0-9a-f]{40,64}', sha) or set(sha) == {'0'}:
            return {'note': '미커밋 변경 · 수정자 미확인'}
        return {'sha': sha, 'author_name': fields.get('author', '이름 미확인'),
                'committer_name': fields.get('committer', ''), 'author_time': fields.get('author-time', '')}

    def _accounts(self, web, sha):
        if not re.fullmatch(r'https://github.com/[\w.-]+/[\w.-]+', web): return {}
        cache = self.cache / (hashlib.sha256((web + sha).encode()).hexdigest() + '.json')
        try:
            if time.time() - cache.stat().st_mtime < 86400:
                return json.loads(cache.read_text(encoding='utf-8'))
        except (OSError, ValueError): pass
        try:
            query = '{sha, author: (.author | {login,id,html_url}), committer: (.committer | {login,id,html_url})}'
            data = json.loads(github_account._run('api', '--method', 'GET', f'repos/{web.removeprefix("https://github.com/")}/commits/{sha}', '--jq', query, timeout=12).stdout)
            if data.get('sha') != sha: return {}
            people = {}
            for role in ('author', 'committer'):
                person = data.get(role) or {}
                login, uid = person.get('login') or '', person.get('id')
                if not re.fullmatch(r'[A-Za-z0-9-]+(?:\[bot\])?', login): continue
                url = person.get('html_url') or 'https://github.com/' + login
                parsed = urlparse(url)
                if parsed.scheme != 'https' or parsed.netloc != 'github.com': continue
                people[role] = {'login': login, 'url': url,
                                'avatar': self._avatar(uid) if type(uid) is int and uid > 0 else ''}
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(people), encoding='utf-8')
            return people
        except (OSError, ValueError): return {}

    def _avatar(self, uid):
        path = self.cache / 'avatars' / f'{uid}.img'
        try:
            if path.is_file() and time.time() - path.stat().st_mtime < 604800: return str(path)
            request = Request(f'https://avatars.githubusercontent.com/u/{uid}?s=80', headers={'User-Agent': 'Code-Companion'})
            with urlopen(request, timeout=5) as response:
                if not response.headers.get_content_type().startswith('image/'): return ''
                image = response.read(1_000_001)
            if len(image) > 1_000_000: return ''
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(image)
            return str(path)
        except (OSError, ValueError): return ''
