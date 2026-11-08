"""Writable user state stays separate from frozen application resources."""
import os
from pathlib import Path
import sys
import shutil
import subprocess

_git_cached = None


def app_root():
    override = os.getenv('CODE_COMPANION_HOME')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        local = os.getenv('LOCALAPPDATA')
        return (Path(local) if local else Path.home()/'AppData/Local')/'CodeCompanion'
    return Path(__file__).resolve().parents[1]


def find_git():
    """Logon processes do not inherit Codex's temporary development PATH."""
    global _git_cached
    found = shutil.which('git')
    if found: return found
    if _git_cached and Path(_git_cached).is_file(): return _git_cached
    if os.name != 'nt': return None
    local = os.getenv('LOCALAPPDATA')
    locations = [Path(os.getenv('ProgramFiles', 'C:/Program Files'))/'Git/cmd/git.exe',
                 (Path(local) if local else Path.home()/'AppData/Local')/'Programs/Git/cmd/git.exe']
    found = next((str(p) for p in locations if p.is_file()), None)
    if not found:
        powershell = Path(os.getenv('SystemRoot', 'C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            run = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command',
                "(Get-AppxPackage -Name 'OpenAI.CodexPrimaryRuntime*' | Sort-Object Version -Descending).InstallLocation"],
                capture_output=True, text=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            found = next((str(Path(line.strip())/'dependencies/native/git/cmd/git.exe') for line in run.stdout.splitlines()
                          if line.strip() and (Path(line.strip())/'dependencies/native/git/cmd/git.exe').is_file()), None)
        except (OSError, subprocess.SubprocessError):
            pass
    if found: _git_cached = found
    return found
