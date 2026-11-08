"""Use the installed Codex login for one source-bounded model response.

The CLI runs in an empty temporary directory with execution, browser, apps,
plugins, hooks, and image tools disabled. App data is supplied on stdin.
Credentials stay with the official CLI; the app does not open auth files.
"""
from functools import lru_cache
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time


DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "unified_exec_tty", "code_mode_host", "apps", "plugins",
    "multi_agent", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "computer_use", "in_app_browser", "image_generation", "view_image", "hooks",
    "skill_search", "tool_suggest", "goals", "sleep_tool",
)
REQUIRED_DISABLED = {"shell_tool", "unified_exec", "code_mode_host", "apps", "plugins"}
_auth_cache = {}


def _run(command, **kwargs):
    return subprocess.run(command, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                          **kwargs)


def find_codex():
    explicit = os.getenv("CODEX_EXECUTABLE")
    if explicit:
        return str(Path(explicit)) if Path(explicit).is_file() else None
    found = shutil.which("codex")
    if found:
        return found
    local = os.getenv("LOCALAPPDATA")
    if local:
        candidates = list((Path(local) / "OpenAI/Codex/bin").glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return None


def status(refresh=False):
    executable = find_codex()
    if not executable:
        return {"ready": False, "label": "Codex 설치 필요", "message": "Codex CLI를 설치하고 codex login으로 로그인해 주세요."}
    cached = _auth_cache.get(executable)
    if not refresh and cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    try:
        result = _run([executable, "login", "status"], timeout=8)
        output = (result.stdout + result.stderr).lower()
        ready = result.returncode == 0 and "logged in" in output
        label = "Codex · ChatGPT 로그인" if ready and "chatgpt" in output else ("Codex 로그인됨" if ready else "Codex 로그인 필요")
        info = {"ready": ready, "label": label,
                "message": "현재 PC의 Codex 로그인을 사용해요." if ready else "터미널에서 codex login으로 로그인한 뒤 다시 확인해 주세요."}
    except (OSError, subprocess.SubprocessError):
        info = {"ready": False, "label": "Codex 확인 필요", "message": "Codex 로그인 상태를 확인하지 못했어요."}
    _auth_cache[executable] = (time.monotonic(), info)
    return info


@lru_cache(maxsize=4)
def disabled_features(executable):
    result = _run([executable, "features", "list"], timeout=8)
    names = {line.split()[0] for line in result.stdout.splitlines() if line.split()}
    if result.returncode or not REQUIRED_DISABLED.issubset(names):
        raise ValueError("설치된 Codex 버전이 이 앱의 제한된 AI 연결을 지원하지 않아요. Codex를 업데이트해 주세요.")
    return [name for name in DISABLED_FEATURES if name in names]


def generate(context, *, instructions, output_schema, timeout=100):
    executable = find_codex()
    if not executable:
        raise ValueError("Codex CLI를 찾지 못했어요. 설치 후 codex login으로 로그인해 주세요.")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="disaster-review-") as temporary:
        root = Path(temporary)
        work = root / "empty"
        work.mkdir()
        schema, output = root / "reply-schema.json", root / "reply.json"
        schema.write_text(json.dumps(output_schema, ensure_ascii=False), encoding="utf-8")
        command = [executable, "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                   "--sandbox", "read-only", "-C", str(work), "--output-schema", str(schema),
                   "--output-last-message", str(output), "--json", "--color", "never",
                   "-c", 'web_search="disabled"']
        for feature in disabled_features(executable):
            command.extend(["--disable", feature])
        if os.getenv("CODEX_MODEL"):
            command.extend(["--model", os.environ["CODEX_MODEL"]])
        command.append("-")
        prompt = instructions + "\n\n<project_data>\n" + json.dumps(context, ensure_ascii=False) + "\n</project_data>"
        child_env = {key: value for key, value in os.environ.items()
                     if key not in {"OPENAI_API_KEY", "CODEX_API_KEY"}}
        try:
            response = _run(command, input=prompt, cwd=work, timeout=timeout, env=child_env)
        except subprocess.TimeoutExpired:
            raise ValueError("Codex 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.") from None
        except OSError:
            raise ValueError("Codex를 실행하지 못했어요. 설치 경로와 로그인 상태를 확인해 주세요.") from None
        if response.returncode or not output.exists():
            diagnostics = (response.stderr + response.stdout).lower()
            if any(word in diagnostics for word in ("usage limit", "rate limit", "quota")):
                raise ValueError("Codex 사용 한도에 도달했어요. 한도가 회복되면 다시 시도해 주세요.")
            if "auth" in diagnostics or "login" in diagnostics:
                raise ValueError("Codex 로그인을 다시 확인해 주세요. 터미널에서 codex login을 실행할 수 있어요.")
            raise ValueError("Codex가 답변을 완성하지 못했어요. 잠시 후 다시 질문해 주세요.")
        usage, tool_events = {}, []
        for line in response.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item", {})
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
            if item.get("type") in {"command_execution", "mcp_tool_call", "web_search", "file_change"}:
                tool_events.append(item["type"])
        if tool_events:
            raise ValueError("제한된 AI 연결에서 예기치 않은 도구 호출이 감지돼 답변을 사용하지 않았어요.")
        try:
            reply = json.loads(output.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            raise ValueError("Codex 답변 형식을 읽지 못했어요. 다시 질문해 주세요.") from None
    return {"result": reply, "usage": usage, "elapsed_ms": round((time.monotonic() - started) * 1000)}
