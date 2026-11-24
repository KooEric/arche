# Terraform·Jsonnet·YAML 분석 설계

2026-11-24 제안. 대상 브랜치는 `claude/epic-bohr-9pbqui`다. 구현은 형식별로 나눠 PR 네 개로 낸다. 같은 날 사용자 답을 받아 열린 질문 세 개를 결정 사항으로 바꿨다.

## 왜 바꾸는가

인프라와 설정을 담은 레포에서 Arche는 거의 아무것도 보여 주지 못한다. `.tf`, `.jsonnet`, `.libsonnet`, `.yaml`, `.yml` 파일은 인벤토리에서 `unsupported_lang`으로 빠진다. 지도에는 폴더만 남고 연결선이 없다. 이 파일들도 서로를 가리킨다. Terraform 모듈은 다른 모듈 폴더를 부르고, Jsonnet은 라이브러리를 `import`한다. Kustomize, Compose, GitHub Actions 같은 YAML도 다른 파일이나 폴더를 경로로 적는다.

## 목표와 범위

**목표.** 세 질문에 근거 줄과 함께 답한다.

1. 이 Terraform 모듈이나 Jsonnet 라이브러리를 바꾸면 어떤 파일이 영향을 받는가
2. 이 YAML 설정은 어떤 파일과 폴더를 끌어오는가
3. 레포 밖에서 가져오는 모듈과 액션, 패키지는 무엇인가

**이번 범위.**

1. 세 형식을 인벤토리에 넣고 파일 노드를 만든다.
2. Terraform `module` 블록의 `source`와 Jsonnet `import`·`importstr`·`importbin`을 `IMPORTS` edge로 만든다.
3. 정해진 YAML 스키마(Helm, Compose, GitHub Actions, Airflow, dbt)의 경로 값을 `REFERENCES` edge로 만든다.
4. dbt SQL의 `{{ ref('모델') }}`을 그 모델 파일로 가는 `READS` edge로 만든다.
5. 레포 밖 출처는 `externals`에 기록한다.
6. Jsonnet 라이브러리 경로를 받는 `--jpath` 옵션을 추가한다.
7. Terraform 블록(`resource`, `data`, `module`, `variable`, `output`)을 심볼로 기록한다.

**하지 않는 것.**

- 외부 파서 사용. 표준 라이브러리만 쓴다는 제약은 그대로다. PyYAML이나 HCL 파서는 쓰지 않는다.
- Terraform 식(expression) 평가와 `count`·`for_each` 펼치기
- 한 모듈 안의 `var.x`, `module.y.out`, `aws_s3_bucket.b` 참조 연결. 같은 폴더의 `.tf` 파일은 하나의 모듈이다. 이 연결은 후속 작업으로 남긴다.
- Jsonnet 평가와 `std.extVar`
- Kustomize, Ansible, OpenAPI 스키마. 대상 레포에서 많지 않다. 후속 작업으로 남긴다.
- dbt `{{ source('원천', '테이블') }}`을 실제 테이블 이름으로 바꾸기. `sources:` 정의의 `schema`·`identifier`를 읽어야 해서 후속 작업으로 남긴다.
- Helm `templates/` 안의 Go 템플릿 YAML. 렌더링 전에는 YAML이 아니다.
- 스키마를 모르는 YAML 키에서 경로를 찾는 것. 단, SQL 파일 경로는 예외다(아래 참고).
- `.tfvars`, `.tfstate`, `.tf.json`, `.hcl`

## 전제

