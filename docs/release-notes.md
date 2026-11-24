# Arche 릴리스 노트

## 1.0 출시 후보

- Terraform 파일 읽기: `file()`·`templatefile()`·`file*` 해시 함수·`fileset()`으로 읽는 파일을 `REFERENCES` edge(`via: terraform:함수`)로 연결합니다. `${path.module}` 기준 경로는 확정, 상대 경로와 `${path.root}`·`${path.cwd}`는 Terraform이 실행되는 폴더를 그 파일의 폴더로 보고 추정합니다. `fileset`은 패턴과 맞는 첫 분석 파일로 연결하고 `glob`과 `matched`(맞는 파일 수)를 남기며, 변경 분석은 패턴과 맞는 모든 파일을 의존으로 셉니다. 함수 밖의 `"${path.module}/workspaces"` 같은 문자열(모듈 인자 등)도 경로로 보고(`terraform:path`), 폴더면 그 아래 모든 파일(`폴더/**`)을 대표합니다. 변수로 넘긴 경로(`fileset(var.dir, …)`)는 따라가지 않습니다.
- dbt: SQL의 `{{ ref('모델') }}`을 그 모델 파일로 가는 `READS` edge(`rule: dbt_ref`, `via: dbt:ref`)로 연결합니다. Jinja가 SQL보다 먼저 실행되므로 `{{ }}`·`{% %}` 안의 `ref()`는 SQL 주석 안에 있어도 의존으로 셉니다(`{# #}` 안은 제외). SQL 파일이 속한 프로젝트(`dbt_project.yml`의 `model-paths`·`snapshot-paths`)에서 먼저 찾고, `packages.yml`의 `local` 패키지에서 그다음 찾습니다. 레포에 없는 패키지의 모델은 외부 패키지로, 시드는 `target_skipped`로 남깁니다. 버전 모델(`orders_v2.sql`)은 `v=`가 있으면 그 버전으로 확정, 없으면 가장 높은 버전으로 추정합니다. `{{ source('원천', '테이블') }}`은 `원천.테이블` 읽기가 되어, 쓰는 파일이 없으면 외부 테이블에 나옵니다. 속성 YAML의 `models: - name:`은 같은 이름의 모델 파일로 연결합니다. jaffle-shop에서 ref 11개, dbt-project-evaluator에서 76개가 모두 연결됩니다.
- 스키마 없는 YAML: Compose·Actions·Helm·dbt가 아닌 YAML(팀별 Airflow DAG 설정 등)에서 값이 레포에 실제로 있는 소스 파일을 가리키면 `path_value` 참조(추정)로 연결합니다. 그 YAML의 폴더, 레포 루트, 경로 뒤쪽이 맞는 파일 하나 순으로 찾고, 맞는 파일이 없으면 아무것도 남기지 않습니다. `via`에 키 경로가 남습니다.
- YAML: `.yaml`·`.yml` 파일을 읽습니다. 외부 파서 없이 블록 매핑·시퀀스, 한 줄 흐름 표기, 여러 문서를 읽고, 블록 스칼라·별칭·여러 줄 흐름 표기는 건너뜁니다. Docker Compose(`build`, `dockerfile`, `env_file`, `extends.file`, `./` 볼륨, `include`; 이름이 달라도 `services`의 `image`·`build`로 알아봅니다), GitHub Actions(`uses: ./…`는 레포 루트 기준, 그 밖의 `uses`는 외부), Helm(`Chart.yaml`의 `file://` 의존과 원격 차트)에 적힌 경로를 `REFERENCES` edge로 연결하고 `via`에 스키마와 키를 남깁니다. 폴더는 `action.yml`·`Chart.yaml`, 없으면 그 폴더의 첫 분석 파일로 연결합니다. 모든 YAML 값 중 SQL 파일 경로는 Python과 같은 SQL 파일 참조가 됩니다. Helm `templates/`는 읽지 않습니다. 리포트에 **설정 파일 참조** 섹션을 더했습니다. apache/airflow에서 Actions 참조 301개 중 292개가 연결되고, 남은 9개는 실행 중 체크아웃되는 경로입니다.
- Terraform: `.tf` 파일의 최상위 `resource`·`data`·`module`·`variable`·`output` 블록을 심볼로 남기고, `module` 블록의 `source`를 연결합니다. 로컬 경로(`./`, `../`)는 그 폴더의 대표 파일(`main.tf`, 없으면 이름순 첫 `.tf`)로 확정 연결하고, edge에 `module_dir`를 남깁니다. 변경 분석은 그 폴더의 다른 `.tf` 파일이 바뀌어도 모듈을 부르는 파일을 영향 후보로 보여 줍니다. 레지스트리·Git·HTTP·S3·GCS 출처는 외부 패키지로 기록합니다. `*.tfvars`·`*.tfstate*`는 비밀 파일로 열지 않고, `.terraform/`은 vendored로 건너뜁니다. terraform-aws-modules의 eks·vpc 레포에서 로컬 모듈 출처 61개·43개가 모두 연결됩니다.
- Jsonnet: `.jsonnet`·`.libsonnet` 파일의 `import`·`importstr`·`importbin`을 읽어 연결합니다. 가져오는 파일 기준 상대 경로를 먼저 찾고, 그다음 `--jpath DIR`로 받은 라이브러리 폴더를 `jsonnet -J`처럼 나중에 준 것부터 찾습니다. 두 경우 모두 확정입니다. `--jpath`가 없으면 레포의 `lib/` 폴더를 추정으로 찾고, jsonnet-bundler 패키지는 외부로 기록합니다. `brief.txt`는 `-J`·`--jpath`·`JSONNET_PATH`가 적힌 줄을 보여 줍니다. 최상위 `local`은 심볼로 남깁니다.
- 속도: import 단계가 파일마다 Python 구문 트리를 한 번만 만들고, FastAPI·Flask 이름이나 SQL 확장자가 없는 파일은 해당 탐색을 건너뜁니다. 절대 import는 그 이름을 가진 소스 루트만 찾습니다. apache/airflow(1만 5,600 파일)에서 132초가 39초로, 그 5배 크기에서 1,559초가 236초로 줄었고 결과는 같습니다.
- 메모리: 감사(audit)가 파일마다 구문 트리를 요약한 뒤 버립니다. apache/airflow에서 render 최대 메모리가 2.4GB에서 237MB로 줄었고 결과는 같습니다.
- Python: `__init__.py` 없는 로컬 `airflow/` 같은 폴더가, 그 안에 없는 모듈의 import를 가리지 않습니다. 이런 import는 외부 패키지로 분류됩니다.

