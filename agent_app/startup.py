"""Opt-in per-user Windows logon startup, shared with the installer."""
import os
from pathlib import Path
import subprocess
import sys

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'CodeCompanion'


def command():
    if getattr(sys, 'frozen', False):
        args = [sys.executable, '--startup']
    else:
        python = Path(sys.executable).with_name('pythonw.exe')
        args = [str(python), str(Path(__file__).resolve().parents[1]/'launch.py'), '--startup']
    if not Path(args[0]).is_file():
        raise OSError('자동 실행할 비서 파일을 찾지 못했어요.')
    return subprocess.list2cmdline(args)


def is_enabled():
    if os.name != 'nt': return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return bool(winreg.QueryValueEx(key, VALUE_NAME)[0])
    except FileNotFoundError:
        return False


def set_enabled(enabled):
    if os.name != 'nt': raise OSError('Windows에서 사용할 수 있어요.')
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try: winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError: pass
