# Terraform·Jsonnet·YAML 분석 설계

2026-11-24 제안. 대상 브랜치는 `claude/epic-bohr-9pbqui`다. 구현은 형식별로 나눠 PR 세 개로 낸다.

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
3. 정해진 YAML 스키마의 경로 값을 `REFERENCES` edge로 만든다.
4. 레포 밖 출처는 `externals`에 기록한다.
5. Terraform 블록(`resource`, `data`, `module`, `variable`, `output`)을 심볼로 기록한다.

**하지 않는 것.**

- 외부 파서 사용. 표준 라이브러리만 쓴다는 제약은 그대로다. PyYAML이나 HCL 파서는 쓰지 않는다.
- Terraform 식(expression) 평가와 `count`·`for_each` 펼치기
- 한 모듈 안의 `var.x`, `module.y.out`, `aws_s3_bucket.b` 참조 연결. 같은 폴더의 `.tf` 파일은 하나의 모듈이다. 이 연결은 후속 작업으로 남긴다.
- Jsonnet 평가와 `std.extVar`, `-J`(`--jpath`) 실행 옵션 추측
- Helm `templates/` 안의 Go 템플릿 YAML. 렌더링 전에는 YAML이 아니다.
- 스키마를 모르는 YAML 키에서 경로를 찾는 것. 단, SQL 파일 경로는 예외다(아래 참고).
- `.tfvars`, `.tfstate`, `.tf.json`, `.hcl`

## 전제

- 대상 레포는 Terraform, Jsonnet, YAML을 함께 쓴다. 비중과 YAML 종류는 확인하지 못했다(열린 질문 1).
- Terraform은 1.x 문법을 가정한다.
- Jsonnet의 라이브러리 경로는 jsonnet-bundler 관례(`jsonnetfile.json`과 `vendor/`)를 가정한다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `langs/__init__.py` (수정) | 확장자 → 언어: `.tf` → `terraform`, `.jsonnet`·`.libsonnet` → `jsonnet`, `.yaml`·`.yml` → `yaml` | 없음 |
| `langs/hcl_lexer.py` (신규) | HCL 텍스트를 토큰으로 나눈다. 주석, 문자열과 `${}`, heredoc을 처리한다. | 없음 |
| `langs/terraform.py` (신규) | 최상위 블록을 심볼로 읽고 `module.source`를 import 기록으로 만든다. 출처를 폴더나 외부로 해석한다. | `hcl_lexer` |
| `langs/jsonnet.py` (신규) | `import` 계열 문장을 import 기록으로 만들고, 상대 경로와 라이브러리 경로로 해석한다. | 전용 마스커 |
| `langs/yaml_reader.py` (신규) | YAML 일부(블록 매핑·시퀀스, 스칼라, 짧은 흐름 표기)를 `(키 경로, 값, 줄)` 목록으로 읽는다. | 없음 |
| `langs/yaml_schemas.py` (신규) | 파일 이름과 최상위 키로 스키마를 고른다. 경로 값을 참조 기록으로 바꾼다. | `yaml_reader` |
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

대표 파일은 `main.tf`가 있으면 그것, 없으면 이름순 첫 `.tf` 파일이다(열린 질문 2). edge에는 `rule: module_dir`와 대상 폴더를 함께 남긴다.

## Jsonnet

### 읽기

전용 마스커가 `//`·`#`·`/* */` 주석을 가린다. 문자열 `"…"`, `'…'`, `@"…"`, `@'…'`, 텍스트 블록 `|||`도 처리한다. 가린 텍스트에서 `import`, `importstr`, `importbin` 바로 뒤의 문자열 리터럴만 읽는다. 문자열이 아니면(`import x`) Jsonnet 문법 오류이므로 건너뛴다. 최상위 `local 이름 =`은 심볼(`kind: local`)로 남긴다.

### 해석

`jsonnet` 실행기와 같은 순서로 찾는다.

1. 가져오는 파일의 폴더 기준 상대 경로: 있으면 `verified`.
2. 라이브러리 경로 후보: 레포의 `lib/` 폴더들. 한 곳에서만 찾으면 `inferred`, `rule: jpath`. 여러 곳이면 `unresolved`, `ambiguous`.
3. 어디서도 못 찾고, `jsonnetfile.json`이 있거나 경로가 `github.com/…`처럼 jsonnet-bundler 패키지 모양이면 `external`. 패키지는 `vendor/`에 설치된다. `vendor/`는 인벤토리에서 vendored 항목 하나로 접히므로 그 안의 파일 경로로 확인할 수 없다.
4. 그 밖에는 `unresolved`, `module_not_found`.

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

