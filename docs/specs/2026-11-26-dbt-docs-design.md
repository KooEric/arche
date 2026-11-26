# dbt 모델 테이블과 속성 문서 설계

2026-11-26 확정. 대상 버전은 **1.2.0**이다. 기준은 `main` 18cb40d(v1.1.0)이다. [로컬 데이터 프로파일](2026-11-25-data-profile-design.md)(1.1.0) 위에 쌓는다. 컬럼 단위 lineage는 1.3.0에서 따로 한다([브리프](../plans/2026-11-26-v1.3-column-lineage-brief.md)).

## 왜 바꾸는가

1.1.0의 테이블 lineage는 `INSERT INTO`, `CREATE TABLE` 같은 쓰기 문장이 있는 SQL만 "테이블을 쓰는 파일"로 본다. dbt 모델은 SELECT만 담고, 파일 이름과 설정이 테이블 이름을 정한다. 그래서 dbt 프로젝트의 모델 테이블은 `written_tables`에 없다. 1.1.0 데이터 프로파일도 dbt 모델과 맞춰지지 않는다.

`schema.yml`과 `sources.yml`에는 모델과 컬럼의 설명, 문서에 적힌 컬럼 목록, 선언된 테스트, source의 실제 스키마와 테이블 이름이 있다. 지금은 모델 이름 연결(`dbt_names`)에만 쓰고 나머지는 읽지 않는다. 현재 YAML 리더는 여러 줄 텍스트(`description: >`)를 일부러 건너뛴다.

## 목표와 범위

**목표.** 테이블 하나에 대해 리포트 한 곳에서 다음에 답한다.

1. 이 dbt 모델은 어떤 테이블을 만드는가. 그 테이블을 누가 읽는가.
2. 문서는 이 테이블과 컬럼을 어떻게 설명하고, 어떤 테스트를 선언했는가.
3. 문서의 컬럼과 실제 데이터(1.1.0 프로파일)의 컬럼은 어디서 어긋나는가.

**이번 범위.**

1. 모델이 쓰는 테이블 이름을 정해 `written_tables`에 더한다. 이름은 모델 이름이나 alias를 쓰고, 스키마는 변수로 둔다.
2. `sources.yml`의 `schema`, `identifier`, `database`로 `source()` 읽기의 테이블 이름을 바꾼다.
3. 속성 파일에서 모델과 컬럼의 설명, 컬럼 목록과 `data_type`, 테스트를 읽어 `graph.json`의 새 키로 남긴다.
4. 리포트의 테이블 블록에 문서와 데이터의 비교, 그리고 테스트 단서를 보인다.
5. YAML 리더에 여러 줄 텍스트를 읽는 선택 옵션을 둔다.

**하지 않는 것.**

- dbt 매크로 실행. 커스텀 `generate_schema_name`·`generate_alias_name` 해석
- `profiles.yml`과 target 스키마 읽기. 레포 밖에 있거나 비밀 정보가 섞이기 때문이다.
- `meta`, `tags`, `{% docs %}` 본문, exposures, metrics, semantic models
- 테스트 실행이나 품질 판정
- 컬럼 단위 lineage. 1.3.0에서 한다.

## 전제

- dbt 프로젝트 찾기는 기존 `dbt.build()`를 그대로 쓴다. `dbt_project.yml`이 있는 폴더가 프로젝트이고, 모델은 `model-paths`·`snapshot-paths` 아래의 파싱된 `.sql` 파일이다.
- 속성 파일의 모델 이름과 모델 파일의 연결은 기존 `dbt.resolve_name()`(`rule: dbt_name`)을 그대로 쓴다.
- 실제 스키마는 실행 환경(target)에 따라 달라지므로 레포만으로는 알 수 없다. 그래서 이 기능이 만드는 모델 테이블 이름은 모두 스키마 자리가 변수이고, 다른 SQL이나 데이터와는 **추정**으로만 연결된다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `langs/yaml_reader.py` (수정) | `scalars(text, block_scalars=False)`. `True`이면 블록 스칼라 내용을 값으로 낸다. | 없음 |
| `langs/yaml_dbt.py` (수정) | 속성 파일의 모델·컬럼·테스트·source 속성을 추출한다. `dbt_project.yml`의 폴더 설정 `+alias`·`+schema`도 추출한다. | `yaml_reader` |
| `langs/dbt_config.py` (신규) | 모델 SQL의 `{{ config(...) }}`에서 `alias`·`schema` 리터럴을 읽는다. | 없음 |
| `langs/sql.py` (수정) | `extract` 결과에 `dbt_config`를 더한다. | `dbt_config` |
| `langs/sql_tables.py` (수정) | `source()`에서 온 읽기 레코드에 `source: [src, table]`을 표시한다. | 없음 |
| `dbt.py` (수정) | 모델별 테이블 이름 조립(`model_table`)과 source 해석(`resolve_source`)을 한다. | `Project` |
| `graph.py` (수정) | `written_tables`에 모델 테이블을 더하고, source 읽기의 이름을 바꾸고, `dbt_models`·`dbt_sources`·`dbt_docs_unlinked`를 만든다. | `dbt` |
| `report_data.py` (수정) | 테이블 블록에 문서와 데이터의 비교, 테스트 단서를 그린다. | `graph.json`, `profile.json` |
| `i18n.py` (수정) | 새 문구의 한국어 | 없음 |