- Spring 라우트: Java·Kotlin에서 클래스의 `@RequestMapping` 접두사와 메서드의 `@GetMapping`·`@PostMapping`·`@PutMapping`·`@DeleteMapping`·`@PatchMapping`·`@RequestMapping(method = ...)`을 읽어 라우트로 기록합니다. `value =`·`path =`와 배열(`{...}`, `[...]`, `arrayOf(...)`) 형태를 읽고, 각 라우트는 매핑 어노테이션이 있는 줄을 가리킵니다. 경로가 상수나 식이면 읽지 않고, 클래스 접두사가 상수면 `prefix_unknown`으로 표시합니다. `@FeignClient`의 매핑은 라우트로 세지 않으며, Feign·gRPC 호출 연결은 아직 읽지 않습니다.
- 테이블 lineage: SQL·HQL 파일에서 `FROM`·`JOIN`으로 읽는 테이블과 `INSERT`·`CREATE`·`MERGE`·`DELETE`로 쓰는 테이블을 읽어, 읽는 파일에서 쓰는 파일로 가는 `READS` 연결을 기록합니다. 스키마가 변수인 이름은 테이블 이름으로 맞춰 "추론됨"으로 표시하고, 이름 자체가 실행 시점에 정해지면 따라가지 않습니다. 리포트의 **테이블**에서 외부 테이블을 스키마별로 볼 수 있고, SQL 파일을 바꾸면 그 테이블을 읽는 SQL과 이를 가리키는 DAG 파일이 영향 범위에 듭니다. 컬럼 단위 lineage, Python 안의 SQL, `USE`로 정하는 기본 스키마는 읽지 않습니다. 같은 파일이 먼저 만든 테이블을 읽는 경우는 연결하지 않고, 한쪽에만 카탈로그가 붙은 이름은 "추론됨"으로 연결합니다.
- SQL 참조: `.sql`·`.hql` 파일을 노드로 스캔하고, Python 파일에 문자열로 적힌 SQL 경로를 `REFERENCES` 연결로 기록합니다. `os.path.join`과 `Path /`로 이은 경로도 읽습니다. 리포트의 **SQL 파일 참조**에서 정적 참조가 없는 SQL 파일과 따라가지 못한 경로를 볼 수 있습니다. SQL 내용, `task >> task`, `template_searchpath`는 읽지 않습니다.
- SQL·HQL 파일이 분석 대상에 들어가면서 SQL이 있는 레포는 분석한 파일 수와 줄 수가 늘고 `inventory_sha256`이 달라집니다. 기존 `map.json`이 SQL 폴더를 부품에 넣지 않았다면 "부품에 속하지 않은 파일" 경고가 나올 수 있습니다.
- HTTP 인식: Python에서 다른 파일이 `include_router(..., prefix=...)`로 붙인 접두사를 라우트에 적용합니다. chi의 중첩 `Route`, `this.api.get(...)` 형태의 axios 호출, 같은 파일의 상수 문자열을 넘기는 `fetch(URL)`도 읽습니다.
- 감사: TypeScript·JavaScript·Dart·Go에서 export되지 않았고 이름이 파일·라이브러리·패키지 안에서 한 번만 나오는 선언을 후보로 냅니다.
- 지도: 연결이 많은 화면은 선을 흐리게 그리고, 카드에 마우스를 올리거나 선택하면 그 카드의 연결만 보여 줍니다.
- 평가 리포트(`arche-eval`)에 전체 요약(구성 수, 시도 수, 통과율)과 결과 배지를 추가했습니다.
- 실제 앱 레포(Flutter 앱 + Worker API, 파싱 1,041개 파일)에서 지도 절차를 끝까지 돌려 찾은 문제를 고쳤습니다.
  - 상위 부품이 직접 가진 파일과 연결된 하위 부품을 열면 지도가 그려지지 않던 오류를 고쳤습니다.
  - 테스트 파일에 선언된 라우트가 실제 라우트와 경쟁해 요청이 "후보 여럿"으로 남던 문제를 고쳤습니다.
  - 파일이 많은 부품은 폴더별 카드로 나눠 보여 줍니다. 부품을 크기 때문에 쪼개라는 경고는 없앴습니다.
  - 부품 안에서는 그 부품과 닿는 연결과 이웃만 보여 줍니다.
  - `describe.py`가 먼저 설명할 파일(`describe_first`)을 알려 주고, `brief.txt`는 표준 라이브러리를 빼고 폴더 간 import를 폴더 단위로 셉니다.
