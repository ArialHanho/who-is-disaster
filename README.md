![Who is disaster. 불여우 탐정이 코드 속 악마와 폭탄을 찾아내는 그림](assets/cover.png)

**여러 AI와 사람이 같은 저장소에서 작업할 때, 합치기 전에 사고를 찾아주는 Windows 비서입니다.**

한 AI가 함수 계약을 바꾸는 사이 다른 AI가 이전 방식으로 호출부를 만들고, 사람이 별도 PR에서 설정을 바꿀 수 있습니다. 각 변경만 보면 지나치기 쉬운 문제를 연결된 브랜치·열린 PR·로컬 작업 폴더까지 함께 놓고 찾습니다.

[Windows 설치 파일과 변경 기록](https://github.com/ArialHanho/who-is-disaster/releases/latest)

## 뭘 찾아주냐면

- **변경 누락** — 함수에 새 인자를 넣었는데 기존 호출부가 그대로인 경우
- **과한 설계** — 기존 구현으로 충분한데 중간 계층을 더 붙여 수정할 곳이 늘어난 경우
- **순환 의존성·변경 충돌** — 모듈이 서로를 물고 있거나 여러 브랜치·PR을 합칠 때 계약이 깨지는 경우
- **더미 코드** — 실제 구현이 필요한 경로에 임시 값이나 가짜 성공이 남은 경우

문제나 경고가 나오면 불여우가 **몇 건인지, 어디를 먼저 볼지** 알려줍니다. 브리핑에서 코드 근거를 보고, 고칠 항목만 체크해 AI에게 줄 수정 지시문을 가져갑니다. 고친 뒤에는 코드상 해결과 해당 커밋의 Actions 실행 결과를 확인합니다.

## 일단 써보려면

1. [Releases](https://github.com/ArialHanho/who-is-disaster/releases/latest)에서 Windows 설치 파일을 받습니다. 시작 메뉴의 **Who is disaster**를 열면 불여우가 나옵니다.
2. Git 2.38 이상과 사용할 AI의 공식 CLI를 설치합니다. **비서 열기 → 설정 → 분석 AI**에서 하나를 골라 **공식 로그인 → 로그인 연결 확인 → AI 설정 저장**을 누릅니다.
3. 불여우 우클릭 → **비서 열기 → 저장소**에서 **GitHub 연결**이나 **로컬 폴더**를 고릅니다.
4. **브랜치 보기**에서 볼 브랜치를 체크하고 **지금 분석**을 누릅니다.

| 분석 AI | 연결할 계정 |
|---|---|
| Codex | ChatGPT 계정 · [Codex CLI](https://learn.chatgpt.com/docs/cli) |
| Claude | Claude 구독 계정 · [Claude Code](https://code.claude.com/docs/en/setup) |
| Gemini | Google 계정 · [Antigravity CLI](https://antigravity.google/docs/install) |

분석에는 선택한 계정의 사용 한도가 적용됩니다. 같은 입력의 결과는 로컬에서 재사용합니다. Gemini는 추가 크레딧 사용을 끄고, Claude의 한도 초과 사용은 계정 설정을 따릅니다. [Google 설정](https://antigravity.google/docs/cli/reference/) · [Claude 사용 크레딧](https://support.claude.com/en/articles/12429409-manage-usage-credits-for-paid-claude-plans)

저장소 설정에서 열린 PR, 미커밋 변경, 자동 점검을 선택할 수 있습니다. 브리핑의 **수정됐는지 확인**은 현재 코드를 대조합니다. **GitHub 검사 결과 보기**에서는 해당 커밋의 Actions 실행과 테스트 결과를 확인합니다.

코드 검토에는 실제 영향과 기존 구현을 먼저 확인하는 [Ponytail 감사 기준](https://github.com/DietrichGebert/ponytail/tree/main/skills/ponytail-audit)이 기본으로 적용됩니다.

## 내 캐릭터 가져오기

1. 투명 배경과 같은 캔버스 크기로 대기·분석·알림 동작을 만듭니다.
2. [캐릭터 설정 예제](character.example.json)를 `character.json`으로 저장하고, 같은 폴더에 `idle.webp`, `thinking.webp`, `attention.webp`를 넣습니다. 동작마다 GIF·애니메이션 WebP 파일 또는 PNG 프레임 목록을 사용할 수 있습니다.
3. 불여우 우클릭 → **캐릭터 바꾸기 → 애니메이션 팩 가져오기**에서 `character.json`을 선택합니다.

PNG 프레임 목록은 `"idle": {"frames": ["idle-1.png", "idle-2.png"], "fps": 12}` 형식입니다. 점프(`happy`), 이동(`dragging`), 수면(`sleep`)도 같은 방식으로 추가할 수 있습니다.

## 소스 실행 및 설치 파일 빌드

Windows에서 Python 3.12와 Git을 준비하고 소스 폴더의 `start.bat`을 실행합니다.

설치 파일을 만들려면 Inno Setup을 설치한 뒤 소스 폴더에서 실행합니다. `ISCC.exe`는 실제 설치 경로를 지정하세요.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\packaging\build.ps1 -Python .\.venv\Scripts\python.exe -InnoCompiler "C:\Program Files (x86)\Inno Setup 7\ISCC.exe"
```

`dist/installer`에 앱 버전이 붙은 설치 파일이 생성됩니다. 업데이트하려면 최신 설치 파일을 실행하세요. 백업할 때는 트레이에서 앱을 종료하고 설치형의 `%LOCALAPPDATA%\CodeCompanion\.data` 또는 소스형의 `.data`를 복사합니다.

## 라이선스

독자 코드와 문서는 **PolyForm Noncommercial 1.0.0**으로 공개합니다. 상업적으로 사용하려면 **ArialHanho의 사전 서면 허락**을 받아 주세요. 사용 목적과 범위를 GitHub 이슈로 남겨 주시면 됩니다. [LICENSE](LICENSE) · [사용 허락과 고지](NOTICE.txt) · [상업적 사용 문의](https://github.com/ArialHanho/who-is-disaster/issues/new)

POPME에서 참고·적용한 부분과 Jua 폰트는 각각 MIT와 SIL OFL 1.1을 따릅니다. [배포 자산 고지](NOTICE.txt)
