![Who is disaster. 불여우 탐정이 코드 속 악마와 폭탄을 찾아내는 그림](docs/images/disaster-cover.png)

**여러 AI와 사람이 같은 저장소에서 작업할 때, 합치기 전에 사고를 찾아주는 Windows 비서입니다.**

한 AI가 함수 계약을 바꾸는 사이 다른 AI가 이전 방식으로 호출부를 만들고, 사람이 별도 PR에서 설정을 바꿀 수 있습니다. 각 변경만 보면 지나치기 쉬운 문제를 연결된 브랜치·열린 PR·로컬 작업 폴더까지 함께 놓고 찾습니다.

[Windows 설치 파일](https://github.com/ArialHanho/who-is-disaster/releases/latest) · [사용설명서](docs/usage.md) · [변경 기록](CHANGELOG.md)

## 뭘 찾아주냐면

- **변경 누락** — 함수에 새 인자를 넣었는데 기존 호출부가 그대로인 경우
- **과한 설계** — 기존 구현으로 충분한데 중간 계층을 더 붙여 수정할 곳이 늘어난 경우
- **순환 의존성·변경 충돌** — 모듈이 서로를 물고 있거나 여러 브랜치·PR을 합칠 때 계약이 깨지는 경우
- **더미 코드** — 실제 구현이 필요한 경로에 임시 값이나 가짜 성공이 남은 경우

문제나 경고가 나오면 불여우가 **몇 건인지, 어디를 먼저 볼지** 알려줍니다. 브리핑에서 코드 근거를 보고, 고칠 항목만 체크해 AI에게 줄 수정 지시문을 가져갑니다. 고친 뒤에는 코드상 해결과 해당 커밋의 Actions 실행 결과를 확인합니다.

## 일단 써보려면

1. [Releases](https://github.com/ArialHanho/who-is-disaster/releases/latest)에서 Windows 설치 파일을 받습니다. 시작 메뉴의 **Who is disaster**를 열면 불여우가 나옵니다.
2. Git 2.38 이상과 사용할 AI의 공식 CLI를 설치합니다. 비서의 **설정 → 분석 AI**에서 **Codex·Claude·Gemini 중 하나**를 고르고 공식 로그인과 설정 저장을 합니다. [계정 연결 방법](docs/usage.md#분석-ai-연결)
3. 불여우 우클릭 → **비서 열기 → 저장소**에서 **GitHub 연결**이나 **로컬 폴더**를 고릅니다.
4. **브랜치 보기**에서 볼 브랜치를 체크하고 **지금 분석**을 누릅니다.

연결한 브랜치, 열린 PR, 디스크에 저장한 미커밋 변경을 함께 볼 수 있습니다. 소스·설정·문서는 여러 묶음으로 나눠 검토하고, 연속 푸시는 모아서 확인합니다. 읽은 범위와 분석이 미완료된 이유는 브리핑에서 확인할 수 있습니다. [분석 구조](docs/architecture.md)

검토에는 기존 구현으로 충분한지와 과한 설계가 실제로 어떤 비용을 만드는지 확인하는 Ponytail 기준이 기본으로 적용됩니다. 같은 AI에 같은 검토 자료를 다시 보내는 대신 로컬 결과를 재사용합니다. [검토 기준과 출처](PROVENANCE.md)

## 내 비서로 바꾸기

기본 캐릭터는 **불여우 탐정**입니다. 우클릭 → **캐릭터 바꾸기**에서 직접 만든 PNG·WebP·GIF 팩을 가져올 수 있습니다. [캐릭터 팩 만드는 법](docs/characters.md)

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