블록 스칼라(`|`, `>`)는 내용을 읽지 않고 건너뛴다. 앵커·별칭·태그(`&a`, `*a`, `!Ref`)는 떼고 값만 본다. 별칭이 가리키는 값을 펼치지는 않는다. 줄에 `{{`나 `{%`가 있으면 템플릿 YAML로 보고 그 줄을 건너뛴 뒤 `degraded`를 표시한다.

결과는 `(키 경로, 값, 줄)` 목록이다. 키 경로의 시퀀스 칸은 `[]`로 적는다. 예: `("services.web.build.context", "./web", 12)`, `("resources.[]", "../base", 3)`.

### 스키마와 경로 키

스키마는 파일 이름과 위치로 고른다. 맞는 스키마가 없으면 SQL 경로만 찾는다.

| 스키마 | 고르는 조건 | 경로로 읽는 키 | 대상 |
|---|---|---|---|
| Kustomize | `kustomization.yaml`, `kustomization.yml`, `Kustomization` | `resources.[]`, `bases.[]`, `components.[]`, `patches.[].path`, `patchesStrategicMerge.[]`, `configMapGenerator.[].files.[]`, `secretGenerator.[].files.[]`(경로만) | 파일 또는 폴더 |
| Compose | `compose*.y?ml`, `docker-compose*.y?ml` | `services.*.build`(문자열), `services.*.build.context`, `services.*.build.dockerfile`, `services.*.env_file`(경로만), `services.*.extends.file`, `include.[]` | 파일 또는 폴더 |
| GitHub Actions | `.github/workflows/*.y?ml`, `action.y?ml` | `jobs.*.uses`, `jobs.*.steps.[].uses`, `runs.steps.[].uses` | `./…`는 폴더(`action.yml`) 또는 워크플로 파일. 그 밖에는 외부 |
| Helm Chart | `Chart.yaml` | `dependencies.[].repository`가 `file://…`인 경우 | 폴더(`Chart.yaml`) |
| Ansible | 최상위가 시퀀스이고 항목에 `hosts`나 `tasks`가 있음, 또는 `roles/*/tasks/*.y?ml` | `import_playbook`, `include_tasks`, `import_tasks`, `include_vars`, `roles.[]`, `roles.[].role` | 파일 또는 `roles/<이름>` 폴더 |
| OpenAPI·JSON Schema | 최상위에 `openapi`, `swagger`, `$schema` | 모든 `$ref`(값의 `#` 앞이 비어 있지 않을 때) | 파일 |
| (모든 YAML) | 항상 | 값이 SQL 파일 경로(`fileref.is_path`) | SQL 파일 |

`env_file`, `secretGenerator`, `include_vars`는 경로만 기록하고 대상 파일의 내용은 읽지 않는다. 그 파일이 비밀 패턴에 걸리면 edge는 `unresolved`, `target_skipped`가 된다.

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
| A | Jsonnet | 문법이 가장 단순하고 import 규칙이 분명하다. 어댑터 추가 경로를 검증한다. |
| B | Terraform | HCL 렉서가 필요하다. 모듈 대표 파일 규칙을 여기서 정한다. |
| C | YAML 리더와 Kustomize·Compose·Actions, `REFERENCES` 일반화 | 가장 크다. `fileref` 변경이 SQL 참조에 영향이 없는지 함께 본다. |
| D (선택) | Helm, Ansible, OpenAPI 스키마 | C의 틀에 스키마만 더한다. |

## 열린 질문

1. 대상 레포의 YAML은 어떤 종류인가? Kubernetes 매니페스트·Kustomize, Helm, Compose, Actions, Airflow나 dbt 설정 중 무엇이 많은지에 따라 C와 D의 순서가 바뀐다.
2. Terraform 모듈 edge의 대상을 대표 파일 하나로 할지, 그 폴더의 모든 `.tf` 파일로 할지. 대표 파일 하나로 하면 지도가 깔끔하지만, 대표 파일이 아닌 `variables.tf`를 바꿨을 때 영향 경로가 보이지 않는다. 지금은 대표 파일 하나와 `module_dir`를 기록하고, 영향 분석에서 같은 폴더 파일을 함께 묶는 쪽을 권한다.
3. Jsonnet 라이브러리 경로가 `-J`로만 정해지는 레포인가? 그렇다면 `--jpath` 옵션을 `arche.py`에 추가하는 편이 추측보다 낫다.
