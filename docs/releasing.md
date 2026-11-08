# Windows 빌드와 릴리스

## 준비

Python 3.12, Git, Inno Setup 7.1.1을 설치합니다. 다음 명령은 저장소 루트에서 실행합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

## 빌드

```powershell
.\packaging\build.ps1 -Python .\.venv\Scripts\python.exe -InnoCompiler "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
```

Inno Setup 경로는 실제 설치 위치로 바꾸세요. 스크립트는 `agent_app.__version__`을 읽어 설치 파일 버전을 맞춥니다. PyInstaller는 Qt 관련 DLL만 정상 수집하도록 빌드 PATH를 제한합니다.

- 실행 폴더: `dist/CodeCompanion`
- 설치 파일: `dist/installer/WhoIsDisaster-Setup-0.3.2.exe`
- 내부 실행 파일·AppId·데이터 경로는 이전 설치형과의 업그레이드 호환을 위해 유지합니다.

## 배포

1. `agent_app/__init__.py`의 버전과 `CHANGELOG.md`, README의 버전을 함께 갱신합니다.
2. 관련 검사를 통과시키고 기본 캐릭터로 실행·말풍선·설정을 확인합니다.
3. `.data`, 개인 팩, 사진, 로그, 가상환경, 캐시가 Git 추적에 없는지 확인합니다.
4. 태그를 만들고 GitHub Release에 설치 파일을 첨부합니다. 설치 파일 자체는 Git 커밋에 넣지 않습니다.
5. 기존 설정·기록을 유지하는 업그레이드인지 확인합니다.

```powershell
git tag -a v0.3.2 -m "Who is disaster 0.3.2"
git push origin main --tags
gh release create v0.3.2 dist/installer/WhoIsDisaster-Setup-0.3.2.exe --title "Who is disaster 0.3.2" --notes-file CHANGELOG.md
```

공개 소스에는 직접 작성한 코드·문서·검사와 재배포 가능한 자산만 포함합니다. 개인 데이터로 만든 스크린샷이나 캐릭터 팩을 릴리스에 첨부하지 마세요.