- 대상 레포는 Terraform, Jsonnet, YAML을 함께 쓴다. 사용자가 확인한 YAML 종류는 Helm, Docker Compose, GitHub Actions, Airflow, dbt다.
- Terraform은 1.x 문법을 가정한다.
- Jsonnet 라이브러리 경로는 주로 실행 옵션 `-J`로 정한다(사용자 확인). jsonnet-bundler(`jsonnetfile.json`과 `vendor/`)도 쓸 수 있다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `langs/__init__.py` (수정) | 확장자 → 언어: `.tf` → `terraform`, `.jsonnet`·`.libsonnet` → `jsonnet`, `.yaml`·`.yml` → `yaml` | 없음 |
| `langs/hcl_lexer.py` (신규) | HCL 텍스트를 토큰으로 나눈다. 주석, 문자열과 `${}`, heredoc을 처리한다. | 없음 |
| `langs/terraform.py` (신규) | 최상위 블록을 심볼로 읽고 `module.source`를 import 기록으로 만든다. 출처를 폴더나 외부로 해석한다. | `hcl_lexer` |
| `langs/jsonnet.py` (신규) | `import` 계열 문장을 import 기록으로 만들고, 상대 경로와 라이브러리 경로로 해석한다. | 전용 마스커 |
| `langs/yaml_reader.py` (신규) | YAML 일부(블록 매핑·시퀀스, 스칼라, 짧은 흐름 표기)를 `(키 경로, 값, 줄)` 목록으로 읽는다. | 없음 |
| `langs/yaml_schemas.py` (신규) | 파일 이름과 최상위 키로 스키마를 고른다. 경로 값을 참조 기록으로 바꾼다. | `yaml_reader` |
| `langs/sql_tables.py` (수정) | 변수 자리로 가리기 전에 `{{ ref('…') }}`를 읽어 `ref` 레코드를 남긴다. | 없음 |
| `brief.py` (수정) | 레포 텍스트에서 `-J`, `--jpath`, `JSONNET_PATH`가 적힌 줄을 모아 `brief.txt`에 보여 준다. | 없음 |
| `arche.py`, `imports.py` (수정) | `--jpath DIR` 옵션(여러 번 가능, 레포 기준 상대 경로)을 받아 `graph.json`의 `options.jpath`에 기록한다. | 없음 |
| `langs/yaml.py` (신규) | 어댑터. import는 없고 `file_refs`만 돌려준다. | `yaml_schemas` |
| `fileref.py` (수정) | SQL 전용 해석을 대상 종류(파일·폴더)별로 일반화한다. | 없음 |
| `graph.py` (수정) | `REFERENCES` 대상을 SQL 파일에서 분석한 모든 파일과 폴더로 넓힌다. | `fileref` |
| `inventory.py` (수정) | 비밀 패턴에 `*.tfvars`, `*.tfstate*`를, vendored 폴더에 `.terraform/`을 더한다 | 없음 |

어댑터 계약(`extract`, `build_context`, `resolve`)은 바꾸지 않는다. `extract`가 `file_refs`를 돌려주는 방식은 Python 어댑터가 이미 쓰고 있다.

## Terraform

### 읽기

전용 렉서가 토큰을 만든다.

- 주석: `#`, `//`, `/* */`
- 문자열: `"…"`. 내부의 `${ … }`와 `%{ … }`는 중첩을 세어 건너뛰고, 변수 자리가 있다고 표시한다.
- heredoc: `<<EOF`, `<<-EOF`. 끝 표시가 나올 때까지 한 덩어리로 본다.

중괄호 깊이를 센다. 깊이 0에서 `식별자 "라벨"* {` 모양이면 블록 머리로 본다.

| 블록 | 심볼 이름 | kind |
|---|---|---|
| `resource "aws_s3_bucket" "logs"` | `aws_s3_bucket.logs` | `resource` |
| `data "aws_iam_policy" "p"` | `data.aws_iam_policy.p` | `data` |
| `module "vpc"` | `module.vpc` | `module` |
| `variable "region"` | `var.region` | `variable` |
| `output "id"` | `output.id` | `output` |

`module` 블록 안의 깊이 1에서 `source = "…"`를 읽어 import 기록을 만든다. `source`가 변수 자리를 포함하면 `dynamic`으로 남긴다. Terraform은 `source`에 변수를 허용하지 않으므로 실제로는 거의 없다.

