"""Create an isolated desktop environment, then launch without a console."""
from pathlib import Path
import subprocess
import sys
import venv


def main():
    root = Path(__file__).resolve().parent
    configured = root / ".local-python"
    if configured.exists():
        python = Path(configured.read_text(encoding="utf-8").strip())
    else:
        env = root / ".venv"
        python = env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        if not python.exists():
            venv.EnvBuilder(with_pip=True).create(env)
        if subprocess.run([str(python), "-c", "import PySide6"], capture_output=True).returncode:
            subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "requirements.txt")], check=True)
    if not python.is_file():
        raise RuntimeError("개발 Python 경로를 찾지 못했습니다. .local-python 설정을 지우고 다시 실행하세요.")
    executable = python.with_name("pythonw.exe") if sys.platform == "win32" else python
    subprocess.Popen([str(executable), "-m", "agent_app", *sys.argv[1:]], cwd=root)


if __name__ == "__main__":
    main()
