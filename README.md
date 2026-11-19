# Arkhion

Arkhion은 AI와 함께 변경한 코드베이스의 구조를 파악하고, 설명과 감사 결과를 소스 위치까지 거슬러 확인하는 [Agent Skill](https://agentskills.io/specification)입니다. 제품명은 **Arkhion**, 설치 폴더와 스킬 식별자는 **`arche`**입니다.

## 스킬 사용

Codex에서 설치와 호출을 검증했습니다. Claude Code와 Cursor의 인증 후 실행은 검증 예정입니다. 설치 후 분석할 레포에서 Claude Code는 `/arche 레포 분석`, Codex는 `$arche 레포 분석`으로 호출합니다. 대화에 레포의 목적과 주요 기능, 대표 흐름 그림, 실제 사용 예, 근거 위치와 HTML 리포트 경로를 반환합니다.

[스킬 디렉터리](skills/arche) 전체를 사용하는 에이전트의 스킬 검색 경로에 설치합니다. Python 3.9 이상이 필요하며 Git은 선택입니다. 대상 레포를 연 다음 다음처럼 요청합니다.

> arche 스킬로 이 레포를 읽기 전용으로 분석해 줘. 진입점에서 핵심 모듈까지의 의존성을 근거와 함께 설명하고 HTML 리포트를 만들어 줘.

새로 설치할 때는 이 체크아웃의 루트에서 다음을 실행합니다. 기존 설치가 있으면 해당 경로를 먼저 확인하세요.

```sh
skill_home="$HOME/.agents/skills"
mkdir -p "$skill_home"
test ! -e "$skill_home/arche" && test ! -L "$skill_home/arche" && cp -R skills/arche "$skill_home/arche"
```

Codex를 새로 시작해 분석할 레포에서 `$arche 레포 분석`을 입력합니다. UI 프로젝트에서는 **Screens and features**에서 대표 화면의 표시 조건과 동작을 확인할 수 있습니다.

한 번 시험하려면 Codex 대화에 이 체크아웃의 `skills/arche/SKILL.md` 절대 경로와 분석할 레포의 경로를 함께 전달합니다. HTML을 열고 **Module map**에서 모듈을 선택하면 해당 파일을 탐색할 수 있습니다. 이미 작성된 투어 JSON 경로도 함께 전달하면 **Evidence tour**에서 단계별 파일과 줄을 열 수 있습니다.

스킬은 결과를 레포 밖의 새 디렉터리에 씁니다. HTML은 브라우저에서 오프라인으로 열 수 있습니다. 파일명을 검색하고 파일을 펼쳐 심볼, 들어오고 나가는 의존성, 외부 패키지, 근거 위치를 볼 수 있습니다. 리포트에는 분석 제외 사유, 최근 Git 커밋, 검증된 정적 import의 대표 순환 참조가 포함됩니다. 파일의 역할과 배경은 에이전트가 소스 근거를 바탕으로 대화에서 설명합니다.

## 검증과 범위

정적 분석 어댑터는 Python·TypeScript·JavaScript·Dart·Go를 지원합니다. 합성 레포 7개의 정답과 import 관계를 대조합니다. Dart의 HTTP 요청과 Worker의 OpenAPI 라우트가 발견되면 HTML의 **HTTP connections**에서 메서드·경로와 양쪽 소스 위치를 볼 수 있습니다. 유일하게 매칭된 후보도 배포 호스트와 런타임 호출은 별도 확인이 필요합니다.

```sh
python3 evals/score.py
```

스킬을 설치한 Codex에서 `$arche 레포 분석`으로 시작하세요. 이어서 “번역기가 없을 때 흐름을 보여줘”, “이 두 커밋의 변경과 영향 후보를 설명해줘”, “이 투어를 Mermaid로 내보내줘”처럼 요청할 수 있습니다. Git 변경 목록에는 정적 의존성으로 찾은 영향 후보가 표시됩니다. 감사 규칙은 근거 줄과 함께 순환·반복 import를 표시합니다.

[클라이언트별 호출과 투어 재사용](docs/installation-and-reuse.md) · [Terra on/off 실험과 재채점](evals/experiments/README.md)

[공개 후보 변경 사항](docs/release-notes.md) · [MIT 라이선스](LICENSE)
