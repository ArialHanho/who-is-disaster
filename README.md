![Who is disaster. 불여우 탐정이 코드 속 악마와 폭탄을 찾아내는 그림](docs/images/disaster-cover.png)

**코드 속에서 사고 칠 녀석을 찾아주는 Windows 비서.**

여러 AI가 각자 브랜치에서 빠르게 개발하다 보면, 한쪽에서 바꾼 걸 다른 쪽에서 놓치기 쉽습니다. 기능 하나에 구조가 너무 커지기도 하고요. 그걸 합치기 전에 보고 싶어서 만들었습니다.

[Windows 설치 파일](https://github.com/ArialHanho/who-is-disaster/releases/latest) · [사용설명서](docs/usage.md) · [변경 기록](CHANGELOG.md)

## 뭘 찾아주냐면

- **변경 누락** — 함수에 새 인자를 넣었는데 기존 호출부가 그대로인 경우
- **과한 설계** — 기존 구현으로 충분한데 중간 계층을 더 붙여 수정할 곳이 늘어난 경우
- **순환 의존성·변경 충돌** — 모듈이 서로를 물고 있거나 여러 브랜치·PR을 합칠 때 계약이 깨지는 경우
- **더미 코드** — 실제 구현이 필요한 경로에 임시 값이나 가짜 성공이 남은 경우

문제나 경고가 나오면 불여우가 **몇 건인지, 어디를 먼저 볼지** 알려줍니다. 브리핑을 열면 코드 근거와 AI에게 줄 수정 지시문을 볼 수 있습니다. 고친 뒤에는 코드상 해결과 해당 커밋의 Actions 실행 결과를 각각 확인합니다.

## 일단 써보려면

1. [Releases](https://github.com/ArialHanho/who-is-disaster/releases/latest)에서 Windows 설치 파일을 받습니다. 시작 메뉴의 **Who is disaster**를 열면 불여우가 나옵니다.
2. Git 2.38 이상과 Codex CLI를 준비하고 `codex login`으로 로그인합니다. 기존 ChatGPT 로그인과 사용량 한도를 사용합니다.
3. 불여우 우클릭 → **비서 열기 → 저장소**에서 **GitHub 연결**이나 **로컬 폴더**를 고릅니다.
4. **브랜치 보기**에서 볼 브랜치를 체크하고 **지금 분석**을 누릅니다.

연결한 브랜치, 열린 PR, 디스크에 저장한 미커밋 변경을 함께 볼 수 있습니다. 소스·설정·문서는 여러 묶음으로 나눠 검토하고, 연속 푸시는 모아서 확인합니다. 읽은 범위와 분석이 미완료된 이유는 브리핑에서 확인할 수 있습니다. [분석 구조](docs/architecture.md)

## 내 비서로 바꾸기

기본은 **불여우 탐정**입니다. 우클릭 → **캐릭터 바꾸기**에서 다른 기본 캐릭터를 고르거나, 직접 만든 PNG·WebP·GIF 팩을 가져오면 됩니다. [캐릭터 팩 만드는 법](docs/characters.md)

## 소스에서 실행하기

Windows와 Python 3.11 이상에서 아래 명령을 실행합니다. 개발과 패키징은 Python 3.12에서 확인합니다.

```powershell
git clone https://github.com/ArialHanho/who-is-disaster.git
cd who-is-disaster
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m agent_app
```

`start.bat`으로 전용 환경을 준비해 콘솔 없이 실행할 수도 있습니다. [관리·문제 해결](docs/maintenance.md) · [Windows 빌드·릴리스](docs/releasing.md)

## 라이선스

독자 코드와 문서는 **PolyForm Noncommercial 1.0.0**으로 공개합니다. 상업적으로 사용하려면 **ArialHanho의 사전 서면 허락**을 받아 주세요. 사용 목적과 범위를 GitHub 이슈로 남겨 주시면 됩니다. [LICENSE](LICENSE) · [사용 허락과 고지](NOTICE.txt) · [상업적 사용 문의](https://github.com/ArialHanho/who-is-disaster/issues/new)

POPME에서 참고·적용한 부분과 Jua 폰트는 각각 MIT와 SIL OFL 1.1을 따릅니다. [출처와 배포 자산](PROVENANCE.md)
