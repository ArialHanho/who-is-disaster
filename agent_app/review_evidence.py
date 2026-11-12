"""Resolve simple Python references from source evidence without importing it."""
import ast
from collections import Counter
import warnings


def module_names(path):
    module = path.removesuffix('.py').replace('/', '.').removesuffix('.__init__')
    return {module, module.removeprefix('src.')}


def dotted_name(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        return (node.id, *reversed(parts))
    return ()


class PythonSource:
    def __init__(self, path, body):
        self.body = body
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', SyntaxWarning)
            tree = ast.parse(body)
        nodes = list(ast.walk(tree))
        self.calls = [node for node in nodes if isinstance(node, ast.Call)]
        self.definitions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        self.assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
        module_targets = {target for statement in self.assignments
                          for target in (statement.targets if isinstance(statement, ast.Assign) else [statement.target])
                          if isinstance(target, ast.Name)}
        self.assigned = Counter(target.id for target in module_targets)
        self.defined = Counter(node.name for node in self.definitions)
        self.shadowed = {node.id for node in nodes if isinstance(node, ast.Name)
                         and isinstance(node.ctx, (ast.Store, ast.Del)) and node not in module_targets}
        self.shadowed.update(names[0] for node in nodes if isinstance(node, ast.Attribute)
                             and isinstance(node.ctx, (ast.Store, ast.Del)) and (names := dotted_name(node)))
        self.has_wildcard = any(isinstance(node, ast.ImportFrom) and any(name.name == '*' for name in node.names)
                                for node in nodes)
        self.shadowed.update(node.arg for node in nodes if isinstance(node, ast.arg))
        self.shadowed.update(node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                             and node not in self.definitions)
        self.imports = {}
        for node in tree.body:
            if isinstance(node, ast.Import):
                for name in node.names:
                    self.bind(name.asname or name.name.split('.')[0], name.name if name.asname else name.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                if node.level:
                    package = path.removesuffix('.py').replace('/', '.').split('.')[:-node.level]
                    module = '.'.join([*package, *([module] if module else [])])
                for name in node.names:
                    self.bind(name.asname or name.name, '.'.join(part for part in (module, name.name) if part))
        # An import inside a conditional or function can shadow a module binding.
        top_level = set(tree.body)
        for node in nodes:
            if isinstance(node, (ast.Import, ast.ImportFrom)) and node not in top_level:
                self.shadowed.update(name.asname or name.name.split('.')[0] for name in node.names)

    def bind(self, name, target):
        if name in self.imports and self.imports[name] != target:
            self.shadowed.add(name)
        self.imports[name] = target

    def symbols_at(self, line):
        for node in self.definitions:
            if node.lineno <= line <= node.end_lineno and (not isinstance(node, ast.ClassDef) or line == node.lineno):
                yield node.name, True
        for node in self.assignments:
            if node.lineno <= line <= node.end_lineno:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        yield target.id, False

    def qualified(self, node, path):
        names = dotted_name(node)
        if not names or self.has_wildcard:
            return set()
        root, *rest = names
        if root in self.shadowed:
            return set()
        if root in self.imports and not self.defined[root] and not self.assigned[root]:
            return {'.'.join((self.imports[root], *rest))}
        if not rest and root not in self.imports:
            if self.defined[root] + self.assigned[root] == 1:
                return {module + '.' + root for module in module_names(path)}
        return set()


class ReviewEvidence:
    def __init__(self, views):
        self.views = views
        self.sources = {}

    def source(self, evidence):
        path = evidence['path']
        if not path.endswith('.py'):
            return None
        body = self.views[evidence.get('view', 'combined')]['files'][path]
        key = path, body
        if key not in self.sources:
            try:
                self.sources[key] = PythonSource(path, body)
            except (SyntaxError, ValueError, RecursionError):
                self.sources[key] = None
        return self.sources[key]

    def related_call(self, implementations, caller, *, invocation=False):
        source = self.source(caller)
        if source is None:
            return False
        targets = set()
        for implementation in implementations:
            origin = self.source(implementation)
            if origin is not None:
                for symbol, callable_symbol in origin.symbols_at(implementation['line']):
                    if (not invocation or callable_symbol) and symbol not in origin.shadowed and not origin.has_wildcard and origin.defined[symbol] + origin.assigned[symbol] == 1:
                        targets.update((module + '.' + symbol, callable_symbol)
                                       for module in module_names(implementation['path']))
        if not targets:
            return False
        for call in source.calls:
            if not call.lineno <= caller['line'] <= call.end_lineno:
                continue
            excerpt = ast.get_source_segment(source.body, call) or ''
            if caller['quote'] not in excerpt and excerpt not in caller['quote']:
                continue
            called = {(name, True) for name in source.qualified(call.func, caller['path'])}
            if called & targets:
                return True
            if not invocation:
                values = [*call.args, *(arg.value for arg in call.keywords)]
                if any((name, False) in targets for value in values for name in source.qualified(value, caller['path'])):
                    return True
        return False