괄호가 맞지 않거나 문자열이 닫히지 않으면 SQL 어댑터처럼 `degraded`를 표시하고 그때까지 읽은 것을 쓴다.

### 출처 해석

Terraform 문서의 출처 규칙을 따른다.

| `source` | 결과 |
|---|---|
| `./…`, `../…` 이고 그 폴더에 분석한 `.tf` 파일이 있음 | `verified`, 대상은 그 폴더의 대표 파일 |
| `./…`, `../…` 이고 폴더에 `.tf` 파일이 없음 | `unresolved`, `module_not_found` |
| `hashicorp/consul/aws` 같은 레지스트리 주소 | `external`, `package`는 주소 |
| `git::…`, `github.com/…`, `bitbucket.org/…`, `s3::…`, `gcs::…`, `https://…` | `external`, `package`는 `?ref=` 앞까지 |
| `…//하위폴더` 가 붙은 원격 주소 | `external`. `//` 앞까지를 패키지로 본다 |

대표 파일은 `main.tf`가 있으면 그것, 없으면 이름순 첫 `.tf` 파일이다(사용자 결정). 연결선은 대표 파일 하나로만 긋는다. 지도와 의존 그래프가 폴더 안 파일 수만큼 늘어나지 않는다. edge에는 `rule: module_dir`와 `module_dir`(대상 폴더)를 남긴다. 그래서 변경 분석(`changes.py`)은 같은 폴더의 `variables.tf`·`outputs.tf`가 바뀐 경우에도 그 모듈을 부르는 파일을 영향 후보로 보여 줄 수 있다.

## Jsonnet

### 읽기

전용 마스커가 `//`·`#`·`/* */` 주석을 가린다. 문자열 `"…"`, `'…'`, `@"…"`, `@'…'`, 텍스트 블록 `|||`도 처리한다. 가린 텍스트에서 `import`, `importstr`, `importbin` 바로 뒤의 문자열 리터럴만 읽는다. 문자열이 아니면(`import x`) Jsonnet 문법 오류이므로 건너뛴다. 최상위 `local 이름 =`은 심볼(`kind: local`)로 남긴다.

### 해석

`jsonnet` 실행기와 같은 순서로 찾는다.

1. 가져오는 파일의 폴더 기준 상대 경로: 있으면 `verified`, `rule: relative`.
2. `--jpath`로 받은 폴더: 실행기처럼 오른쪽(나중에 준 것)부터 찾는다. 처음 찾은 곳이 `verified`, `rule: jpath`다. 사용자가 실행 옵션을 그대로 알려 준 것이므로 확정으로 본다.
3. `--jpath`를 받지 않았을 때만: 레포의 `lib/` 폴더들을 후보로 본다. 한 곳에서만 찾으면 `inferred`, `rule: jpath_guess`. 여러 곳이면 `unresolved`, `ambiguous`.
4. 어디서도 못 찾고, `jsonnetfile.json`이 있거나 경로가 `github.com/…`처럼 jsonnet-bundler 패키지 모양이면 `external`. 패키지는 `vendor/`에 설치된다. `vendor/`는 인벤토리에서 vendored 항목 하나로 접히므로 그 안의 파일 경로로 확인할 수 없다.
5. 그 밖에는 `unresolved`, `module_not_found`. `--jpath` 없이 이 결과가 많으면 리포트에 "`-J` 폴더를 `--jpath`로 알려 주면 더 연결된다"는 안내를 띄운다.

`-J` 값은 보통 Makefile, CI 설정, 스크립트에 적혀 있다. 이 값을 자동으로 읽지는 않는다. 실행 명령은 레포마다 형태가 달라 추측이 틀리기 쉽다. 대신 `brief.txt`에 `-J`·`--jpath`·`JSONNET_PATH`가 적힌 줄을 모아 보여 준다. 에이전트는 그 줄을 보고 `--jpath`를 붙여 다시 실행한다.