- 이어 쓰기가 이름과 내용이 함께 바뀐 파일도 따라갑니다. 줄의 60% 이상이 남아 있고 다른 후보보다 뚜렷이 가까운 새 파일로 설명과 근거를 옮기고 다시 확인할 목록에 넣습니다.
- HTTP 인식에 Go 라우터(gin, echo, chi, fiber, gorilla, net/http와 같은 파일의 `Group` 접두사)와 다른 파일에서 import한 axios 인스턴스 호출을 추가했습니다.
- Kotlin 와일드카드 import를 호출한 최상위 함수 이름으로도 좁힙니다.
- `render.py --full-details`는 큰 레포에서도 파일 상세를 전부 HTML에 씁니다. 지도는 두 손가락으로 확대됩니다.
- `evals/audit_recall.py`가 실제 소스에 쓰이지 않는 선언을 심어 Java·Kotlin 감사의 인식률을 잽니다.
- 증분 갱신: `arche.py --from 이전_실행`이 이전 `map.json`과 설명을 새 스캔으로 옮깁니다. 줄이 밀린 근거는 따라가고, 이름이 바뀐 파일의 설명은 옮기며, `carry.json`에 새 파일·바뀐 파일·다시 인용할 근거를 적습니다. `carry.py --save`로 지도를 공유용 폴더에 저장합니다.
- 자동 지도가 폴더를 나누다 끝나지 않던 문제를 고쳤습니다. 최상위 폴더가 3개 미만이고 폴더에 파일만 있을 때 실행이 멈췄습니다.
- 지도를 끌어서 옮기고 휠로 확대합니다(`+`, `-`, `0` 키와 버튼도 됩니다). 변경과 감사 후보를 부품별로 보는 보기, `map.json`의 `screens`를 보여 주는 화면 보기를 추가했습니다.
- `render.py --editor vscode|cursor|zed|idea`를 주면 지도의 근거 줄에 편집기 링크가 붙습니다. 이때만 레포의 절대 경로가 `map.html`에 들어갑니다.
- `brief.txt`가 라우트처럼 보이지만 읽지 못한 줄의 수와 위치를 적습니다.
- 감사에 Java·Kotlin 후보 두 가지를 추가했습니다. 파일 안 다른 곳에서 언급되지 않는 private 멤버와, 어디서도 이름이 나오지 않는 비공개 최상위 타입입니다. 어노테이션이 붙은 선언과 테스트 경로는 제외합니다.
- 파싱한 파일이 500개를 넘으면 리포트가 파일 상세를 데이터로 싣고 파일을 펼칠 때 그립니다. Django 5.0 리포트가 6.6MB에서 2.2MB로 줄었습니다. 500개 이하에서는 이전처럼 전부 HTML에 들어 있습니다.
- 지도 뷰어 `map.html`을 추가했습니다. 부품 카드와 연결선으로 시스템을 보여 주고, 부품을 열어 파일까지 들어가며, 흐름과 목록 보기를 제공합니다.
- `brief.py`가 에이전트용 요약을, `map.py`가 `map.json`을 검사해 `atlas.json`을, `describe.py`가 설명 워크시트를 만듭니다. `map.json`이 없으면 폴더 기준 지도를 그립니다.
- `graph.json`에 읽어 낸 라우트 목록 `http_routes`가 추가됐습니다.
- 투어 단계에 `range_sha256`을 넣으면 파일의 다른 부분이 바뀌어도 인용한 줄을 따라가 재사용합니다. `cite.py`가 단계를 만들고 기존 투어를 현재 스캔에 맞춰 다시 고정합니다.
- Python 분석과 감사가 빨라졌습니다. Django 5.0 전체(파싱 2,818개 파일) 실행이 약 18초에서 약 9초로 줄었습니다.
- Kotlin에서 같은 패키지의 최상위 함수를 import 없이 호출하는 연결을 추론으로 표시합니다.
- 평가 리포트(`arche-eval`)에 같은 서체와 다크 모드를 적용했습니다.
- Java와 Kotlin을 지원합니다. 완전한 이름으로 import를 해석하고, 같은 패키지에서 import 없이 쓰는 타입과 와일드카드 import는 추론으로 표시합니다.
- 리포트를 다시 설계했습니다. 개요 수치, 핵심 파일 순위, 폴더를 펼치는 의존성 맵, 라이트·다크 테마, 한국어 화면(`--lang ko`)을 제공합니다.
- `arche.py` 한 줄로 스캔, import 해석, 렌더링을 실행합니다.
- 투어에 `title`·`summary`와 단계별 `title`·`note`를 적으면 리포트에 설명이 함께 실립니다. 최근 Git 이력에는 커밋 제목이 표시됩니다.
- HTTP 연결 후보가 `app.get("/path", …)` 형태의 라우트, FastAPI·Flask 데코레이터, `fetch`·axios 요청, Dart 헬퍼 경유 요청을 읽습니다. `:id` 같은 변수 경로도 맞추고, 외부 호스트로 가는 요청은 `external_host`로 구분합니다.
- 화면과 버튼에서 소스 근거를 좁혀 보는 기능을 추가했습니다. 모바일 요청과 서버 핸들러의 HTTP 연결 후보는 추정으로 표시합니다.
- Git 두 리비전의 의존 후보와 감사 후보의 추가·해소 여부를 비교합니다.
- `arche-eval`이 합성 코드 이해 및 수정 과제를 실행하고 로컬 재채점, 비교 HTML, 블라인드 리뷰 표본을 제공합니다.
- 기존 0.1 스냅샷과 투어를 읽을 수 있습니다. 태그에서 두 스킬 ZIP과 SHA-256 체크섬을 만들고 설치를 점검합니다.

[공개 로드맵](ROADMAP.md) · [설치](installation-and-reuse.md) · [평가](evaluation.md)

## 0.1.0-rc.1

첫 공개 프리릴리스에서 Codex 설치, 읽기 전용 레포 탐색, 화면별 소스 안내, 오프라인 HTML과 근거 투어 내보내기를 제공했습니다.
