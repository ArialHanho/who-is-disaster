"""GitHub account access through the official CLI. No application token storage."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile
from .runtime import app_root

CLI_VERSION = "2.103.3"
TOOLS = app_root() / ".data/tools/github-cli"
_install_lock = threading.Lock()
_auth_lock = threading.Lock()
_auth_cache = (0, False)


def cli_path():
    installed = shutil.which("gh")
    bundled = TOOLS / "bin/gh.exe"
    return Path(installed) if installed else bundled if bundled.is_file() else None


def environment():
    # Prefer the explicitly signed-in CLI account, never an unrelated injected API token.
    env = {k: v for k, v in os.environ.items() if k not in {"GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_DEBUG", "GH_HOST"}}
    env.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", GH_NO_EXTENSION_UPDATE_NOTIFIER="1", NO_COLOR="1", GH_PAGER="cat")
    return env


def _download(url, limit):
    try:
        with urlopen(Request(url, headers={"User-Agent": "Code-Companion", "Accept": "application/octet-stream"}), timeout=60) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError("GitHub 연결 도구의 다운로드 크기를 확인하지 못했어요.")
        return data
    except (HTTPError, URLError, TimeoutError, OSError):
        raise ValueError("GitHub 연결 도구를 받지 못했어요. 네트워크를 확인하고 다시 연결해 주세요.") from None


def ensure_cli():
    with _install_lock:
        found = cli_path()
        if found:
            return found
        if os.name != "nt":
            raise ValueError("GitHub CLI(gh)를 설치한 뒤 다시 연결해 주세요.")
        arch = "arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "amd64"
        filename = f"gh_{CLI_VERSION}_windows_{arch}.zip"
        release = f"https://github.com/cli/cli/releases/download/v{CLI_VERSION}/"
        manifest = _download(release + f"gh_{CLI_VERSION}_checksums.txt", 500_000).decode("utf-8")
        expected = next((line.split()[0] for line in manifest.splitlines() if line.split() and line.split()[-1].lstrip("*") == filename), "")
        archive = _download(release + filename, 80_000_000)
        if not re.fullmatch(r"[a-f0-9]{64}", expected) or hashlib.sha256(archive).hexdigest() != expected:
            raise ValueError("GitHub 연결 도구의 체크섬이 맞지 않아 설치하지 않았어요.")
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            names = [n for n in bundle.namelist() if n == "bin/gh.exe" or n.endswith("/bin/gh.exe")]
            if len(names) != 1 or bundle.getinfo(names[0]).file_size > 100_000_000:
                raise ValueError("GitHub 연결 도구의 파일 구성을 읽지 못했어요.")
            binary = bundle.read(names[0])
            license_names = [n for n in bundle.namelist() if n.rsplit("/", 1)[-1] == "LICENSE"]
            license_text = bundle.read(license_names[0]) if license_names else b""
        target = TOOLS / "bin/gh.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        pending = target.with_suffix(".download")
        pending.write_bytes(binary)
        pending.replace(target)
        if license_text:
            (TOOLS / "LICENSE").write_bytes(license_text)
        return target


def _run(*args, timeout=60, check=True):
    executable = cli_path()
    if not executable:
        raise ValueError("먼저 GitHub 계정을 연결해 주세요.")
    try:
        result = subprocess.run([str(executable), *args], capture_output=True, timeout=timeout, env=environment(),
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("GitHub 응답을 받지 못했어요. 잠시 후 다시 시도해 주세요.") from None
    if check and result.returncode:
        # Do not relay CLI stderr (which can contain account/authentication details).
        error = result.stderr.decode("utf-8", "replace").lower()
        if "rate limit" in error:
            raise ValueError("GitHub 조회 한도에 도달했어요. 잠시 후 자동으로 다시 확인해요.")
        if "401" in error or "not logged" in error or "auth login" in error:
            raise ValueError("GitHub 로그인이 필요해요. 계정을 다시 연결해 주세요.")
        raise ValueError("GitHub 자료를 읽지 못했어요. 저장소 접근 권한과 조직의 연결 승인을 확인해 주세요.")
    return result


def signed_in(refresh=False):
    global _auth_cache
    with _auth_lock:
        if not refresh and time.monotonic() - _auth_cache[0] < 45:
            return _auth_cache[1]
        ready = bool(cli_path()) and _run("auth", "status", "--active", "--hostname", "github.com", check=False).returncode == 0
        _auth_cache = (time.monotonic(), ready)
        return ready


def account():
    if not signed_in(refresh=True):
        return {"connected": False, "login": ""}
    data = json.loads(_run("api", "--method", "GET", "user", "--jq", "{login: .login}").stdout)
    return {"connected": True, "login": data["login"]}


def api_pages(endpoint):
    # Only repository discovery and open PR reads are exposed by this adapter.
    if not (endpoint.startswith("user/repos?") or re.fullmatch(r"repos/[\w.-]+/[\w.-]+/pulls\?state=open&per_page=100", endpoint)):
        raise ValueError("지원하지 않는 GitHub 조회예요.")
    data = json.loads(_run("api", "--method", "GET", endpoint, "--paginate", "--slurp",
                           "-H", "Accept: application/vnd.github+json", timeout=120).stdout)
    if not isinstance(data, list) or any(not isinstance(page, list) for page in data):
        raise ValueError("GitHub 목록 응답을 읽지 못했어요.")
    return [row for page in data for row in page]


def repositories():
    user = account()
    if not user["connected"]:
        return {**user, "repositories": []}
    rows = api_pages("user/repos?affiliation=owner,collaborator,organization_member&sort=pushed&per_page=100")
    repos = []
    for row in rows:
        name = row.get("full_name", "")
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", name):
            continue
        repos.append({"id": row["id"], "name": name, "source": "https://github.com/" + name,
                      "private": bool(row.get("private")), "archived": bool(row.get("archived")),
                      "description": (row.get("description") or "")[:300], "default_branch": row.get("default_branch", ""),
                      "owner": row.get("owner", {}).get("login", name.split("/")[0])})
    return {**user, "repositories": list({r["id"]: r for r in repos}.values())}


def open_pulls(source):
    from .review_parallel import github_origin
    web = github_origin(source)
    if not web:
        raise ValueError("열린 PR 자동 확인에는 GitHub 저장소 연결이 필요해요.")
    name = web.removeprefix("https://github.com/")
    rows = api_pages(f"repos/{name}/pulls?state=open&per_page=100")
    return [{"number": r["number"], "title": r["title"], "head": r["head"]["sha"],
             "base": r["base"]["ref"], "draft": bool(r.get("draft")), "url": web + "/pull/" + str(r["number"])} for r in rows]


def git_auth_options():
    """Per-process helper; never change the user's global Git config or read tokens."""
    if not cli_path() or not signed_in():
        return []
    executable = cli_path().as_posix().replace("'", "'\\''")
    return ["-c", "credential.https://github.com.helper=", "-c", f"credential.https://github.com.helper=!'{executable}' auth git-credential"]
