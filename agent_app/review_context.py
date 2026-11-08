"""Local source retrieval for review. References are leads, never proof of a bug."""
import ast
from collections import defaultdict
import hashlib
from pathlib import PurePosixPath
import re
import warnings


def requirement_file(path):
    p = PurePosixPath(path)
    return p.suffix.lower() == '.md' and bool(re.search(r'readme|agents|claude|design|spec|requirement|architecture', p.name, re.I))


def symbol_index(files):
    words, calls, definitions, bodies = defaultdict(dict), defaultdict(dict), defaultdict(list), defaultdict(list)
    errors = []
    for path, body in files.items():
        for n, line in enumerate(body.splitlines(), 1):
            for word in set(re.findall(r'[A-Za-z_$][\w$]{2,}', line)):
                words[word].setdefault(path, []).append(n)
        if not path.endswith('.py'):
            for match in re.finditer(r'(?m)\b(?:function|class|interface|def|fn|func)\s+(\w+)', body):
                definitions[path].append((match[1], body.count('\n', 0, match.start()) + 1))
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', SyntaxWarning)
                tree = ast.parse(body)
        except (SyntaxError, ValueError, RecursionError):
            errors.append(path); continue
        aliases = {a.asname: a.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for a in node.names if a.asname}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions[path].append((node.name, node.lineno))
                statements = node.body
                if statements and isinstance(statements[0], ast.Expr) and isinstance(statements[0].value, ast.Constant) and isinstance(statements[0].value.value, str):
                    statements = statements[1:]
                shape = ast.dump(ast.Module(body=statements, type_ignores=[]), include_attributes=False)
                if len(shape) >= 60:
                    bodies[hashlib.sha256(shape.encode()).hexdigest()].append((path, node.lineno, node.name))
            elif isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ''
                if name:
                    calls[aliases.get(name, name)].setdefault(path, []).append(node.lineno)
    return words, calls, definitions, bodies, errors


def related_context(view, focus, graph):
    files = view['files']
    focus = set(focus) & files.keys()
    words, calls, definitions, bodies, errors = symbol_index(files)
    roles, anchors = defaultdict(set), defaultdict(set)
    for path in focus:
        roles[path].add('changed')
        anchors[path].update(n for _, n in definitions[path][:12])
    terms = {name for p in focus for name, _ in definitions[p] if len(name) > 3 and name not in {'main', 'run', '__init__', '__call__'}}
    terms.update(PurePosixPath(p).stem for p in focus if len(PurePosixPath(p).stem) > 3)
    selected_terms = sorted(terms)[:100]
    for term in selected_terms:
        defining_paths = {p for p in focus if any(name == term for name, _ in definitions[p])}
        for path, lines in words.get(term, {}).items():
            if path not in focus:
                roles[path].add('reference')
                anchors[path].update(lines[:12])
        for path, lines in calls.get(term, {}).items():
            # A same-named local function in an unrelated script is only a search lead.
            # Require an import edge before treating it as a caller of this implementation.
            if path not in focus and not defining_paths.intersection(graph.get(path, ())):
                continue
            roles[path].add('caller')
            anchors[path].update(lines[:12])
    for entries in bodies.values():
        if len(entries) > 1 and any(p in focus for p, _, _ in entries):
            for path, line, _ in entries:
                roles[path].add('similar_implementation'); anchors[path].add(line)
    for path, targets in graph.items():
        if path in focus:
            for target in targets:
                roles[target].add('dependency')
        elif focus.intersection(targets):
            roles[path].add('importer')
    for path in files:
        if requirement_file(path): roles[path].add('requirement')
    # Interleave implementation and supporting material; a large diff cannot starve callers.
    order, seen = [], set()
    buckets = [sorted(p for p in roles if role in roles[p]) for role in
               ('requirement', 'changed', 'caller', 'similar_implementation', 'importer', 'dependency', 'reference')]
    for index in range(max((len(b) for b in buckets), default=0)):
        for bucket in buckets:
            if index < len(bucket) and bucket[index] not in seen:
                order.append(bucket[index]); seen.add(bucket[index])
    order.extend(p for p in sorted(files) if p not in seen)
    # Diffs point to changed functions below a long file's header.
    path = None
    for line in view.get('diff', '').splitlines():
        if line.startswith('+++ b/'): path = line[6:]
        match = re.match(r'@@ .*\+(\d+)', line)
        if match and path in files: anchors[path].add(int(match[1]))
    return {'order': order, 'roles': roles, 'anchors': anchors,
            'scanned_files': len(files), 'omitted_files': view.get('omitted', 0),
            'parse_errors': errors, 'terms_truncated': len(terms) > len(selected_terms)}


def excerpt(body, budget, anchors=()):
    lines = body.splitlines()
    full = '\n'.join(f'{i}: {line}' for i, line in enumerate(lines, 1))
    if len(full) <= budget:
        return {'code': full, 'truncated': False}
    priorities = list(range(1, min(10, len(lines)) + 1))
    for line in sorted(anchors)[:18]:
        priorities.extend(range(max(1, line - 3), min(len(lines), line + 18) + 1))
    priorities.extend(range(1, len(lines) + 1))
    chosen, used = {}, 0
    for n in dict.fromkeys(priorities):
        text = f'{n}: {lines[n-1]}'
        if used + len(text) + 1 > budget: continue
        chosen[n] = text; used += len(text) + 1
    return {'code': '\n'.join(chosen[n] for n in sorted(chosen)), 'truncated': True,
            'excerpt_kind': 'selected original lines; gaps are omitted, not deleted code'}


def collect_context(view, focus, graph, budget, view_id, source_excerpt):
    found = related_context(view, focus, graph)
    entries = []
    relevant = [p for p in found['order'] if found['roles'][p]]
    for index, path in enumerate(found['order']):
        if budget < 160 or len(entries) >= 35: break
        roles = found['roles'][path]
        cap = max(350, budget // max(1, min(8, len(relevant) - index)))
        cap = min(budget, cap, 1800 if 'requirement' in roles else 7000)
        # Keep the existing special treatment of generated dependency hash files.
        value = source_excerpt(path, view['files'][path], cap, found['anchors'][path])
        if not value['code']: continue
        entries.append({'view': view_id, 'path': path, **value, 'context_roles': sorted(roles)})
        budget -= len(value['code'])
    included = {e['path'] for e in entries}
    report = {k: found[k] for k in ('scanned_files', 'omitted_files', 'parse_errors', 'terms_truncated')}
    report.update(view=view_id, supporting_files=len(relevant), included_files=len(entries),
                  missing_supporting_files=[p for p in relevant if p not in included][:30],
                  reference_search='literal names, strings, Python calls and equal Python bodies; dynamic dispatch is not proven')
    return entries, report