`report.py`는 이미 800줄 가까이 된다. 새 표시 코드는 `report_data.py`에 둔다. `report_data.py`가 300줄을 넘으면 문서 비교 부분을 `report_docs.py`로 나눈다.

## 여러 줄 텍스트 읽기

`yaml_reader.scalars(text, block_scalars=True)`는 다음을 읽는다.

- `|`는 줄바꿈을 유지하고, `>`는 줄을 공백으로 접는다. 빈 줄은 줄바꿈 하나가 된다.
- 끝 줄바꿈 표시 `-`(모두 제거), `+`(모두 유지), 표시 없음(하나만 남김)을 따른다.
- 들여쓰기 숫자 표시(`|2`, `>-1`)를 따른다. 숫자 표시가 없으면 첫 내용 줄의 들여쓰기가 본문 들여쓰기이다.
- 본문은 본문 들여쓰기 이상인 줄과 빈 줄이다. 그보다 얕은 첫 줄에서 끝난다. 본문 안의 `#`은 주석이 아니다.
- 값의 줄 번호는 키가 있는 줄이다. 값은 2,000자에서 자르고, 자르면 그 스칼라를 `truncated`로 알린다.
- 앵커·태그 처리와 템플릿 줄의 `degraded` 표시는 기존과 같다.

`block_scalars`의 기본값은 `False`이다. 끄면 지금과 결과가 완전히 같다. 그래서 Compose, GitHub Actions, Helm, 경로 값 연결 결과는 바뀌지 않는다. 이 옵션은 dbt 속성 파일을 읽을 때만 켠다. `yaml_dbt`가 dbt 파일인지 판단한 뒤 다시 읽는다.

## 모델이 쓰는 테이블 이름

모델 파일마다 다음을 정한다.

| 항목 | 우선순위(위에서부터 처음 맞는 것) |
|---|---|
| alias | ① 모델 SQL의 `{{ config(alias='x') }}` ② `dbt_project.yml` 폴더 설정의 `+alias`(모델 파일을 담는 가장 깊은 폴더의 설정) ③ 파일 이름(확장자 제외). 버전 모델 `orders_v2.sql`은 `orders_v2`이다. |
| schema | ① `config(schema='marts')` ② 폴더 설정의 `+schema`(가장 깊은 폴더) ③ 없음 |

- 이름은 소문자로 정규화한다. 테이블 이름은 schema가 없으면 `{}.<alias>`, 있으면 `{}_<schema>.<alias>`이다. dbt 기본 `generate_schema_name`이 target 스키마 뒤에 `_<schema>`를 붙이는 방식과 같다.
- 폴더 설정은 `models:` 아래 `<프로젝트 이름>:` 다음의 폴더 키 경로를 `model-paths` 기준 하위 폴더와 맞춘다. `snapshots:`도 같다. `+` 없는 `alias:`·`schema:` 키(옛 문법)도 같은 뜻으로 읽는다.
- alias가 리터럴이 아니면(`config(alias=var('x'))`) 그 모델은 `dynamic: true`이고 `written_tables`에 넣지 않는다. schema만 리터럴이 아니면 `{}.<alias>`로 둔다.
- `config(...)`가 `{% if %}`·`{% for %}` 블록 안에 있으면 분기마다 다를 수 있다. 그 모델은 `dynamic: true`이다.
- 같은 파일에 `config(...)`가 여러 번 나오면 마지막 값을 쓴다.
- `written_tables`에 `{"table": "{}_marts.orders", "from": "file:models/marts/orders.sql", "line": N, "via": "dbt:model"}`을 더한다. `line`은 config가 있으면 그 줄이고, 없으면 1이다.
- 정렬과 중복 제거는 기존 `written_tables` 규칙(`(table, from, line)` 순, 첫 줄)을 따른다. 기존 항목에는 `via` 키가 없다. 추가 키이다.

