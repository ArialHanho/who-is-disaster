"""Same revision identity for a cheap probe and the captured Git snapshot."""
import hashlib
import json


def revision_token(repo, catalog, pulls, detached_head=''):
    base = repo['branch'] or catalog['default']
    selected = set(json.loads(repo['targets'])) | {base}
    refs = sorted((b['name'], b['head']) for b in catalog['branches']
                  if repo['all_branches'] or b['name'] in selected)
    options = [repo[k] for k in ('source', 'branch', 'all_branches', 'open_prs', 'targets', 'include_working', 'rules')]
    raw = [options, base, detached_head if not base else '', refs, sorted(pulls)]
    return hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()


def captured_token(repo, catalog, snapshot, captured, pulls):
    if not catalog:
        return None
    base = repo['branch'] or catalog['default']
    heads = {v['id']: v['head'] for v in captured['views']} if captured else {}
    refs = [{'name': b['name'], 'head': snapshot['head'] if b['name'] == base
             else heads.get('branch:'+b['name'], b.get('head', ''))} for b in catalog['branches']]
    selected = set(json.loads(repo['targets'])) | {base}
    if any(not b['head'] for b in refs if repo['all_branches'] or b['name'] in selected):
        return None
    prs = ([(p['number'], p['head'], p['base']) for p in pulls] if repo['open_prs'] else
           [(int(key[3:]), head, '') for key, head in heads.items() if key.startswith('pr:')])
    return revision_token(repo, {**catalog, 'branches': refs}, prs, snapshot['head'])
