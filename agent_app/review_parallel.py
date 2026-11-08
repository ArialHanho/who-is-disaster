"""Read-only multi-ref capture and isolated virtual integration (no checkout)."""
import hashlib
import json
from pathlib import Path
import re
import tempfile
from .review_source import git, valid_branch, normalize_source, allowed_file

def github_origin(source):
    if source.startswith("https://"):
        return source
    try:
        url = git(source, "remote", "get-url", "origin").decode().strip()
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url[len("git@github.com:"):]
        return normalize_source(url)[0] if url.startswith("https://github.com/") else ""
    except ValueError:
        return ""


def parse_targets(value, source):
    entries = value.splitlines() if isinstance(value, str) else value or []
    targets = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        if entry.startswith("https://"):
            match = re.fullmatch(r"(https://github.com/[\w.-]+/[\w.-]+)/pull/([1-9][0-9]*)/?", entry)
            if not match or match[1].lower() != github_origin(source).lower():
                raise ValueError("연결한 저장소의 PR 주소를 입력해 주세요.")
            entry = "pr:" + match[2]
        if entry.startswith(("pr:", "#")):
            number = entry.removeprefix("pr:").removeprefix("#")
            if not re.fullmatch(r"[1-9][0-9]{0,8}", number):
                raise ValueError("PR은 pr:123 또는 GitHub PR 주소로 입력해 주세요.")
            entry = "pr:" + number
        else:
            entry = valid_branch(entry)
        if entry not in targets:
            targets.append(entry)
    return targets


def fetch_ref(reader, web, ref):
    return fetch_refs(reader, web, [ref])[ref]


def fetch_refs(reader, web, refs):
    folder = reader.cache / hashlib.sha256(web.encode()).hexdigest()[:20]
    if not folder.exists():
        folder.mkdir()
        git(folder, "init", "--bare", isolated=True)
    refs = list(dict.fromkeys(refs))
    mapped = {ref: "refs/companion/" + hashlib.sha256(ref.encode()).hexdigest() for ref in refs}
    for start in range(0, len(refs), 48):
        specs = ["+" + ref + ":" + mapped[ref] for ref in refs[start:start+48]]
        git(folder, "fetch", "--no-tags", "--depth=256", "--", web + ".git", *specs, timeout=120)
    return {ref: {"folder": str(folder), "head": git(folder, "rev-parse", mapped[ref]).decode().strip(), "web": web} for ref in refs}