대상이 `.json`처럼 분석하지 않는 파일이면 Python 어댑터처럼 `unresolved`, `target_skipped`로 둔다.

## YAML

### 왜 스키마를 정하는가

YAML에는 import 문법이 없다. 경로처럼 생긴 값은 많지만 대부분 경로가 아니다(이미지 태그, URL, 라벨 값). 모든 값을 경로로 보면 잘못된 연결이 많이 생긴다. 그래서 키의 뜻이 알려진 스키마만 읽는다.

### 읽기(`yaml_reader`)

YAML 전체를 해석하지 않는다. 다음만 읽는다.

- 들여쓰기로 이어지는 블록 매핑(`key: value`)과 블록 시퀀스(`- item`)
- 일반 스칼라와 따옴표 스칼라(`'…'`, `"…"`)
- 한 줄짜리 흐름 시퀀스(`[a, b]`)와 흐름 매핑(`{a: b}`)
- 주석(`#`)과 문서 구분(`---`)

블록 스칼라(`|`, `>`)는 내용을 읽지 않고 건너뛴다. 앵커·별칭·태그(`&a`, `*a`, `!Ref`)는 떼고 값만 본다. 별칭이 가리키는 값을 펼치지는 않는다. 값 안의 `{{ … }}`, `${{ … }}`, `${…}`는 변수 자리로 읽는다. GitHub Actions와 dbt 설정에는 흔하므로 `degraded`로 보지 않는다. `{%`로 시작하는 줄이나 키 자리에 `{{`가 오는 줄은 템플릿 구조로 보고 건너뛴 뒤 `degraded`를 표시한다.

결과는 `(키 경로, 값, 줄)` 목록이다. 키 경로의 시퀀스 칸은 `[]`로 적는다. 예: `("services.web.build.context", "./web", 12)`, `("resources.[]", "../base", 3)`.

### 스키마와 경로 키

스키마는 파일 이름과 위치로 고른다. 맞는 스키마가 없으면 SQL 경로만 찾는다.

| 스키마 | 고르는 조건 | 경로로 읽는 키 | 대상 |
|---|---|---|---|
| Helm | `Chart.yaml` | `dependencies.[].repository`가 `file://…`인 경우 | 폴더(`Chart.yaml`) |
| Helm | 차트 폴더(`Chart.yaml`이 있는 폴더)의 `values*.yaml` | 읽지 않는다. 차트 노드에 묶이도록 파일 노드만 만든다 | — |
| Compose | `compose*.y?ml`, `docker-compose*.y?ml` | `services.*.build`(문자열), `services.*.build.context`, `services.*.build.dockerfile`, `services.*.env_file`(경로만), `services.*.extends.file`, `services.*.volumes.[]`의 `./`·`../`로 시작하는 호스트 쪽 경로, `include.[]` | 파일 또는 폴더 |
| GitHub Actions | `.github/workflows/*.y?ml`, `action.y?ml` | `jobs.*.uses`, `jobs.*.steps.[].uses`, `runs.steps.[].uses` | `./…`는 폴더(`action.yml`) 또는 워크플로 파일. 그 밖에는 외부 |
| Airflow | 최상위 매핑의 값에 `default_args`와 `tasks`가 있음(dag-factory 형식) | `*.tasks.*.python_callable_file`, `*.tasks.*.bash_command`·`*.tasks.*.sql`이 파일 경로일 때, `template_searchpath` | 파일 또는 폴더 |
| dbt 프로젝트 | `dbt_project.yml` | `model-paths`, `seed-paths`, `test-paths`, `analysis-paths`, `macro-paths`, `snapshot-paths`의 각 항목 | 폴더 |
| dbt 패키지 | `packages.yml`, `dependencies.yml` | `packages.[].local` | 폴더(`dbt_project.yml`). `package`·`git`은 외부 |
| dbt 속성 | `dbt_project.yml`이 있는 프로젝트의 모델 폴더 아래 `*.yml` | `models.[].name`, `snapshots.[].name`, `seeds.[].name` | 같은 이름의 모델·스냅숏·시드 파일(`.sql`, `.csv`) |
| (모든 YAML) | 항상 | 값이 SQL 파일 경로(`fileref.is_path`) | SQL 파일 |