이렇게 하면 기존 연결 규칙이 그대로 적용된다. dbt 밖의 SQL이 `analytics_marts.orders`를 읽으면 "스키마 변수" 규칙(`rule: table_name`)으로 이 모델 파일에 추정 연결된다. 1.1.0 데이터 프로파일의 `orders` 데이터셋도 테이블 이름 규칙으로 추정 연결된다. dbt 모델끼리는 기존 `dbt:ref` edge가 이미 있으므로, 같은 쌍에 대한 테이블 이름 `READS` edge는 만들지 않는다.

## source 이름 해석

- 형태: `sources: - name: raw, schema: raw_data, database: lake, tables: - name: events, identifier: ev_v2`
- `{{ source('raw', 'events') }}`가 읽는 테이블은 `[database.]schema.identifier`이다. `schema`가 없으면 source 이름을, `identifier`가 없으면 테이블 이름을 쓴다. 소문자로 정규화한다.
- 찾는 순서는 기존 `ref()`와 같다. 그 SQL 파일이 속한 dbt 프로젝트의 정의를 먼저 보고, 다음으로 로컬 패키지를 본다.
- 정의가 없으면 지금처럼 `raw.events`이다. 같은 순서 단계에 정의가 둘 이상이면 기본 이름을 쓰고 `dbt_sources`에 `ambiguous`로 남긴다.
- `sql_tables`는 `source()`에서 온 읽기 레코드에 `source: ["raw", "events"]`를 표시한다. `graph.py`가 읽기를 해석하기 전에 이 레코드들의 `table`을 바꾼다. 해석 결과가 외부 테이블이면 `external_tables`에 바뀐 이름으로 나온다.

## 속성 파일에서 읽는 것

대상은 `dbt_project.yml`과 `packages.yml`·`dependencies.yml`이 아닌, dbt 프로젝트 안의 YAML 파일이다. 그 안의 `models:`·`snapshots:`·`seeds:`·`sources:` 최상위 키를 읽는다.

- 모델 수준
  - `name`, `description`
  - `{{ doc('x') }}`만 있는 설명이면 `doc: "x"`
  - `tests`와 `data_tests` 둘 다 읽는 모델 테스트
- 컬럼 수준: `columns: - name, description, data_type, tests | data_tests`
- 테스트 한 항목
  - 문자열이면 `{"name": "not_null"}`이다. `dbt_utils.unique_combination_of_columns`처럼 패키지 이름이 붙은 것도 이름 그대로 둔다.
  - 매핑이면 키가 이름이다. 핵심 인자만 남긴다.
    - `accepted_values`: `values` 앞 10개와 `values_count`
    - `relationships`: `to`(`ref('x')`면 `{"ref": "x"}`, `source('a','b')`면 `{"source": ["a", "b"]}`, 그 밖이면 `{"dynamic": true}`)와 `field`
    - 그 밖의 테스트: 이름만
  - 인자는 `arguments:` 아래 있어도 같은 키로 읽는다(dbt 1.10+ 문법).
- source 수준: `name`, `schema`, `database`, `description`, `tables: - name, identifier, description, columns`
- 설명은 2,000자에서 자른다. 줄 번호는 항목의 `name:` 줄이다.

## `graph.json` 추가 키

기존 키는 바뀌지 않는다. `SCHEMA_VERSION`은 `"1.0"` 그대로이다.