def _worktrees(folder):
    entries = []
    for block in git(folder, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
        values = dict(item.decode("utf-8", "replace").split(" ", 1) for item in block.split(b"\0") if b" " in item)
        if "worktree" in values and "HEAD" in values:
            path = Path(values["worktree"])
            if path.is_dir():
                entries.append((path, values.get("branch", "detached").removeprefix("refs/heads/")))
    return entries


def _capture_working(folder):
    """Capture final saved contents once; staged state is also part of the cache key."""
    head = git(folder, "rev-parse", "HEAD").decode().strip()
    def state():
        return git(folder, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    before = state()
    index = git(folder, "ls-files", "--stage", "-z")
    if any(b" 1\t" in p or b" 2\t" in p or b" 3\t" in p for p in index.split(b"\0")):
        raise ValueError(f"{folder.name}: 진행 중인 Git 충돌을 먼저 해결해 주세요. 미해결 index를 정상 코드로 합치지 않아요.")
    names = git(folder, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", "HEAD", "--")
    names += git(folder, "ls-files", "--others", "--exclude-standard", "-z")
    autocrlf = git(folder, "config", "--get", "core.autocrlf", allowed_codes=(0, 1)).strip().lower() in {b"true", b"input"}
    attrs = git(folder, "check-attr", "-z", "--stdin", "text", "eol", input_data=names).split(b"\0") if names else []
    attributes = {}
    for i in range(0, len(attrs) - 2, 3):
        attributes.setdefault(attrs[i], {})[attrs[i+1]] = attrs[i+2]
    files, omitted, total, stamps = {}, 0, 0, {}
    for raw in sorted(set(names.split(b"\0")) - {b""}):
        name = raw.decode("utf-8", "replace")
        if not allowed_file(name):
            continue
        path = folder / name
        if not path.exists() and not path.is_symlink():
            files[name] = None
            continue
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
            omitted += 1
            continue
        stat = path.stat()
        body = path.read_bytes()
        stamps[name] = (stat.st_mtime_ns, stat.st_size)
        if b"\0" in body:
            omitted += 1
            continue
        # Match built-in Git EOL normalization without executing clean filters.
        attr = attributes.get(raw, {})
        normalize = attr.get(b"text") != b"unset" and (autocrlf or attr.get(b"text") in {b"set", b"auto"} or attr.get(b"eol") in {b"lf", b"crlf"})
        files[name] = body.replace(b"\r\n", b"\n") if normalize else body
        total += len(body)
    if before != state() or head != git(folder, "rev-parse", "HEAD").decode().strip() or any(
            not (folder / n).exists() or ((folder / n).stat().st_mtime_ns, (folder / n).stat().st_size) != stamp
            for n, stamp in stamps.items()):
        raise ValueError("수집 중 코드가 바뀌었어요. 저장이 끝난 뒤 다시 분석해 주세요.")
    digest = hashlib.sha256(index + before)
    for name, body in sorted(files.items()):
        digest.update(name.encode() + b"\0" + (b"deleted" if body is None else hashlib.sha256(body).digest()))
    return {"head": head, "dirty_files": files, "dirty_signature": digest.hexdigest(), "omitted": omitted}


def capture(reader, source, branch, targets, working):
    """Resolve every selected ref before deciding whether an AI call is needed."""
    base = reader.prepare(source, branch)
    web = github_origin(source)
    base.update(id="base", label="기준: " + (branch or "HEAD"), kind="base", web=web)
    remote = {}
    if source.startswith("https://") and targets:
        base_ref = "refs/heads/" + branch if branch else "HEAD"
        refs = [base_ref] + ["refs/pull/" + t[3:] + "/head" if t.startswith("pr:") else "refs/heads/" + t for t in targets]
        remote = fetch_refs(reader, source, refs)
        base.update(remote[base_ref])
    views = []
    for target in targets:
        if target.startswith("pr:"):
            if not web:
                raise ValueError("PR 분석에는 이 저장소의 GitHub origin 주소가 필요해요.")
            ref = "refs/pull/" + target[3:] + "/head"
            state = remote[ref] if remote else fetch_ref(reader, web, ref)
            kind, title = "pr", "PR #" + target[3:]
        elif source.startswith("https://"):
            state = remote["refs/heads/" + target]
            kind, title = "branch", target
        else:
            ref = "refs/remotes/" + target if target.startswith("origin/") else "refs/heads/" + target
            state = {"folder": source, "head": git(source, "rev-parse", "--verify", ref + "^{commit}").decode().strip(), "web": web}
            kind, title = "branch", target
        views.append({**state, "id": "branch:" + target if kind == "branch" else target, "label": title, "kind": kind})
    if working:
        if source.startswith("https://"):
            raise ValueError("미커밋 변경은 로컬 Git 폴더를 연결한 뒤 포함할 수 있어요.")
        for folder, name in _worktrees(source):
            data = _capture_working(folder)
            views.append({**data, "folder": str(folder), "id": "worktree:" + hashlib.sha256(str(folder).encode()).hexdigest()[:12],
                          "label": name + " · " + folder.name + " (작업 폴더)", "kind": "worktree", "web": web})
    key = [[v["id"], v["head"], v.get("dirty_signature", "")] for v in [base] + views]
    signature = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
    return {"base": base, "views": views, "signature": signature}


def compose(reader, captured):
    """All object/index writes and merge drivers live in a disposable bare repo."""
    base, targets = captured["base"], captured["views"]
    with tempfile.TemporaryDirectory(prefix="integration-", dir=reader.cache) as temp:
        scratch = Path(temp)
        def run(*args, **kw):
            return git(scratch, *args, isolated=True, **kw)
        object_format = git(base["folder"], "rev-parse", "--show-object-format").decode().strip()
        run("init", "--bare", "--object-format=" + object_format)
        alternates = set()
        for state in [base] + targets:
            path = git(state["folder"], "rev-parse", "--path-format=absolute", "--git-path", "objects").decode().strip()
            alternates.add(Path(path).as_posix())
        (scratch / "objects/info/alternates").write_text("\n".join(sorted(alternates)) + "\n", encoding="utf-8", newline="\n")
        def commit(tree, parents):
            args = ["-c", "commit.gpgsign=false", "commit-tree", tree]
            for parent in dict.fromkeys(parents):
                args.extend(["-p", parent])
            return run(*args, input_data=b"Who is disaster virtual integration\n").decode().strip()
        def read(state, head, ancestor):
            data = reader.read({**state, "name": base["name"], "folder": temp, "head": head}, ancestor)
            # Keep real revision metadata; synthetic commits must never become GitHub links.
            return {**data, "head": state["head"], "folder": state["folder"],
                    "omitted": data["omitted"] + state.get("omitted", 0)}
        views = {"base": read(base, base["head"], base["head"])}
        current, conflicts, applied, conflict_paths, independent = base["head"], [], ["base"], set(), []
        for state in targets:
            target = state["head"]
            if state.get("dirty_files"):
                run("read-tree", target)
                for path, body in state["dirty_files"].items():
                    if body is None:
                        record = b"0 " + b"0" * (64 if object_format == "sha256" else 40) + b"\t" + path.encode() + b"\0"
                    else:
                        oid = run("hash-object", "-w", "--stdin", input_data=body).strip()
                        # Preserve executable mode of tracked files; untracked files are regular.
                        entry = run("ls-tree", target, "--", path)
                        mode = entry.split(b" ", 1)[0] if entry.startswith(b"100755 ") else b"100644"
                        record = mode + b" " + oid + b"\t" + path.encode() + b"\0"
                    run("update-index", "-z", "--index-info", input_data=record)
                target = commit(run("write-tree").decode().strip(), [target])
            try:
                ancestor = run("merge-base", base["head"], target).decode().strip()
            except ValueError:
                # Orphan branches (for example gh-pages) are visible and reviewed separately.
                views[state["id"]] = {**read(state, target, target), "independent": True}
                independent.append(state["id"])
                continue
            views[state["id"]] = {**read(state, target, ancestor), "merged": ancestor == target}
            if run("merge-base", "--is-ancestor", target, current, allowed_codes=(0, 1), with_code=True)[0] == 0:
                continue
            code, output = run("merge-tree", "--write-tree", "--name-only", "-z", "--no-messages", current, target,
                               allowed_codes=(0, 1), with_code=True, timeout=90)
            parts = output.split(b"\0")
            tree = parts[0].decode().strip()
            if not re.fullmatch(r"[a-f0-9]{40,64}", tree):
                raise ValueError("Git 가상 통합 결과를 읽지 못했어요. Git 2.38 이상이 필요해요.")
            if code:
                paths = [p.decode("utf-8", "replace") for p in parts[1:] if p]
                if not paths:
                    raise ValueError("가상 통합 충돌 위치를 읽지 못했어요.")
                conflicts.append({"left": list(applied), "right": state["id"], "paths": paths})
                conflict_paths.update(paths)
            current = commit(tree, [current, target])
            applied.append(state["id"])
        combined = reader.read({**base, "folder": temp, "head": current}, base["head"])
        for path in conflict_paths:
            combined["files"].pop(path, None)
        combined.update(head=base["head"], base=base["head"], views=views, conflicts=conflicts,
                        snapshot_id=captured["signature"], virtual=True, composition_complete=not conflicts and not independent,
                        independent_views=independent,
                        scope="선택한 전체 브랜치·PR·작업 폴더 분석 · 충돌 파일 제외 · 공통 조상을 읽을 수 없는 브랜치는 별도 원문으로 검토")
        combined["omitted"] += sum(v.get("omitted", 0) for v in targets)
        return combined


def sources_metadata(snapshot):
    return {key: {**{k: v.get(k, "") for k in ("label", "kind", "head", "web", "folder", "merged", "independent", "base_branch")},
                  "uncommitted": bool(v.get("dirty_files")) if v.get("kind") == "worktree" else False}
            for key, v in snapshot.get("views", {}).items()}
