"""Same revision identity for a cheap probe and the captured Git snapshot."""
import hashlib
import json


def working_revisions(views):
    return sorted((v['id'], v['head'], v['dirty_signature']) for v in views if v['kind'] == 'worktree')


def excluded_branches(repo):
    return set(json.loads(repo.get('excluded_branches', '[]')))


def included_branches(repo, catalog):
    base = repo['branch'] or catalog['default']
    excluded = excluded_branches(repo)
    selected = set(json.loads(repo['targets']))
    return [b for b in catalog['branches'] if b['name'] == base or
            b['name'] not in excluded and (repo['all_branches'] or b['name'] in selected)]


def worktree_exclusions(repo, catalog):
    selected = {branch['name'] for branch in included_branches(repo, catalog)}
    unselected = {branch['name'] for branch in catalog['branches']} - selected
    return unselected | (excluded_branches(repo) - {repo['branch'] or catalog['default']})


def included_pulls(repo, pulls, default=""):
    base = repo['branch'] or default
    excluded = excluded_branches(repo)
    selected = set(json.loads(repo['targets']))
    return [p for p in pulls if not p.get('same_repository') or p.get('head_branch') == base or
            p.get('head_branch') not in excluded and (repo['all_branches'] or p.get('head_branch') in selected)]


def revision_token(repo, catalog, pulls, detached_head='', *, working=()):
    base = repo['branch'] or catalog['default']
    refs = sorted((b['name'], b['head']) for b in included_branches(repo, catalog))
    options = [repo[k] for k in ('source', 'branch', 'all_branches', 'open_prs', 'targets', 'include_working', 'rules')]
    raw = [options, base, detached_head if not base else '', refs, sorted(pulls)]
    if excluded := excluded_branches(repo):
        raw.append(sorted(excluded))
    if repo['include_working']:
        raw.append(working)
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def captured_token(repo, catalog, snapshot, captured, pulls):
    if not catalog:
        return None
    base = repo['branch'] or catalog['default']
    heads = {v['id']: v['head'] for v in captured['views']} if captured else {}
    refs = [{'name': b['name'], 'head': snapshot['head'] if b['name'] == base
             else heads.get('branch:'+b['name'], b.get('head', ''))} for b in catalog['branches']]
    current_catalog = {**catalog, 'branches': refs}
    if any(not b['head'] for b in included_branches(repo, current_catalog)):
        return None
    prs = ([(p['number'], p['head'], p['base']) for p in pulls] if repo['open_prs'] else
           [(int(key[3:]), head, '') for key, head in heads.items() if key.startswith('pr:')])
    working = working_revisions(captured['views']) if repo['include_working'] and captured else ()
    return revision_token(repo, current_catalog, prs, snapshot['head'], working=working)