`env_file`은 경로만 기록하고 대상 파일의 내용은 읽지 않는다. 그 파일이 비밀 패턴에 걸리면 edge는 `unresolved`, `target_skipped`가 된다. Helm `templates/` 안의 YAML은 Go 템플릿이므로 파일 노드만 만들고 내용은 읽지 않는다.

Airflow 설정은 dag-factory 형식만 스키마로 읽는다. 다른 형식의 Airflow YAML(연결·변수 설정, 자체 형식의 DAG 정의)은 마지막 줄의 SQL 경로 찾기만 적용된다. 사용자의 레포에 다른 형식이 많으면 키 목록을 받아 스키마를 더한다.

### dbt 모델 연결

dbt 모델 SQL은 테이블 이름 대신 `{{ ref('orders') }}`를 쓴다. 지금 `sql_tables`는 이 자리를 변수(`{}`)로 바꾸므로 읽기가 모두 `dynamic`으로 끝난다.

- `sql_tables`는 가리기 전에 `ref('이름')`, `ref('패키지', '이름')`을 읽어 `{"ref": "이름", "package": …, "line": …}` 레코드를 남긴다. 인자가 문자열 리터럴이 아니면 `dynamic`이다.
- `graph.py`는 dbt 프로젝트의 모델 폴더(`model-paths`, 없으면 `models/`) 아래 `.sql` 파일을 파일 이름(확장자 제외)으로 색인한다. 스냅숏·시드도 같은 방식이다.
- 이름이 하나와 맞으면 `READS` edge, `verified`, `rule: dbt_ref`. 여럿이면 `unresolved`, `ambiguous`. 패키지를 적었고 그 패키지가 `packages.yml`의 `local` 폴더가 아니면 `external`.
- `{{ source('원천', '테이블') }}`은 `external_tables`에 `원천.테이블`로 기록한다.

dbt 모델 이름은 프로젝트 안에서 유일해야 하므로 이름 맞추기를 확정으로 본다.

### 해석

경로 값은 그 YAML 파일의 폴더 기준으로 푼다. Compose는 `-f`로 받은 첫 파일의 폴더가 기준이지만, 정적으로는 알 수 없으므로 자기 폴더로 본다. GitHub Actions의 `./…`는 레포 루트 기준이다.

| 경우 | 결과 |
|---|---|
| 파일이 있고 분석함 | `verified`, `rule: relative` |
| 폴더가 있음 | `verified`, 대상은 그 폴더의 대표 파일(`kustomization.yaml`, `action.yml`, `Chart.yaml`, `Dockerfile`, 없으면 이름순 첫 분석 파일) |
| 있지만 분석하지 않음 | `unresolved`, `target_skipped` |
| 없음 | `unresolved`, `not_found` |
| `https://`, `oci://`, `owner/repo@ref`, `docker://` | `external` |
| 변수 자리(`${VAR}`, `${{ … }}`)가 있음 | `unresolved`, `dynamic` |

## 그래프에 들어가는 모양

- Terraform `module.source`와 Jsonnet `import`는 `IMPORTS` edge다. `origin`은 언어 이름이다. 기존 화면의 의존 지도와 순환 검사에 그대로 들어간다.
- YAML 경로는 `REFERENCES` edge다. `via`에 스키마와 키 경로를 남긴다(예: `kustomize:resources`). 지금 `REFERENCES`는 "Python 파일에 적힌 SQL 파일 경로"만 뜻한다. 설명 문서(`SKILL.md`)와 리포트 문구를 함께 고친다.
- 외부 출처는 기존 `externals`에 넣는다. 리포트의 외부 패키지 표에 Terraform 모듈, GitHub 액션, Jsonnet 패키지가 함께 나온다.

