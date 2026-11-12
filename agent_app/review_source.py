"""Complete source inventory in bounded read batches, without executing repository code."""
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from urllib.parse import urlparse
from .review_context import requirement_file
from .runtime import find_git

SOURCE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".cs",
                   ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".kt", ".kts", ".rb", ".php", ".lua",
                   ".sh", ".bash", ".ps1", ".sql", ".vue", ".svelte", ".dart", ".r", ".scala"}
TEXT_SUFFIXES = SOURCE_SUFFIXES | {".md", ".json", ".toml", ".yaml", ".yml", ".html", ".css", ".scss", ".ini", ".cfg", ".xml", ".proto", ".cmake"}
SKIP_PARTS = {"node_modules", ".venv", "venv", ".git", ".data", "dist", "build", "vendor", "coverage", "__pycache__", "graphify-out"}
READ_BATCH_BYTES = 8_000_000


def requirements_file(path):
    return bool(re.fullmatch(r"(?:requirements|constraints)(?:[._-][a-z0-9_-]+)?\.(?:txt|lock)", PurePosixPath(path).name.lower()))


def git(folder, *args, timeout=40, input_data=None, isolated=False, allowed_codes=(0,), with_code=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never", "GIT_OPTIONAL_LOCKS": "0"})
    if isolated:
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                   GIT_AUTHOR_NAME="Who is disaster", GIT_AUTHOR_EMAIL="local@example.invalid",
                   GIT_COMMITTER_NAME="Who is disaster", GIT_COMMITTER_EMAIL="local@example.invalid",
                   GIT_AUTHOR_DATE="2000-01-01T00:00:00+00:00", GIT_COMMITTER_DATE="2000-01-01T00:00:00+00:00")
    # Object-only reads do not use filters, external diffs, hooks, or repository code.
    command = [find_git() or "git", "--no-replace-objects", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
               "-c", "diff.external=", "-c", "protocol.ext.allow=never", "-C", str(folder), *args]
    if not isolated and any(a in {"fetch", "ls-remote"} for a in args) and any(a.startswith("https://github.com/") for a in args):
        from .github_account import git_auth_options, environment
        auth = git_auth_options()
        if auth:
            command[1:1] = auth
            safe_auth_env = environment()
            for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG", "GH_HOST"):
                env.pop(key, None)
            env.update({k: v for k, v in safe_auth_env.items() if k.startswith("GH_")})
    try:
        run = subprocess.run(command, input=input_data, capture_output=True, timeout=timeout, env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except FileNotFoundError:
        raise ValueError("Git 설치가 필요해요.") from None
    except subprocess.TimeoutExpired:
        raise ValueError("Git 응답 시간이 초과됐어요. 연결 상태를 확인해 주세요.") from None
    if run.returncode not in allowed_codes:
        # Never put credential-helper output or embedded credentials in the UI/logs.
        raise ValueError("저장소를 읽지 못했어요. 주소·브랜치와 Git 접근 권한을 확인해 주세요. 비공개 저장소는 로컬 폴더로 연결할 수도 있어요.")
    return (run.returncode, run.stdout) if with_code else run.stdout


def normalize_source(value):
    value = value.strip()
    if not value:
        raise ValueError("GitHub 주소 또는 로컬 Git 폴더를 입력해 주세요.")
    if Path(value).is_dir():
        path = Path(value).resolve()
        git(path, "rev-parse", "--git-dir")
        return str(path), path.name
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        value = "https://github.com/" + value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
        raise ValueError("https://github.com/소유자/저장소 형식이나 로컬 Git 폴더를 사용해 주세요.")
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", p) and p not in {".", ".."} for p in parts):
        raise ValueError("저장소 기본 주소를 입력하고 브랜치는 별도 칸에 적어 주세요.")
    parts[1] = parts[1].removesuffix(".git")
    return "https://github.com/" + "/".join(parts), "/".join(parts)


def github_origin(source):
    """Resolve a local repository or its configured GitHub URL to HTTPS."""
    try:
        url = source if source.startswith(("https://", "git@github.com:")) else git(source, "remote", "get-url", "origin").decode().strip()
        if url.startswith("git@github.com:"):
            url = "https://github.com/" + url.removeprefix("git@github.com:")
        return normalize_source(url)[0] if url.startswith("https://github.com/") else ""
    except ValueError:
        return ""


def valid_branch(value):
    value = value.strip()
    if value and (len(value) > 1024 or re.search(r"[\x00-\x20\x7f~^:?*\[\\]", value) or value == "@" or value.startswith(("-", "/")) or
                  any(t in value for t in ("..", "//", "@{")) or value.endswith(("/", ".")) or
                  any(part.startswith(".") or part.endswith(".lock") for part in value.split("/"))):
        raise ValueError("브랜치 이름을 확인해 주세요.")
    return value


def allowed_file(path):
    p = PurePosixPath(path)
    name = p.name.lower()
    manifest = requirements_file(path) or name in {"uv.lock", "pipfile", "pipfile.lock", "cargo.lock", "dockerfile", "makefile", "cmakelists.txt"}
    return (not any(part.lower() in SKIP_PARTS for part in p.parts) and
            not any(t in name for t in (".env", "secret", "credential", "token", "lock.json", "lock.yaml", "lock.yml")) and
            not name.endswith(".min.js") and (p.suffix.lower() in TEXT_SUFFIXES or manifest))


def redact(text):
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[REDACTED PRIVATE KEY]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b", "[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(?im)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[\"'][^\"'\r\n]{8,}[\"']", r'\1"[REDACTED]"', text)
    return text


class GitSource:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)

    def prepare(self, source, branch=""):
        source, name = normalize_source(source)
        branch = valid_branch(branch)
        if source.startswith("https://"):
            folder = self.cache / hashlib.sha256(source.encode()).hexdigest()[:20]
            if not folder.exists():
                folder.mkdir()
                git(folder, "init", "--bare")
            ref = "refs/heads/" + branch if branch else "HEAD"
            git(folder, "fetch", "--no-tags", "--depth=2", "--", source + ".git", ref, timeout=90)
            head = git(folder, "rev-parse", "FETCH_HEAD").decode().strip()
            web = source
        else:
            folder, web = Path(source), ""
            ref = "refs/heads/" + branch if branch else "HEAD"
            head = git(folder, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
        return {"folder": str(folder), "head": head, "web": web, "name": name}

    def read(self, state, previous=""):
        folder, head = Path(state["folder"]), state["head"]
        base = previous
        if not base:
            try:
                base = git(folder, "rev-parse", "--verify", head + "^").decode().strip()
            except ValueError:
                base = ""
        changed, deleted, diff = [], [], ""
        if base and re.fullmatch(r"[a-f0-9]{40,64}", base):
            try:
                changed = [p for p in git(folder, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", "--diff-filter=AMT", base, head, "--").decode("utf-8", "replace").split("\0") if allowed_file(p)]
                deleted = [p for p in git(folder, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--name-only", "-z", "--diff-filter=D", base, head, "--").decode("utf-8", "replace").split("\0") if allowed_file(p)]
            except ValueError:
                base = ""
        records = []
        for item in git(folder, "ls-tree", "-r", "-l", "-z", head).split(b"\0"):
            if not item:
                continue
            meta, path = item.split(b"\t", 1)
            mode, kind, oid, size = meta.split()
            path = path.decode("utf-8", "replace")
            # Excludes symlinks and submodules, even when their names resemble code.
            if kind == b"blob" and mode in {b"100644", b"100755"} and allowed_file(path):
                records.append((path, oid.decode(), int(size)))
        records.sort(key=lambda r: (r[0] not in changed, not requirement_file(r[0]), PurePosixPath(r[0]).suffix not in SOURCE_SUFFIXES, r[0]))
        files, omitted, selected, batch_bytes, batches = {}, 0, [], 0, []
        for path, oid, size in records:
            if selected and (len(selected) >= 128 or batch_bytes + size > READ_BATCH_BYTES):
                batches.append(selected)
                selected, batch_bytes = [], 0
            selected.append((path, oid, size))
            batch_bytes += size
        if selected: batches.append(selected)
        for selected in batches:
            blobs = git(folder, "cat-file", "--batch", input_data=("\n".join(oid for _, oid, _ in selected) + "\n").encode())
            offset = 0
            for path, oid, size in selected:
                end = blobs.index(b"\n", offset)
                actual_size = int(blobs[offset:end].split()[-1])
                blob = blobs[end+1:end+1+actual_size]
                offset = end + 2 + actual_size
                if b"\0" in blob:
                    omitted += 1
                    continue
                files[path] = redact(blob.decode("utf-8", "replace"))
        diff_paths = (changed + deleted)[:50]
        if base and diff_paths:
            diff = redact(git(folder, "diff", "--no-ext-diff", "--no-textconv", "--unified=3", base, head, "--", *diff_paths).decode("utf-8", "replace"))[:24000]
        log = git(folder, "log", "-1", "--format=%s", head).decode("utf-8", "replace").strip()
        return {**state, "base": base, "files": files, "changed": changed, "deleted": deleted,
                "diff": diff, "commit_title": redact(log), "omitted": omitted,
                "scope": "커밋된 코드 기준 · Python 및 JS/TS 상대경로 의존성 정적 분석 · 다른 언어는 AI 검토"}