```json
"dbt_models": [{
  "model": "orders", "kind": "models", "project": "jaffle",
  "file": "file:models/marts/orders.sql", "table": "{}_marts.orders", "dynamic": false,
  "alias_from": "config", "schema_from": "project",
  "description": "주문 한 건이 한 행", "doc": null,
  "tests": [{"name": "dbt_utils.expression_is_true"}],
  "columns": [{"name": "order_id", "description": "...", "data_type": "bigint",
               "tests": [{"name": "not_null"}, {"name": "unique"}]}],
  "defined_in": {"path": "models/marts/schema.yml", "line": 12},
  "duplicates": []
}],
"dbt_sources": [{
  "source": "raw", "table": "events", "resolved": "lake.raw_data.ev_v2",
  "description": "...", "columns": [...],
  "defined_in": {"path": "models/staging/sources.yml", "line": 3},
  "status": "resolved"
}],
"dbt_docs_unlinked": [{"name": "old_model", "kind": "models", "defined_in": {"path": "...", "line": 40}}]
```

- `dbt_models`에는 dbt 프로젝트의 모든 모델과 스냅숏 파일이 들어간다. 속성 파일에 없는 모델은 `description: null`, `columns: []`, `defined_in: null`이다. 정렬은 `file` 순이다.
- `alias_from`과 `schema_from`은 `config`, `project`, `default` 중 하나이다. 무엇이 이름을 정했는지 보이기 위한 값이다.
- 같은 모델이 속성 파일에 두 번 나오면 처음 것을 쓰고, 나머지 위치를 `duplicates`에 남긴다.
- `dbt_sources`의 `status`는 `resolved`(정의를 찾음) 또는 `ambiguous`이다. 정의가 없는 source 호출은 이 목록에 들어가지 않는다. 그 호출은 기존 읽기 레코드로 남는다.
- 모델 파일과 연결되지 않은 속성 항목(`not_found`)은 `dbt_docs_unlinked`에 넣는다.
- dbt 프로젝트가 없는 레포에서는 세 키가 모두 빈 목록이다.
- 같은 입력이면 바이트 단위로 같다.

## 리포트

1.1.0의 "Tables" 구역 테이블 블록을 다음처럼 바꾼다.

1. **블록을 그리는 조건.** 지금은 `profile.json`에 맞춘 테이블만 블록이 있다. 1.2.0에서는 `dbt_models`에 있는 테이블도 블록을 그린다. 프로파일이 없으면 문서 정보만 보인다.
2. **요약 줄.** 테이블 이름 옆에 `dbt` 표시와 모델 설명의 첫 문장(120자까지)을 붙인다.
3. **컬럼 표.** 문서 컬럼과 데이터 컬럼을 이름으로 합친다. 대소문자는 무시한다.
   - 열 구성: 이름, 문서 타입(`data_type`), 데이터 타입, 설명, 테스트, 기존 데이터 지표(null %, 고윳값, 범위·길이, 모양)
   - 문서에만 있는 컬럼: "데이터에 없음"
   - 데이터에만 있는 컬럼: "문서에 없음"
   - 타입 차이: `data_type`을 1.1.0 타입으로 바꿔 비교하고, 다르면 표시한다. 대응표는 아래와 같다. 대응표에 없는 타입은 비교하지 않는다.

     | `data_type` | 1.1.0 타입 |
     |---|---|
     | `int`, `integer`, `bigint`, `smallint`, `tinyint`, `int64` | integer |
     | `numeric`, `decimal`, `number`, `float`, `float64`, `double`, `real` | decimal |
     | `varchar`, `char`, `string`, `text` (괄호 인자 무시) | string |
     | `date` | date |
     | `timestamp`, `timestamp_ntz`, `timestamp_tz`, `timestamp_ltz`, `datetime` | timestamp |
     | `bool`, `boolean` | boolean |

4. **테스트 단서.** "표본에서 본 것이며 판정이 아님"이라는 문구와 함께, 개수와 비율만 보이고 값은 보이지 않는다.
   - `not_null`인데 표본의 null 비율이 0보다 크면: "표본에서 null 3.2%"
   - `unique`인데 `distinct_capped`가 아니고 고윳값이 읽은 행 수보다 적으면: "표본에서 중복 있음"
   - 민감 컬럼에도 개수와 비율은 보인다. 값은 원래 없다.
5. **설명 텍스트.** 모든 설명과 테스트 인자는 기존 이스케이프 함수를 거친다. 설명 안의 지시문은 데이터로만 다룬다.
6. **링크.** 모델 파일과 속성 파일(`defined_in`)은 기존 파일 링크로 건다.
7. **기존 블록.** dbt가 아닌 테이블의 블록은 1.1.0과 같다.

## 오류 처리

