# 읽기 전용 설치와 투어 재사용

Arche은 Python 3.9 이상을 사용한다. 분석 대상은 읽기 전용으로 열고,
산출물은 대상 레포와 스킬 설치 디렉터리 밖의 새 개인 디렉터리에 둔다.
대상에 있는 명령을 실행하거나 의존성을 설치하지 않는다. 아래 설치는 신뢰한
Arche 체크아웃의 `skills/arche` 전체를 복사한다. 기존 설치가 있으면 덮어쓰기
전에 내용을 확인하고 별도로 보관한다. 스킬 설치 위치는 클라이언트 설정에
따라 달라질 수 있으며, 아래는 사용자 단위 위치의 예다.

```sh
# Arche 체크아웃의 절대 경로로 설정한다.
arche_checkout=/absolute/path/to/Arche
# 아래 중 사용할 클라이언트 하나의 사용자 스킬 디렉터리를 선택한다.
skill_home="$HOME/.agents/skills"       # Codex
# skill_home="$HOME/.claude/skills"    # Claude Code
# skill_home="$HOME/.cursor/skills"    # Cursor
mkdir -p "$skill_home"
# 이미 arche가 있으면 중단한다.
test ! -e "$skill_home/arche" && test ! -L "$skill_home/arche" && cp -R "$arche_checkout/skills/arche" "$skill_home/arche"
```

클라이언트를 새로 시작하거나 스킬 목록을 새로 고친 후, 분석 대상 폴더를
열고 다음과 같이 명시적으로 요청한다. 접근 권한은 대상 읽기와 대상 밖
산출물 쓰기로 제한한다. 클라이언트의 권한 설정은 별도로 확인한다.

| 클라이언트 | 호출 예 |
|---|---|
| Codex | `$arche 레포 분석` |
| Claude Code | `/arche 레포 분석` |
| Cursor | `arche 스킬을 사용해 이 레포를 읽기 전용으로 분석해 줘` |

스킬이 반환한 절대 `report.html` 경로를 로컬 브라우저에서 열어 Module map과
Evidence tour를 확인한다. 설치 경로, 활성화 방식 및 결과 열기는 각 클라이언트
버전에서 실제로 검증해야 한다. 명시적 파일 경로를 지정하는 일회성 호출도
가능하다. 설치만으로 대상이 쓰기 방지되는 것은 아니므로 클라이언트의 도구
권한도 제한한다.

## 수동 실행과 기존 투어 소비

다음은 클라이언트와 독립적인 실행 예다. `repo`와 `tour`는 절대 경로로
지정하고, 투어는 해당 레포와 정확히 일치하는 것을 사용한다. 아래 예는
Arche 자체를 대상으로 한다. 세 명령은 같은 출력 폴더를 사용한다.

```sh
repo=/absolute/path/to/Arche
scripts="$skill_home/arche/scripts"
tour="$repo/evals/tours/arche-report.json"
out="$(mktemp -d "${TMPDIR:-/tmp}/arche-run.XXXXXX")"
python3 -B "$scripts/scan.py" "$repo" --out "$out"
python3 -B "$scripts/imports.py" --out "$out"
python3 -B "$scripts/render.py" --out "$out" --tour "$tour"
printf 'Report: %s/report.html\n' "$out"
```

[두 번째 소비 스킬의 최소 예](examples/tour-consumer/SKILL.md)는 같은 `out`과
`tour`를 사용해 다시 스캔하지 않고 기존 `inventory.json`, 투어 및 현재
소스를 대조한다. 예제의 셸 명령을 실행할 때 `scripts`, `out`, `tour`를 그대로
전달한다. 각 단계의 ID·경로·포함 줄 범위·SHA-256과 revision을 출력하며,
파일이 바뀌거나 범위가 유효하지 않으면 실패한다. dirty 작업 트리는 commit만으로
완전히 식별되지 않으므로 각 파일 해시도 보존한다. 투어 관계의 의미는 별도
소스 검토가 필요하다. 이를 다른 스킬에 설치하려면 예제 디렉터리를 원하는
클라이언트의 스킬 디렉터리에 `tour-consumer` 이름으로 복사하고 위 세 절대
경로를 요청에 포함한다.

## 클라이언트 실행 기록

| 클라이언트 | 확인한 범위 |
|---|---|
| Codex | 새 세션에서 사용자 스킬을 한 줄로 호출해 합성 UI·CLI의 오프라인 HTML을 열었다. 빈 사용자 설치 경로에 공개 후보 ZIP을 복사해 scan·imports·render를 검증했다. 기존 전역 스킬 경로를 비활성화한 별도 Codex 세션에서도 프로젝트 로컬 설치본의 SKILL.md와 scripts를 읽고 HTML을 생성한 실행 기록을 확인했다. |
| Claude Code | 2.1.207에서 합성 레포의 프로젝트 로컬 스킬을 `/arche 레포 분석`으로 호출해 HTML을 생성했다. 설치된 스킬 폴더는 분석에서 제외됐고, Python 소스 2개가 분석됐다. |
| Cursor Agent | 2026.11.19-f005e9d에서 로그인과 프로젝트 스킬 읽기를 확인했다. 보고서 생성 검증을 진행 중이다. |

JSON·Markdown·Mermaid 내보내기와 소비 스킬의 무재스캔 근거 대조는 [재사용 예시](examples/tour-consumer/SKILL.md)에 있다.
