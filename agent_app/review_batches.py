"""Finish the full source pass after the focused relationship review.

The request size is bounded, never the number of files or requests. Identical
file contents across refs share a pass; each distinct version is retained.
"""
import hashlib
from pathlib import PurePosixPath
import re
from .review_context import requirement_file
from .review_source import SOURCE_SUFFIXES

SOURCE_CHUNK_CHARS = 100000
SOURCE_BATCH_CHARS = 120000
SOURCE_BATCH_FILES = 80


def source_lines(path, body):
    dependency_hashes = re.fullmatch(r'(requirements|constraints).*\.(txt|lock)', path.rsplit('/', 1)[-1], re.I)
    for number, line in enumerate(body.splitlines(), 1):
        if dependency_hashes and (not line.strip() or line.lstrip().startswith(('#', '--hash='))):
            continue
        yield number, line


def missing_chunks(view, path, body, shown, budget=SOURCE_CHUNK_CHARS):
    rows, used = [], 0
    for number, line in source_lines(path, body):
        numbered = f'{number}: {line}'
        if numbered in shown:
            continue
        if len(numbered) > budget:
            if rows:
                yield {'view':view, 'path':path, 'code':'\n'.join(rows), 'truncated':True}
                rows, used = [], 0
            width = budget-len(f'{number}: ')
            for start in range(0, len(line), width):
                yield {'view':view, 'path':path, 'code':f'{number}: {line[start:start+width]}',
                       'truncated':True, 'partial_line':number, 'column_start':start}
            continue
        if rows and used+len(numbered)+1 > budget:
            yield {'view':view, 'path':path, 'code':'\n'.join(rows), 'truncated':True}
            rows, used = [], 0
        rows.append(numbered); used += len(numbered)+1
    if rows:
        yield {'view':view, 'path':path, 'code':'\n'.join(rows), 'truncated':True}


def complete_contexts(snapshot, focused, graph, rules, source_excerpt, *, view_graphs=None):
    """Stable full-file pages; changing the focused diff cannot reshuffle them."""
    views = {'combined':snapshot, **{key:value for key,value in snapshot.get('views', {}).items() if not value.get('merged')}}
    versions, identities = {}, {}
    for view, data in views.items():
        for path, body in sorted(data['files'].items()):
            identity = (path, hashlib.sha256(body.encode()).hexdigest())
            identities[view,path] = identity
            item = versions.setdefault(identity, {'view':view, 'path':path, 'body':body, 'copies':[]})
            item['copies'].append(view)
    # Text edits must not move unrelated code to different batches.
    sections = {True: [], False: []}
    for item in versions.values():
        for chunk in missing_chunks(item['view'],item['path'],item['body'],set()):
            chunk['same_content_in'] = item['copies']
            chunk['context_roles'] = ['full_source_pass']
            sections[PurePosixPath(item['path']).suffix.lower() in SOURCE_SUFFIXES].append(chunk)
    pages = []
    for chunks in sections.values():
        page, used, paths = [], 0, set()
        for chunk in chunks:
            identity = (chunk['view'], chunk['path'])
            anchor = int(hashlib.sha256(chunk['path'].encode()).hexdigest()[:8], 16) % 4 == 0
            if page and (used + len(chunk['code']) > SOURCE_BATCH_CHARS or identity in paths
                         or len(page) >= SOURCE_BATCH_FILES or used >= 90000 and anchor):
                pages.append(page)
                page, used, paths = [], 0, set()
            page.append(chunk)
            used += len(chunk['code'])
            paths.add(identity)
        if page:
            pages.append(page)
    contexts = list(focused)
    graphs = {key:(view_graphs or {}).get(key, graph) for key in views}
    reverse = {key:{} for key in views}
    for key, edges in graphs.items():
        for path, targets in edges.items():
            for target in targets:reverse[key].setdefault(target,set()).add(path)
    contracts = {key: sorted(path for path in view['files'] if '/' not in path and requirement_file(path))
                 for key, view in views.items()}
    for page in pages:
        entries = list(page)
        included = {(e['view'],e['path']) for e in entries}
        support_budget = 10000
        related_hashes = set()
        for entry in page:
            view, path = entry['view'], entry['path']
            source = views[view]
            related = sorted(set(graphs[view].get(path, ())) | reverse[view].get(path, set()))
            related = list(dict.fromkeys([*contracts[view], *related]))
            related_hashes.update((view,p,identities[view,p][1]) for p in related if (view,p) in identities)
            for nearby in related:
                if support_budget < 300:break
                if nearby not in source['files'] or (view,nearby) in included:continue
                requirement = nearby in contracts[view]
                value = source_excerpt(nearby, source['files'][nearby], min(1800 if requirement else 2500, support_budget))
                entries.append({'view':view,'path':nearby,**value,
                                'context_roles':['requirement' if requirement else 'dependency_or_caller']})
                included.add((view,nearby));support_budget-=len(value['code'])
        contexts.append({
            'repository':snapshot['name'], 'project_rules':rules, 'initial_review':True,
            'files':entries, 'changed_files':[], 'deleted_files':[],
            'related_source_versions': sorted(related_hashes),
            'dependency_graph':{v:{e['path']:graphs[v].get(e['path'],[]) for e in entries if e['view']==v}
                                for v in sorted({e['view'] for e in entries})},
            'parallel_changes':[{'view':key,'label':views[key].get('label',key),
                                 'kind':views[key].get('kind','combined')} for key in sorted({e['view'] for e in page})],
            'review_pass':'Content review of current source. Review design and correctness from the provided code and caller evidence. Commit changes, new features and cross-branch integration are reviewed separately. Chunks retain original line numbers; missing lines are in other batches, not deleted code.',
            'coverage':{'ai_excerpt_files':len(entries)},
        })
    return contexts, {'source_files':len({path for _,path in identities}), 'source_versions':len(versions),
                      'source_chunks':sum(map(len, sections.values())), 'source_pass_batches':len(pages)}
