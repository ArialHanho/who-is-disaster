"""Connect a verified resolution to actual Git changes, without another AI call."""
import hashlib
from pathlib import Path
import re
from .review_source import allowed_file, git, github_origin, redact


def resolution_of(finding):
    if finding.get('state') != 'resolved': return None
    if finding.get('resolution'): return finding['resolution']
    # Existing records already retain the first resolved event, before this feature.
    event = next((e for e in reversed(finding.get('events', [])) if e['state'] == 'resolved'), None)
    checked = event or finding.get('verification')
    if not checked: return None
    return {'before': {k: finding.get(k) for k in ('head', 'sources', 'evidence', 'snapshot_id')}, 'after': checked}


def diff_files(text):
    """Keep exact changed lines (not diff headers or unchanged context)."""
    files, current = [], None
    for line in redact(text).splitlines():
        if line.startswith('diff --git '):
            current = {'path': '', 'before': [], 'after': []}; files.append(current)
        elif current is not None:
            if line.startswith('+++ b/'): current['path'] = line[6:]
            elif line.startswith('--- a/') and not current['path']: current['path'] = line[6:]
            elif line.startswith('-') and not line.startswith('---'): current['before'].append(line[1:])
            elif line.startswith('+') and not line.startswith('+++'): current['after'].append(line[1:])
    return [f for f in files if f['path'] and (f['before'] or f['after'])]


def history(repo, finding, authors):
    resolution = resolution_of(finding)
    before = resolution['before'] if resolution else finding
    after = resolution['after'] if resolution else finding.get('verification', {})
    data = {'resolved': bool(resolution), 'reason': after.get('reason', ''), 'writers': authors.lookup(repo, before),
            'fixers': [], 'files': [], 'confirmed': [], 'notes': [], 'checked': after.get('checked', after.get('stamp'))}
    if not resolution:
        data['reason'] = after.get('reason') if after.get('method') not in ('detected', 'unchanged') else ''
        return data
    source, web = repo['source'], github_origin(repo['source'])
    folder = authors.git_cache / hashlib.sha256(source.encode()).hexdigest()[:20] if source.startswith('https://') else Path(source)
    old_views = before.get('sources') or {'base': {'head': before.get('head'), 'label': '기준 코드'}}
    new_views = after.get('sources') or {'base': {'head': after.get('head'), 'label': '기준 코드'}}
    seen_commits, seen_diffs, seen_heads = set(), set(), set()
    old_evidence, new_evidence = before.get('evidence') or [], after.get('evidence') or []
    for key, old in old_views.items():
        relevant = [e for e in old_evidence if e.get('view', 'combined') in (key, 'combined')]
        if not relevant: continue
        new = new_views.get(key)
        if not new:
            data['notes'].append('이전 브랜치와 현재 코드의 연결을 확인하지 못했어요.'); continue
        name = new.get('label') or key
        if new.get('kind') == 'worktree' and new.get('uncommitted', True):
            data['notes'].append(name + ' · 미커밋 상태에서 해결됐어요. 수정 커밋은 아직 없어요.'); continue
        start, end = old.get('head') or '', new.get('head') or ''
        if not all(re.fullmatch(r'[a-f0-9]{40,64}', value) for value in (start, end)): continue
        if start == end: continue
        if end not in seen_heads:
            data['confirmed'].append({'sha': end, 'view': name, 'url': web + '/commit/' + end if web else ''})
            seen_heads.add(end)
        checkout = Path(new.get('folder') or old.get('folder') or folder)
        paths = sorted({e['path'] for e in relevant + new_evidence if allowed_file(e['path']) and '..' not in Path(e['path']).parts})
        try:
            code, _ = git(checkout, 'merge-base', '--is-ancestor', start, end, allowed_codes=(0, 1), with_code=True)
            if code:
                data['notes'].append(name + ' · 이력이 이어지지 않아 해결자를 확정하지 않았어요.'); continue
            args = ('--no-ext-diff', '--no-textconv', '--no-renames', '--unified=0')
            patch = git(checkout, '-c', 'core.quotePath=false', 'diff', *args, start, end, '--', *paths).decode('utf-8', 'replace')
            for change in diff_files(patch):
                removed = [line for line in change['before'] if any(e['path'] == change['path'] and e['quote'].strip() and e['quote'].strip() in line for e in relevant)]
                added = [line for line in change['after'] if any(e['path'] == change['path'] and e['quote'].strip() and e['quote'].strip() in line for e in new_evidence)]
                if not removed and not added: continue
                if removed: change['before'] = removed
                if added: change['after'] = added
                identity = (change['path'], tuple(change['before']), tuple(change['after']))
                if identity not in seen_diffs:
                    data['files'].append({**change, 'view': name}); seen_diffs.add(identity)
            commits = git(checkout, 'rev-list', '--max-count=65', '--reverse', '--topo-order', start + '..' + end, '--', *paths).decode().split()
            if len(commits) > 64:
                data['notes'].append(name + ' · 관련 변경이 많아 수정자 추적을 보류했어요.'); continue
            for sha in commits:
                if sha in seen_commits: continue
                meta = git(checkout, 'show', '-s', '--format=%P%x00%an%x00%cn%x00%at', sha).decode('utf-8', 'replace').strip().split('\0')
                parents = meta[0].split()
                if len(parents) != 1: continue  # Do not mistake a merge recorder for the code author.
                changes = diff_files(git(checkout, '-c', 'core.quotePath=false', 'diff', *args, parents[0], sha, '--', *paths).decode('utf-8', 'replace'))
                hits = []
                for change in changes:
                    removed = any(e['path'] == change['path'] and e['quote'].strip() and any(e['quote'].strip() in line for line in change['before']) for e in relevant)
                    added = any(e['path'] == change['path'] and e['quote'].strip() and any(e['quote'].strip() in line for line in change['after']) for e in new_evidence)
                    if removed or added: hits.append(change['path'])
                if not hits: continue
                seen_commits.add(sha)
                data['fixers'].append({'sha': sha, 'author_name': meta[1], 'committer_name': meta[2], 'author_time': meta[3],
                                       'path': ', '.join(hits), 'view': name,
                                       'commit_url': web + '/commit/' + sha if web else '', **authors._accounts(web, sha)})
        except (ValueError, OSError):
            data['notes'].append(name + ' · 수정 이력을 끝까지 읽지 못했어요.')
    data['notes'] = list(dict.fromkeys(data['notes']))
    if not data['fixers'] and not data['notes']:
        data['notes'].append('코드 해결은 확인했지만, 근거를 바꾼 수정자는 이력으로 확정하지 못했어요.')
    return data