## 감사(audit)와 지도

- `isolated-module`은 Python 전용이다. 이번 형식에는 적용하지 않는다. Terraform 루트 모듈과 YAML 설정은 원래 아무도 가져오지 않는다.
- `map.py`의 폴더 기본 묶음은 그대로 둔다. 새 언어 이름만 i18n 표에 넣는다.

## 성능

세 형식 모두 파일 하나를 한 번 훑는 선형 처리다. 파일별 결과만 남기고 원문과 토큰은 바로 버린다. 오늘 `build_audit`에서 고친 것처럼 전체 레포 분량을 한꺼번에 메모리에 들고 있지 않는다. 대규모 레포 측정에 Terraform·YAML이 많은 레포 하나를 더한다.

## 시험

- 형식별 단위 시험: 렉서·리더의 경계(주석 안의 `import`, heredoc 안의 `source =`, 블록 스칼라 안의 `resources:`, 템플릿 줄)
- 해석 시험: 위 표의 각 줄마다 하나
- 골든 픽스처: `tests/fixtures/infra-mini/` 아래에 Terraform 루트와 모듈 둘, Jsonnet 라이브러리와 `vendor/`, Kustomize base/overlay, Compose, Actions 워크플로를 둔다. 기대 edge 목록을 고정한다.
- 표준 라이브러리만 쓰는지 확인하는 기존 시험(`test_stdlib_only.py`)이 새 모듈도 덮는지 확인한다.

## 나눠 내기

| PR | 내용 | 이유 |
|---|---|---|
| A | Jsonnet과 `--jpath` | 문법이 가장 단순하고 import 규칙이 분명하다. 어댑터 추가 경로와 옵션 전달 경로를 검증한다. |
| B | Terraform | HCL 렉서가 필요하다. 모듈 대표 파일 규칙을 여기서 정한다. |
| C | YAML 리더, `REFERENCES` 일반화, Compose·GitHub Actions·Helm | 가장 크다. `fileref` 변경이 SQL 참조에 영향이 없는지 함께 본다. |
| D | dbt(`dbt_project.yml`·`packages.yml`·속성 YAML, SQL의 `ref()`)와 Airflow(dag-factory) | 데이터 파이프라인 연결이다. dbt는 SQL 쪽도 바꾸므로 C와 따로 낸다. |
| 후속 | Kustomize, Ansible, OpenAPI, dbt `source()` 펼치기, Terraform 모듈 안 참조 | 요청이 있을 때 |

## 결정 사항

2026-11-24 사용자 답.

1. YAML 종류: Helm, Docker Compose, GitHub Actions, Airflow, dbt. Kustomize·Ansible·OpenAPI는 후속으로 미룬다. dbt `ref()` 연결을 범위에 넣는다.
2. Terraform 모듈 edge는 대표 파일 하나로 연결한다. `module_dir`는 기록해 변경 분석에 쓴다.
3. Jsonnet 라이브러리 경로는 주로 `-J`로 지정한다. `--jpath` 옵션을 추가하고, 받은 경로로 찾은 import는 `verified`로 본다. 옵션이 없을 때만 `lib/` 추측을 쓴다.

## 남은 질문

1. 레포의 Airflow YAML이 dag-factory 형식인가, 자체 형식인가? 자체 형식이면 경로를 담는 키 이름을 알려 주면 스키마에 넣는다.
2. dbt 프로젝트가 레포에 여러 개 있는가? 여러 개면 `ref()`는 같은 프로젝트 안에서 먼저 찾고, `packages.yml`의 `local`로 이어진 프로젝트를 그다음에 찾는다.
