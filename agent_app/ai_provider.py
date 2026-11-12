"""Route each review to one locally signed-in subscription CLI."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import Lock
import time

from . import codex_provider
from .codex_provider import ReviewTimeout, _run, review_prompt


PROVIDERS = {"codex": "Codex", "claude": "Claude", "gemini": "Gemini"}
COMMANDS = {"claude": "claude", "gemini": "agy"}
LOGIN_ARGS = {"codex": ["login"], "claude": ["auth", "login", "--claudeai"], "gemini": []}
INSTALL_URLS = {
    "codex": "https://learn.chatgpt.com/docs/cli",
    "claude": "https://code.claude.com/docs/en/setup",
    "gemini": "https://antigravity.google/docs/install",
}
GEMINI_MODEL = "Gemini 3.5 Flash"
GEMINI_SETTINGS = {
    "useG1Credits": False, "modelProvider": "", "historySize": 0,
    "toolPermission": "strict", "allowNonWorkspaceAccess": False,
    "permissions": {"allow": [], "ask": [], "deny": [
        f"{action}(*)" for action in ("command", "read_file", "write_file", "read_url", "execute_url", "mcp", "unsandboxed")
    ]},
}
ALTERNATE_AUTH = {
    "claude": {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
               "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR"},
    "gemini": {"GOOGLE_API_KEY", "GEMINI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_GEMINI_BASE_URL",
               "GOOGLE_GENAI_USE_VERTEXAI", "AGY_ADC_AUTH"},
}
_status_cache = {}
_status_lock = Lock()


def selection():
    provider = os.getenv("DISASTER_AI_PROVIDER", "codex")
    if provider not in PROVIDERS:
        provider = "codex"
    return provider, os.getenv("DISASTER_AI_MODEL", os.getenv("CODEX_MODEL", "") if provider == "codex" else "")


def select(provider, model):
    if provider not in PROVIDERS:
        raise ValueError("지원하는 AI를 선택해 주세요.")
    model = model.strip()[:100]
    if model.startswith("-") or (provider == "gemini" and model and not model.lower().startswith("gemini")):
        raise ValueError("Gemini 연결에는 Gemini 모델을 입력해 주세요." if provider == "gemini" else "모델 이름을 확인해 주세요.")
    os.environ.update(DISASTER_AI_PROVIDER=provider, DISASTER_AI_MODEL=model)


def model_identity():
    provider, model = selection()
    return model if provider == "codex" else [provider, model or (GEMINI_MODEL if provider == "gemini" else "")]


def find_cli(provider):
    if provider == "codex":
        return codex_provider.find_codex()
    cli = COMMANDS[provider]
    if os.name != "nt":
        return shutil.which(cli)
    local = os.getenv("LOCALAPPDATA")
    native = (Path.home() / ".local/bin/claude.exe" if provider == "claude"
              else Path(local) / "agy/bin/agy.exe" if local else None)
    return shutil.which(cli + ".exe") or (str(native) if native and native.is_file() else None)


def child_environment(provider):
    if provider == "codex":
        return codex_provider.child_environment()
    return {key: value for key, value in os.environ.items()
            if key not in ALTERNATE_AUTH[provider] and not key.startswith("CLAUDE_CODE_USE_")}


def start_login(provider, folder):
    executable = find_cli(provider)
    if not executable:
        raise ValueError(f"{PROVIDERS[provider]} 공식 CLI를 먼저 설치해 주세요.")
    folder.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen([executable, *LOGIN_ARGS[provider]], cwd=folder, env=child_environment(provider), creationflags=flags)


def check_gemini_configuration(executable, cwd, env):
    # CLI permissions apply to model tools. Global hooks and MCP servers can start independently.
    hooks = Path.home() / ".gemini/config/hooks.json"
    if hooks.is_file() and json.loads(hooks.read_text(encoding="utf-8-sig")).get("hooks"):
        raise ValueError("Antigravity의 전역 훅을 끈 뒤 다시 연결해 주세요.")
    for args, empty in ((["plugin", "list", "--json"], "[]"), (["mcp", "list"], "No MCP servers configured.")):
        result = _run([executable, *args], cwd=cwd, env=env, timeout=8)
        if result.returncode or result.stdout.strip() != empty:
            raise ValueError("Antigravity의 플러그인과 MCP 연결을 비운 뒤 다시 확인해 주세요.")


def status(refresh=False, *, provider=None):
    provider = provider or selection()[0]
    with _status_lock:
        return login_status(provider, refresh)


def login_status(provider, refresh):
    if provider == "codex":
        return codex_provider.status(refresh=True) if refresh else codex_provider.status()
    name = PROVIDERS[provider]
    executable = find_cli(provider)
    if not executable:
        return {"ready": False, "label": f"{name} 설치 필요", "message": f"공식 {COMMANDS[provider]} CLI를 설치해 주세요."}
    key = (provider, executable)
    cached = _status_cache.get(key)
    if not refresh and cached and time.monotonic() - cached[0] < 30:
        return cached[1]
    info = {"ready": False, "label": f"{name} 로그인 필요", "message": "공식 로그인 후 연결을 다시 확인해 주세요."}
    try:
        env = child_environment(provider)
        with tempfile.TemporaryDirectory(prefix="disaster-auth-") as temporary:
            if provider == "claude":
                probe = _run([executable, "auth", "status", "--json"], cwd=temporary, env=env, timeout=10)
                auth = json.loads(probe.stdout) if probe.returncode == 0 else {}
                ready = auth.get("loggedIn") is True and auth.get("authMethod") == "claude.ai" and auth.get("apiProvider") == "firstParty"
                account = "Claude 구독"
            else:
                # Listing models requires Google sign-in and makes no inference request.
                probe = _run([executable, "models"], cwd=temporary, env=env, timeout=12)
                ready = probe.returncode == 0 and "gemini" in probe.stdout.lower()
                if ready:
                    check_gemini_configuration(executable, temporary, env)
                account = "Google Antigravity"
        if ready:
            info = {"ready": True, "label": f"{name} 연결됨", "message": f"현재 PC의 {account} 로그인을 사용해요."}
    except ValueError as exc:
        info = {"ready": False, "label": f"{name} 연결 확인", "message": str(exc) if type(exc) is ValueError else "공식 CLI를 업데이트한 뒤 다시 확인해 주세요."}
    except (OSError, subprocess.SubprocessError, AttributeError):
        info = {"ready": False, "label": f"{name} 확인 필요", "message": "공식 CLI를 업데이트하고 로그인 상태를 확인해 주세요."}
    _status_cache[key] = (time.monotonic(), info)
    return info


def gemini_result(stdout):
    reply = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("event") == "step_update" and event.get("step_update", {}).get("step_type") == "tool":
            raise ValueError("Gemini가 도구를 호출해 이 답변을 사용하지 않았어요.")
        if event.get("event") == "result":
            reply = event["result"]
    return reply


def generate(context, *, instructions, output_schema, timeout=100):
    provider, model = selection()
    ready = status(provider=provider)
    if not ready["ready"]:
        raise ValueError(ready["message"])
    if provider == "codex":
        return codex_provider.generate(context, instructions=instructions, output_schema=output_schema, timeout=timeout)
    name = PROVIDERS[provider]
    executable = find_cli(provider)
    if not executable:
        raise ValueError(f"{name} CLI를 찾지 못했어요.")
    started = time.monotonic()
    prompt = review_prompt(context, instructions)
    schema = json.dumps(output_schema, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="disaster-review-") as temporary:
        root = Path(temporary)
        work = root / "empty"
        work.mkdir()
        if provider == "claude":
            args = ["-p", "--restricted", "--safe-mode", "--permission-prompts", "none", "--tools", "",
                    "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                    "--output-format", "json", "--json-schema", schema]
        else:
            settings, schema_path = root / "settings.json", root / "schema.json"
            settings.write_text(json.dumps(GEMINI_SETTINGS), encoding="utf-8")
            schema_path.write_text(schema, encoding="utf-8")
            args = ["--settings", str(settings), "--input-format", "stream-json", "--output-format", "stream-json",
                    "--disable-slash-commands", "--json-schema", str(schema_path), "--log-file", str(root / "cli.log")]
            model = model or GEMINI_MODEL
            prompt = json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"
        if model:
            args.extend(["--model", model])
        try:
            response = _run([executable, *args], cwd=work, input=prompt, env=child_environment(provider), timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ReviewTimeout(f"{name} 응답 시간이 초과됐어요. 잠시 후 다시 시도해 주세요.") from None
        except OSError:
            raise ValueError(f"{name} CLI를 실행하지 못했어요.") from None
    try:
        envelope = json.loads(response.stdout) if provider == "claude" else gemini_result(response.stdout)
        reply = envelope.get("structured_output")
        usage = envelope.get("usage", {})
        failed = envelope.get("is_error", False) if provider == "claude" else envelope.get("status") != "SUCCESS"
    except (json.JSONDecodeError, AttributeError, KeyError):
        failed, reply, usage = True, None, {}
    if response.returncode or failed or not isinstance(reply, dict) or not isinstance(usage, dict):
        output = (response.stderr + response.stdout).lower()
        if any(word in output for word in ("rate limit", "usage limit", "quota", "resource_exhausted")):
            raise ValueError(f"{name} 사용 한도에 도달했어요. 한도가 회복되면 다시 시도해 주세요.")
        raise ValueError(f"{name} 응답을 마치지 못했어요. 로그인, 모델 이름과 CLI 버전을 확인해 주세요.")
    return {"result": reply, "usage": usage, "elapsed_ms": round((time.monotonic() - started) * 1000)}
