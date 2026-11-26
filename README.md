# Arche

Arche은 AI와 함께 변경한 코드베이스의 구조를 파악하고, 설명과 감사 결과를 소스 위치까지 거슬러 확인하는 [Agent Skill](https://agentskills.io/specification)입니다. 스킬 식별자는 **`arche`**입니다.

[공개 로드맵](docs/ROADMAP.md) · [에이전트 평가](docs/evaluation.md) · [릴리스](https://github.com/KooEric/arche/releases)

Arche helps developers understand codebases through source-backed walkthroughs, offline reports, change audits, and reproducible synthetic agent evaluations.

## 스킬 설치와 실행

1. Python 3.9 이상을 준비하고 [GitHub Releases](https://github.com/KooEric/arche/releases)에서 선택한 버전의 분석 스킬 ZIP을 내려받아 압축을 풉니다.
2. `skills/arche/SKILL.md`가 있는 폴더에서 사용하는 클라이언트에 맞게 설치합니다. 다음 명령은 Codex의 사용자 설치 예시입니다. 기존 설치가 있으면 위치를 출력합니다.

```sh
skill_home="$HOME/.agents/skills"
if [ -e "$skill_home/arche" ] || [ -L "$skill_home/arche" ]; then
  printf '기존 설치 경로: %s/arche\n' "$skill_home"
else
  mkdir -p "$skill_home" && cp -R skills/arche "$skill_home/arche"
fi
```

3. 분석할 레포에서 새 세션을 열고 아래처럼 호출합니다. 답변에 있는 로컬 HTML을 열면 대표 기능과 소스 근거를 볼 수 있습니다.

| 클라이언트 | 사용자 설치 위치 | 호출 예 |
|---|---|---|
| Codex | `~/.agents/skills/arche` | `$arche 레포 분석` |
| Claude Code | `~/.claude/skills/arche` | `/arche 레포 분석` |
| Cursor Agent | `~/.cursor/skills/arche` | `arche 스킬로 레포 분석해 줘` |

[프로젝트별 설치, 수동 실행 및 실제 검증 기록](docs/installation-and-reuse.md)

## 어떤 결과가 나오나요?

**화면에서 코드로.** 합성 수령 확인 앱에서 ‘수령 처리’를 누르면 상태가 ‘수령 완료’로 바뀌고 ‘처리 취소’가 나타납니다. 리포트의 **화면과 기능**에서 이 전환을 설명하는 `app.js` 줄을 찾을 수 있습니다.

![수령 확인 화면의 상태와 버튼을 app.js의 근거 줄에 연결한 리포트](docs/examples/first-run/ui.png)

[UI 소스](evals/walkthrough-fixtures/ui) · [오프라인 UI 리포트](docs/examples/first-run/ui.html) · [리포트에 쓴 투어](docs/examples/first-run/ui-tour.json)

**명령에서 출력으로.** 합성 CLI에서 `python3 count_tags.py red red blue`의 예상 출력은 순서대로 `blue 1`, `red 2`입니다. **파일 의존성**에서 `count_tags.py`를 펼치면 집계를 담당하는 `tally.py`로 이어지는 import를 확인할 수 있습니다.

![count_tags.py와 tally.py의 핵심 파일 순위와 의존성 맵을 보여 주는 리포트](docs/examples/first-run/cli.png)

[CLI 소스](evals/walkthrough-fixtures/cli) · [오프라인 CLI 리포트](docs/examples/first-run/cli.html)

GitHub에서 HTML 예제를 보려면 파일 페이지에서 **Download raw file**을 선택하고 내려받은 파일을 브라우저로 여세요. 직접 설치를 시험해 볼 수 있는 [첫 사용자 과제](docs/first-use.md)도 있습니다.

스킬은 결과를 레포 밖의 새 디렉터리에 씁니다. HTML은 브라우저에서 오프라인으로 열 수 있고 라이트·다크 테마와 한국어·영어 화면을 지원합니다. 리포트는 다음 순서로 읽습니다.

- **개요와 요약**: 파일·줄·심볼·의존성 수, 언어 비율, 에이전트가 소스를 읽고 쓴 요약을 봅니다.
- **핵심 파일**: 진입점 후보, 가장 많이 의존되는 파일, 가장 큰 파일에서 읽기 시작할 곳을 고릅니다.
- **의존성 맵**: 폴더를 눌러 펼치며 import 방향을 따라갑니다. 서로 순환하는 파일은 같은 열에 놓입니다.
- **화면과 기능, 근거 투어**: 버튼과 단계마다 설명과 소스 줄 범위를 확인합니다.
- **파일 의존성**: 파일명을 검색하고 파일을 펼쳐 심볼, 들어오고 나가는 의존성, 외부 패키지, 근거 위치를 봅니다.

같은 폴더에 **지도**(`map.html`)도 함께 만들어집니다. 시스템을 부품 카드와 연결선으로 보여 주고, 카드를 열면 하위 부품과 파일까지 들어갑니다. 에이전트가 `map.json`에 부품 이름과 설명, 흐름을 적으면 그 내용이 지도에 실리고, 적지 않으면 폴더 기준으로 자동으로 그립니다. 실선은 스캔이 해석한 import, 점선은 추론, 파란 선은 작성자가 근거 줄과 함께 적은 연결입니다.

에이전트 없이 직접 실행하려면 한 줄이면 됩니다. 출력의 `map`과 `report` 경로를 브라우저로 여세요.

```sh
python3 -B skills/arche/scripts/arche.py /path/to/repo --lang ko
```
 리포트에는 분석 제외 사유, 최근 Git 커밋, 검증된 정적 import의 대표 순환 참조가 포함됩니다. 파일의 역할과 배경은 에이전트가 소스 근거를 바탕으로 대화에서 설명합니다.

## 검증과 범위

정적 분석 어댑터는 Python·TypeScript·JavaScript·Dart·Go·Java·Kotlin·Jsonnet·Terraform을 지원합니다. Terraform은 `module` 블록의 `source`를 모듈 폴더의 대표 파일(`main.tf`)로 연결하고, `file()`·`templatefile()`·`fileset()`과 `${path.module}/…` 경로로 읽는 파일도 연결합니다. YAML은 Docker Compose·GitHub Actions·Helm 차트·dbt에 적힌 경로를 **설정 파일 참조**로 연결하고, 모든 YAML에서 SQL 파일 경로를 찾습니다. 스키마를 모르는 YAML도 값이 실제 소스 파일을 가리키면 추정으로 연결하고, Python이 읽는 YAML(파일 이름, 패턴, 같은 폴더 관례)도 연결합니다. YAML이 왜 연결되지 않는지는 `diagnose_yaml.py REPO --output DIR`로 키 구조별 진단을 볼 수 있습니다(값은 출력하지 않음). dbt SQL의 `{{ ref() }}`는 그 모델 파일로 연결합니다. Java와 Kotlin은 서로의 클래스를 import할 수 있고, import 문 없이 쓰는 같은 패키지의 타입은 "추론됨"으로 표시합니다. 합성 레포 11개의 정답과 import 관계를 대조합니다. Dart·TypeScript·JavaScript의 HTTP 요청과 Hono·Express·Fastify·FastAPI·Flask·Go(gin, echo, chi, fiber, net/http)·Spring MVC(Java·Kotlin의 `@RequestMapping`·`@GetMapping` 등) 라우트가 발견되면 HTML의 **HTTP connections**에서 메서드·경로와 양쪽 소스 위치를 볼 수 있습니다. 유일하게 매칭된 후보도 배포 호스트와 런타임 호출은 별도 확인이 필요합니다. SQL·HQL 파일에서는 읽고 쓰는 테이블과 컬럼을 읽습니다. 한 파일이 읽는 테이블을 다른 파일이 쓰면 두 파일을 연결하고, 스캔한 SQL 중 쓰는 파일이 없는 테이블은 리포트의 **테이블**에 외부 테이블로 보여 줍니다. 테이블에 쓰는 컬럼마다 어느 상류 컬럼에서 어떤 식으로 오는지(예: `mart.revenue.net_amount` ← `SUM(o.amount)` ← `billing.orders.amount`)를 `columns.json`과 리포트에 남기고, 컬럼을 바꾸면 영향받는 하류 컬럼과 파일을 `changes.json`의 `column_impact`로 보여 줍니다. Python 안의 SQL, 기본 스키마, 레포 밖의 뷰는 읽지 않습니다. Python 파일에 문자열로 적힌 SQL 파일 경로는 **SQL 파일 참조**로 연결하고, f-string처럼 실행 시점에 만들어지는 경로는 따라가지 않고 건수만 보여 줍니다. Jsonnet 라이브러리 폴더를 `jsonnet -J`로 지정하는 레포는 같은 폴더를 `--jpath DIR`(레포 기준 상대 경로, 여러 번 가능)로 넘기면 그 import가 확정으로 연결됩니다. 레포 밖 폴더의 CSV·TSV·JSON·JSONL·Parquet은 `arche.py REPO --data DIR`로 프로파일해 SQL이 읽고 쓰는 테이블과 맞출 수 있습니다. 리포트의 **테이블**에서 테이블마다 쓰는 파일, 읽는 파일, 컬럼 타입, null 비율, 키 후보를 보여 주고, 문자열 값은 기본으로 보여 주지 않습니다. dbt 레포에서는 모델이 만드는 테이블(스키마는 변수)과 `schema.yml`의 설명·컬럼·테스트를 읽어, 문서와 데이터의 컬럼 차이와 테스트 단서를 같은 블록에 보여 줍니다.

```sh
python3 evals/score.py
```

스킬을 설치한 Codex에서 `$arche 레포 분석`으로 시작하세요. 이어서 “번역기가 없을 때 흐름을 보여줘”, “이 두 커밋의 변경과 영향 후보를 설명해줘”, “이 투어를 Mermaid로 내보내줘”처럼 요청할 수 있습니다. Git 변경 목록에는 정적 의존성으로 찾은 영향 후보가 표시됩니다. 감사 규칙은 근거 줄과 함께 순환·반복 import를 표시합니다.

[클라이언트별 호출과 투어 재사용](docs/installation-and-reuse.md) · [Terra on/off 실험과 재채점](skills/arche-eval/scripts/studies/README.md)

[공개 후보 변경 사항](docs/release-notes.md) · [MIT 라이선스](LICENSE)

[공개 개발과 PR 절차](CONTRIBUTING.md)

## 평가 및 1.0 개발

`arche-eval`로 합성 레포의 코드 이해와 수정 행동을 평가하고, 저장된 실행을 모델 재호출 없이 채점할 수 있습니다.

[평가 사용법](docs/evaluation.md) · [정적 감사](docs/audit.md) · [RC 자료 호환성](docs/artifact-compatibility.md) · [릴리스 패키징](docs/releasing.md)
