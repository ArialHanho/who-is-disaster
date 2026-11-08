"""Finish the full source pass after the focused relationship review.

The request size is bounded, never the number of files or requests. Identical
file contents across refs share a pass; each distinct version is retained.
"""
import hashlib
import re

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


def complete_contexts(snapshot, focused, graph, rules, source_excerpt):
    """Append every unread line, with nearby dependency/caller evidence per page."""
    views = {'combined':snapshot, **{key:value for key,value in snapshot.get('views', {}).items() if not value.get('merged')}}
    versions, identities = {}, {}
    for view, data in views.items():
        for path, body in sorted(data['files'].items()):
            identity = (path, hashlib.sha256(body.encode()).hexdigest())
            identities[view,path] = identity
            item = versions.setdefault(identity, {'view':view, 'path':path, 'body':body, 'copies':[], 'shown':set()})
            item['copies'].append(view)
    for context in focused:
        for entry in context['files']:
            identity = identities.get((entry.get('view','combined'),entry['path']))
            if identity in versions:
                versions[identity]['shown'].update(entry['code'].splitlines())
    chunks = []
    for item in versions.values():
        for chunk in missing_chunks(item['view'],item['path'],item['body'],item['shown']):
            chunk['same_content_in'] = item['copies']
            chunk['context_roles'] = ['full_source_pass']
            chunks.append(chunk)
    pages, page, used, paths = [], [], 0, set()
    for chunk in chunks:
        identity = (chunk['view'],chunk['path'])
        if page and (used+len(chunk['code']) > SOURCE_BATCH_CHARS or identity in paths or len(page)>=SOURCE_BATCH_FILES):
            pages.append(page);page,used,paths=[],0,set()
        page.append(chunk);used+=len(chunk['code']);paths.add(identity)
    if page:pages.append(page)
    contexts = list(focused)
    reverse = {}
    for path, targets in graph.items():
        for target in targets:reverse.setdefault(target,set()).add(path)
    for page in pages:
        entries = list(page)
        included = {(e['view'],e['path']) for e in entries}
        support_budget = 10000
        for entry in page:
            view, path = entry['view'], entry['path']
            source = views[view]
            related = sorted(set(graph.get(path, ())) | reverse.get(path,set()))
            for nearby in related:
                if support_budget < 300:break
                if nearby not in source['files'] or (view,nearby) in included:continue
                value = source_excerpt(nearby,source['files'][nearby],min(2500,support_budget))
                entries.append({'view':view,'path':nearby,**value,'context_roles':['dependency_or_caller']})
                included.add((view,nearby));support_budget-=len(value['code'])
        page_paths = {e['path'] for e in entries}
        contexts.append({
            'repository':snapshot['name'], 'commit':snapshot['head'], 'base':snapshot['base'],
            'project_rules':rules, 'initial_review':snapshot.get('initial_review',True),
            'files':entries, 'changed_files':sorted(page_paths & set(snapshot['changed'])),
            'deleted_files':snapshot['deleted'], 'changed_since_last_review':snapshot.get('new_changed',[]),
            'dependency_graph':{p:graph.get(p,[]) for p in page_paths},
            'parallel_changes':[{'view':key,'label':views[key].get('label',key),'head':views[key]['head'],
                                 'kind':views[key].get('kind','combined')} for key in sorted({e['view'] for e in page})],
            'composition_complete':snapshot.get('composition_complete',True),
            'review_pass':'Full source continuation. The file chunks retain original line numbers; missing lines are in other batches, not deleted code.',
            'coverage':{'ai_excerpt_files':len(entries)},
        })
    return contexts, {'source_files':len({path for _,path in identities}), 'source_versions':len(versions),
                      'source_chunks':len(chunks), 'source_pass_batches':len(pages)}