| 상황 | 처리 |
|---|---|
| 속성 파일의 템플릿 줄(`{{ }}`, `{% %}`) | 기존처럼 그 파일을 `degraded`로 표시하고, 읽은 부분만 쓴다. |
| 같은 모델이 속성 파일에 두 번 | 처음 것을 쓰고 `duplicates`에 남긴다. |
| 모델 파일과 연결되지 않는 속성 항목 | `dbt_docs_unlinked` |
| `relationships.to`가 리터럴이 아님 | `{"dynamic": true}` |
| `config()` 인자가 리터럴이 아니거나 분기 안 | `dynamic` 규칙(위) |
| 블록 스칼라가 2,000자를 넘음 | 자르고 `truncated` |
| dbt 프로젝트가 없음 | 새 키는 빈 목록이고, 나머지 출력은 그대로이다. |

## 테스트

- 단위 테스트
  - `yaml_reader` 블록 스칼라
    - `|`, `>`, `-`, `+`, 숫자 표시
    - 빈 줄, `#`이 든 본문, 들여쓰기가 끝나는 지점
    - 옵션을 끄면 기존 출력과 같은지
  - `dbt_config`: 리터럴(작은따옴표·큰따옴표), 비리터럴, `{% if %}` 안, 여러 번 나올 때
  - 폴더 설정 우선순위와 `+` 없는 옛 문법
  - 테이블 이름 조립: alias·schema 조합, 버전 모델, dynamic
  - source 해석
    - 프로젝트를 먼저, 다음으로 패키지
    - schema·identifier·database 기본값
    - ambiguous
  - 속성 추출
    - tests와 data_tests, 흐름 표기 `values`, `arguments:` 문법
    - `doc()`, 중복 항목, 연결되지 않은 항목
  - `graph.json`
    - 새 키
    - `written_tables`의 `via`
    - source 읽기의 이름 바꾸기
    - dbt 없는 레포에서 출력이 그대로인지
  - 리포트
    - 문서만 있는 테이블 블록
    - 비교 표시(문서에 없음, 데이터에 없음, 타입 차이)
    - 테스트 단서
    - 이스케이프
    - 한국어 문구
- fixture: `evals/fixtures/dbt-docs/`(합성)
  - 폴더 `+schema`, `config(alias=)`, 버전 모델 두 개
  - `sources.yml`(schema·identifier·database가 있는 것과 없는 것)
  - 여러 줄 설명, 패키지 테스트, `relationships`
  - dbt 밖에서 모델 테이블을 읽는 SQL 하나
  - `expected_graph.json`에 `dbt_models`(model, table, dynamic, 컬럼 이름, 테스트 이름)와 `dbt_sources`(source, table, resolved, status)의 정답을 적는다.
- 채점: `evals/score.py`의 정확 비교 집합에 `dbt_models`와 `dbt_sources`를 더한다. 기존 IMPORTS 정밀도·재현율과 `score_profile.py`의 결과는 그대로여야 한다.
- Python 3.9: 커밋 전에 `uv run --no-project --python 3.9 --with pytest python -m pytest -q -p no:cacheprovider`

## 문서와 버전

- `arche_lib/__init__.py`: `__version__ = "1.2.0"`
- `docs/release-notes.md`: 맨 위에 `## 1.2.0` 절
- `docs/artifact-compatibility.md`: `graph.json`의 `dbt_models`, `dbt_sources`, `dbt_docs_unlinked`, `written_tables[].via`가 추가 키라는 것
- `skills/arche/SKILL.md`: 다음을 적는다.
  - dbt 레포에서 모델 테이블 이름은 스키마 자리가 변수라서, 다른 SQL이나 데이터와의 연결은 추정이라는 것
  - 문서와 데이터의 비교를 읽는 법
  - 테스트 단서는 판정이 아니라는 것
- `README.md`: 기능 한 줄
- 병합 뒤 `v1.2.0` 태그로 릴리스한다. 태그 push는 공개 릴리스이므로 사용자 확인을 받는다.

## 이후 단계로 미루는 것

- 1.3.0: 컬럼 단위 lineage([브리프](../plans/2026-11-26-v1.3-column-lineage-brief.md))
  - 이 스펙의 `dbt_models[].columns`(문서 컬럼 목록)와 `dbt_models[].table`을 입력으로 쓴다.
- 커스텀 `generate_schema_name`, target 스키마 옵션
- `meta`, `tags`, doc 블록 본문, exposures
