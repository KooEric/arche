# 로컬 데이터 프로파일 설계

2026-11-25 확정. 대상 브랜치는 `erkoo/data-profile`(기준: `main` b63da18)이다. 이 작업이 들어가면 Arche는 **1.1.0**이 된다. 데이터 기능의 1단계이며, 컬럼 단위 lineage·메타데이터(2단계)와 웨어하우스 덤프·실행 로그(3단계)는 이후 버전에서 다룬다.

## 왜 바꾸는가

[SQL 테이블 lineage](2026-11-23-table-lineage-design.md)와 dbt `ref()` 연결로 Arche는 어느 SQL이 어느 테이블을 쓰고 읽는지 안다. 그 테이블의 실제 데이터가 어떻게 생겼는지는 모른다. 데이터 엔지니어가 새 소스를 받거나 테이블 변경을 검토할 때 필요한 것은 둘을 합친 그림이다. "이 테이블은 이 SQL이 쓰고 저 DAG가 읽는데, 데이터에는 이 컬럼이 있고 이 컬럼은 키다."

## 목표와 범위

**목표.** 테이블 하나에 대해 리포트 한 곳에서 다음 세 가지에 답한다.

1. 이 테이블은 어느 파일이 쓰고 어느 파일이 읽는가. 이것은 기존 lineage가 답한다.
2. 실제 데이터는 어떻게 생겼는가. 컬럼, 추정 타입, null, distinct, 후보 키로 답한다.
3. 코드가 아는 테이블 중 데이터가 없는 것은 무엇이고, 데이터는 있지만 코드가 모르는 것은 무엇인가.

**이번 범위.**

1. 레포 밖 데이터 폴더(`--data DIR`, 여러 번 지정 가능)에서 CSV, TSV, JSON, JSONL·NDJSON, Parquet 데이터셋을 찾는다.
2. 데이터셋마다 행 수 상한까지 스트리밍으로 읽어 컬럼 프로파일을 만든다.
3. 데이터셋을 lineage의 테이블 이름에 맞춘다. 매핑 파일로 맞춘 것은 확정, 경로 규칙으로 맞춘 것은 추정이다.
4. 기본 출력에는 집계만 넣는다. 값은 옵트인이고, 민감해 보이는 컬럼은 옵트인해도 가린다.
5. `profile.json`을 쓰고, 리포트의 "Tables" 구역에 테이블별 데이터 상세를 붙인다.

**하지 않는 것.**

- 웨어하우스 직접 접속(BigQuery, Snowflake, Hive metastore)
- 컬럼 단위 lineage, SQL의 SELECT 컬럼 해석
- 품질 규칙 검사(`not_null`·`unique` 판정), 두 데이터셋 비교
- 데이터 쓰기·수정·변환
- 압축 파일(`.gz`, `.zst`, `.snappy` 단일 파일), Excel, Avro, ORC
- 레포 안 데이터 파일(dbt seeds 등)의 자동 프로파일. `--data`로 레포 안 폴더를 주는 것도 거부한다(아래 "읽기 전용" 참고).
- `map.html`에 데이터 표시. 이번에는 `report.html`만 바꾼다.

## 전제

- 사용자는 테이블 데이터를 레포 밖 폴더에 내려받아 둔다. 랜딩 폴더, 웨어하우스 export, 테스트 추출본 같은 것이다.
- 폴더 구조는 대개 `schema/table/…`, `schema.table.ext`, `table.ext` 중 하나이다. Spark·Hive 형식의 `key=value` 파티션 폴더와 `part-00000…` 파일을 포함한다.
- 데이터에는 개인정보가 섞일 수 있고, `report.html`은 공유될 수 있다.
- 표준 라이브러리만 쓴다는 원칙은 그대로이다. Parquet 행 읽기만은 사용자의 환경에 `pyarrow`나 `duckdb`가 이미 있으면 빌려 쓴다. Arche는 이들을 설치하지 않는다.

## 파이프라인

```
scan → imports → data_profile(신규, 선택) → map → render
```

- `data_profile.py --out RUN --data DIR [--data DIR2] [--map data-map.json] [--max-rows N] [--show-values]`는 `RUN/profile.json`을 쓴다. 스크립트 이름을 `profile.py`로 하지 않는 것은 표준 라이브러리 `profile` 모듈(cProfile이 가져온다)을 스크립트 폴더가 가리기 때문이다.
- `arche.py`에 `--data`, `--data-map`, `--max-rows`, `--show-values`를 더한다. `--data`가 있으면 imports 다음에 data_profile을 실행한다. `--data`가 없으면 그 단계를 실행하지 않고, 기존 출력은 바이트 단위로 같다.
- `render.py`는 `RUN/profile.json`이 있으면 읽는다. 없으면 지금과 같다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `scripts/data_profile.py` (신규 CLI) | 인자 검사, 각 단계 호출, `profile.json`과 `run.json` 기록 | 아래 모듈 |
| `arche_lib/data/__init__.py` | 패키지와 상수(`PROFILE_VERSION = 1`, 상한값) | 없음 |
| `arche_lib/data/discover.py` | 데이터 폴더를 걸어 데이터셋 목록과 지문을 만든다 | `paths` |
| `arche_lib/data/readers.py` | 형식별 행 이터레이터: CSV/TSV, JSON, JSONL | stdlib `csv`, `json` |
| `arche_lib/data/parquet_meta.py` | Parquet 푸터(Thrift compact)에서 스키마와 전체 행 수를 읽는다 | 없음 |
| `arche_lib/data/parquet_rows.py` | `pyarrow` 또는 `duckdb`가 있을 때 Parquet 행을 읽는다. `importlib`로만 가져온다 | 선택 |
| `arche_lib/data/stats.py` | 컬럼별 스트리밍 집계, 타입 추정, 패턴, 민감 판정 | 없음 |
| `arche_lib/data/match.py` | 데이터셋과 테이블 이름 맞추기, 매핑 파일 읽기 | `jsonio` |
| `arche_lib/data/profiler.py` | 데이터셋 하나를 읽어 상태·행 수·컬럼 요약을 만든다 | 위 모듈 |
| `arche_lib/graph.py` (수정) | 추가 키 `written_tables`를 기록한다 | `lineage` |
| `arche_lib/report_data.py` (신규) | 리포트의 테이블별 데이터 상세 HTML | `report`의 공용 함수 |
| `arche_lib/report.py` (수정) | "Tables" 구역에서 `report_data`를 부른다 | `report_data` |
| `arche_lib/i18n.py` (수정) | 새 문구의 한국어 | 없음 |

`report.py`는 이미 725줄이다. 데이터 상세를 그 안에 넣으면 800줄을 넘으므로 새 모듈로 뺀다.

`tests/test_stdlib_only.py`는 모든 `import` 문을 검사한다. `parquet_rows.py`는 `importlib.import_module("pyarrow.parquet")`처럼 문자열로 가져와 이 검사를 통과하고, 실패하면 `None`을 돌려준다.

## 테이블 목록: `graph.json`의 `written_tables`

지금 `graph.json`에는 읽는 쪽이 있는 테이블(`READS` edge의 `table`)과 `external_tables`만 남는다. 쓰기만 하고 아무도 읽지 않는 테이블(최종 마트 등)은 보이지 않는다. 그래서 `build_graph`가 `_written_tables`의 결과를 추가 키로 남긴다.

```json
"written_tables": [{"table": "accounting.ledger_daily", "from": "file:sql/ledger.sql", "line": 3}]
```

- `temp`와 `dynamic` 쓰기는 기존 색인과 마찬가지로 넣지 않는다.
- 정렬 순서는 `(table, from, line)`이다. 같은 파일·테이블 쌍은 첫 줄만 남긴다.
- dbt 모델은 이미 SQL 쓰기 레코드가 아니므로 이번에는 넣지 않는다. dbt 모델 이름을 테이블로 보는 일은 2단계에서 다룬다.
- 추가 키이므로 `SCHEMA_VERSION`은 `"1.0"` 그대로이다. `docs/artifact-compatibility.md`에 적는다.

프로파일이 맞춰 보는 테이블 집합은 `written_tables`와 `external_tables`의 `table`을 합친 것이다.

## 데이터셋 찾기 (`discover`)

데이터 폴더마다 이름순으로 걷는다. 데이터 폴더에는 순번 `d0`, `d1`, …이 붙는다.

**데이터 파일.** 확장자로 정한다.

| 확장자 | 형식 |
|---|---|
| `.csv` | `csv` |
| `.tsv`, `.tab` | `tsv` |
| `.json` | `json` |
| `.jsonl`, `.ndjson` | `jsonl` |
| `.parquet`, `.parq` | `parquet` |

**건너뛰는 것.** 이름이 `.`이나 `_`로 시작하는 파일과 폴더(`_SUCCESS`, `_delta_log`, `.crc` 포함), 심볼릭 링크, 위 확장자가 아닌 파일, 크기가 0인 파일. 건너뛴 수는 이유별로 센다.

**묶음 데이터셋.** 폴더 `D`가 다음 중 하나에 해당하면 `D` 전체를 데이터셋 하나로 보고, 그 아래는 더 쪼개지 않는다.

1. `D` 아래(깊이 상관없이)의 모든 데이터 폴더 이름이 `key=value` 형태이고, 그 안의 데이터 파일 형식이 하나이다. 파티션 키는 바깥부터 순서대로 기록한다.
2. `D`가 바로 담은 데이터 파일이 둘 이상이고, 모두 같은 형식이며, 하위 폴더가 없고, 모든 파일 이름이 `part-*`이거나 숫자로 끝나는 같은 접두사(`ledger_000.csv`, `ledger_001.csv`)이다.
3. `D`에 `_SUCCESS`가 있고 데이터 파일 형식이 하나이다.

해당하지 않으면 파일 하나가 데이터셋 하나이다.

**데이터셋 레코드.**

```json
{"id": "d0:accounting/ledger_daily", "root": "d0", "path": "accounting/ledger_daily", "kind": "folder",
 "format": "parquet", "files": 12, "bytes": 48213, "partitions": ["dt"], "fingerprint": "<sha256>"}
```

- `path`는 데이터 폴더 기준 POSIX 상대 경로이다. 파일 데이터셋이면 확장자까지 포함한다.
- `fingerprint`는 포함 파일의 `(상대 경로, 크기, mtime_ns)`를 정렬해 이은 문자열의 SHA-256이다. 내용 해시는 만들지 않는다. 큰 데이터를 두 번 읽지 않기 위해서이다.
- 데이터셋은 최대 2,000개, 파일은 최대 100,000개까지 센다. 넘으면 멈추고 `truncated: true`와 함께 기록한다.

## 읽기 (`readers`, `parquet_meta`, `parquet_rows`)

모든 리더는 `Iterator[Dict[str, Any]]`를 내놓는다. 프로파일은 데이터셋마다 `--max-rows`(기본 100,000)행에서 멈춘다. 묶음 데이터셋은 파일을 이름순으로 이어 읽고, 파티션 값을 컬럼으로 더한다.

**CSV/TSV.**

- 인코딩은 `utf-8-sig`로 읽고 `errors="replace"`를 쓴다. 바뀐 글자가 있는 행 수를 `encoding_errors`로 센다.
- `.csv`의 구분자는 첫 64KB에 `csv.Sniffer().sniff(sample, delimiters=",;|\t")`를 적용해 정하고, 실패하면 `,`를 쓴다. `.tsv`는 항상 탭이다.
- 첫 행을 헤더로 본다. 빈 이름은 `column_N`(1부터)으로, 중복 이름은 `name_2`, `name_3`으로 바꾼다. 원래 이름은 `header_fixed`에 남긴다.
- 필드 수가 헤더와 다른 행은 `malformed_rows`로 센다. 그런 행도 앞쪽 필드는 집계한다.
- 필드 하나의 최대 길이는 1MB(`csv.field_size_limit`)이다. 넘으면 그 파일은 `status: "error"`, `reason: "field_too_large"`이다.
- 묶음 데이터셋에서 파일마다 헤더가 다르면 컬럼을 합집합으로 만들고 `schema_drift: true`를 기록한다.

**JSON.**

- 파일이 64MB를 넘으면 `status: "skipped"`, `reason: "json_too_large"`이다. 큰 데이터는 JSONL을 쓰라고 안내한다.
- 최상위가 객체의 배열이면 원소 하나가 행 하나이다. 최상위가 객체 하나이면 행 하나이다. 그 밖의 경우는 `reason: "not_records"`이다.

**JSONL.** 줄 하나가 객체 하나이다. 빈 줄은 건너뛰고, 객체가 아니거나 해석되지 않는 줄은 `malformed_rows`로 센다.

**중첩.** JSON 계열의 중첩 객체는 `a.b.c`로 펼친다. 깊이는 3까지이고, 더 깊은 값은 `object` 타입 하나로 본다. 배열은 펼치지 않고 `array` 타입으로 본다.

**Parquet 메타데이터 (stdlib).**

- 파일 끝 8바이트가 `<4바이트 LE 길이>PAR1`이고 앞 4바이트가 `PAR1`인지 확인한다. 아니면 `reason: "not_parquet"`이다.
- 푸터 길이는 16MB 이하여야 한다. 넘으면 `reason: "footer_too_large"`이다.
- `FileMetaData`를 Thrift compact protocol로 해석한다. 필요한 필드는 `num_rows`(3)와 `schema`(2)이고, `SchemaElement`에서는 `name`, `type`, `converted_type`, `logicalType`, `num_children`, `repetition_type`을 읽는다. 모르는 필드는 형식만 보고 건너뛴다.
- 해석기는 길이와 중첩 깊이(최대 64)를 검사하고, 어긋나면 `ParseError`를 낸다. 신뢰할 수 없는 입력이라는 전제로 짠다.
- 결과로 잎 컬럼의 경로·물리 타입·논리 타입, 전체 행 수를 얻는다. 묶음 데이터셋은 파일마다 읽어 행 수를 더하고, 스키마가 다르면 합집합과 `schema_drift: true`를 남긴다.

**Parquet 행 (선택).**

- `pyarrow.parquet`이 있으면 `ParquetFile(path).iter_batches(batch_size=8192)`로 읽는다. 없고 `duckdb`가 있으면 `duckdb.connect(":memory:")`에서 `SELECT * FROM read_parquet(?, hive_partitioning=false) LIMIT ?`를 실행하고 `fetchmany`로 받는다. 파티션 값은 Arche가 폴더 이름에서 직접 더한다.
- 둘 다 없으면 메타데이터만으로 `status: "schema_only"`, `reason: "no_reader"`를 기록한다. 컬럼 이름, 타입, 전체 행 수는 그대로 보인다.
- 어느 리더를 썼는지 `reader: "stdlib" | "library"`로 남긴다. 정확한 라이브러리 이름은 같은 입력이면 같은 profile.json이 나오도록 run.json에만 기록한다.

## 컬럼 지표 (`stats`)

컬럼마다 한 번 훑는 집계기를 둔다. 기억하는 것은 개수와 해시, 짧은 문자열뿐이다.

| 지표 | 정의 |
|---|---|
| `type` | 값별 추정 타입 중 null이 아닌 값의 95% 이상을 차지하는 타입. 그런 타입이 없으면 `mixed`이고, `types`에 비율 상위 3개를 남긴다 |
| `null_ratio` | null 수 / 읽은 행 수. 소수 넷째 자리에서 반올림 |
| `distinct` | 서로 다른 값의 수. 값의 SHA-1 해시를 10,000개까지 모아 센다. 넘으면 `distinct: 10000`, `distinct_capped: true`이다 |
| `length` | 문자열 값 길이의 `[min, max]` |
| `min`, `max` | `integer`, `decimal`, `date`, `timestamp` 컬럼만 해당. 비교는 타입에 맞게 하고, 출력은 문자열이다 |
| `patterns` | 문자열 값의 모양 상위 5개와 개수. 아래 "패턴" 참고 |
| `candidate_key` | null이 0이고, `distinct_capped`가 아니며, distinct가 읽은 행 수와 같고, 읽은 행이 2 이상이면 `true` |
| `sensitive` | 아래 "값 보호" 참고 |
| `top_values` | `--show-values`일 때만. 빈도 상위 5개와 개수, 각 값은 40자에서 자름 |

**값별 타입.** CSV 값은 문자열에서 추정하고, JSON 값은 해석된 타입을 따른다.

- null: 빈 문자열, `NULL`, `null`, `\N`, `NaN`, `nan`(CSV), JSON `null`
- `boolean`: `true`/`false`(대소문자 무시), JSON bool
- `integer`: `^[+-]?\d{1,18}$`. 앞자리 0이 있는 두 자리 이상 값(`007`)은 `string`이다. 코드 값일 가능성이 높기 때문이다.
- `decimal`: `^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$`. `integer`와 `decimal`이 섞여 있으면 둘을 합쳐 `decimal`로 센다.
- `date`: `YYYY-MM-DD`로 `date.fromisoformat`이 받는 값
- `timestamp`: `YYYY-MM-DD[T ]HH:MM[:SS[.f]][Z|±HH:MM]`
- `object`, `array`: JSON 계열만 해당
- `bytes`(Parquet 행 리더)는 `binary`
- 그 밖은 `string`
- null이 아닌 값이 하나도 없는 컬럼의 `type`은 `unknown`이다.
- Parquet 행 리더가 준 값은 파이썬 타입에서 위 이름으로 바꾼다. Parquet 컬럼에는 메타데이터의 `physical_type`(예: `INT64`)과 `logical_type`(예: `DATE`)도 남긴다. `schema_only`이면 `type`은 이 두 값에서 정한다.

**패턴.** 영문 대문자는 `A`, 영문 소문자는 `a`, 숫자는 `9`, 그 밖의 문자(한글 포함)는 `?`로 바꾼다. 공백과 구두점은 그대로 둔다. 같은 글자가 4번 넘게 이어지면 `a{12}`처럼 줄인다. 결과는 40자에서 자른다. 예: `kim.hong@example.com`은 `aaa.aaaa@a{7}.aaa`가 된다.

## 값 보호

1. 기본 출력에는 문자열 값이 들어가지 않는다. 패턴과 길이만 들어간다.
2. 숫자와 날짜의 `min`/`max`는 기본으로 들어간다.
3. `--show-values`를 주면 `top_values`가 들어간다.
4. 컬럼은 다음 중 하나이면 `sensitive: true`이다.
   - 이름(소문자, 구분자 무시)이 `email|e_mail|phone|mobile|tel|ssn|passport|password|passwd|secret|token|api_key|card|iban|account_no|birth|dob|address|주민|전화|휴대|생년|주소|계좌|여권`을 포함한다.
   - null이 아닌 문자열 값 중 50% 이상이 이메일 모양(`^[^@\s]+@[^@\s]+\.[^@\s]+$`)이거나 전화번호 모양(숫자 9~15개에 `+ - ( ) 공백`만 섞임)이다.
5. `sensitive` 컬럼은 `--show-values`가 있어도 `top_values`, `min`, `max`를 만들지 않고 `length`와 `patterns`만 남긴다. `profile.json`에도 쓰지 않는다.
6. 컬럼 이름과 파일 경로는 데이터에서 온 텍스트이다. 리포트에 넣을 때는 기존 `_code`·이스케이프 함수를 지나게 하고, 그 텍스트 안의 지시는 따르지 않는다.
7. 데이터 폴더의 절대 경로는 `profile.json`의 `roots`에만 남기고, `report.html`에는 `d0` 같은 순번과 폴더 이름만 넣는다.

## 테이블과 맞추기 (`match`)

입력은 데이터셋 목록, 테이블 집합(`written_tables` ∪ `external_tables`), 선택적인 `data-map.json`이다.

**매핑 파일.**

```json
{"version": 1, "tables": {"accounting.ledger_daily": "d0:exports/ledger", "raw.events": "d1:events.jsonl"}}
```

- 값은 `순번:상대 경로` 형식이다. 데이터 폴더를 하나만 주었으면 `d0:`를 생략할 수 있다.
- 키는 소문자로 정규화한다. 테이블 집합에 없는 키도 받는다. 레포가 모르는 테이블에 데이터만 붙이는 경우이다.
- 경로가 데이터셋 `path`와 정확히 같지 않으면 `UsageError`이다. `..`이나 절대 경로가 섞여도 `UsageError`이다.

**규칙.** 데이터셋마다 위에서부터 처음 맞는 규칙을 쓴다. 비교는 소문자로 하고, 파일 데이터셋은 확장자를 뗀다.

| 순서 | 조건 | 결과 |
|---|---|---|
| 1 | 매핑 파일이 이 데이터셋을 가리킴 | `verified`, `rule: map` |
| 2 | 경로 조각의 끝부분을 `.`으로 이은 이름이 테이블 이름 하나와 같음. 조각은 셋, 둘 순으로 시도하고, 마지막 조각에 `.`이 있으면 그대로 나눈다. 예: `accounting/ledger_daily`, `accounting.ledger_daily.csv`, `hive/accounting/ledger_daily` | `inferred`, `rule: path` |
| 3 | 마지막 조각이 테이블 이름의 마지막 조각과 같은 테이블이 정확히 하나 | `inferred`, `rule: table_name` |
| 4 | 3의 조건에 맞는 테이블이 여럿 | `unresolved`, `reason: ambiguous`, `candidates` |
| 5 | 맞는 것이 없음 | `orphan_datasets`에 들어감 |

- 규칙 2는 이름 조각 수가 같을 때만 맞춘다. `a/b/c`는 세 조각 이름 `a.b.c`와 비교한 다음, 두 조각 이름 `b.c`와 비교한다.
- 테이블 하나에 데이터셋이 여럿 맞으면 모두 기록한다. 리포트는 `verified`를 먼저 보이고, 같은 상태끼리는 `id` 순으로 보인다.
- 맞는 데이터셋이 하나도 없는 테이블은 `tables_without_data`에 들어간다.
- 변수 자리가 있는 테이블 이름(`{}.ledger`)은 규칙 3에서만 쓴다.

## `profile.json`

```json
{
  "profile_version": 1,
  "inventory_sha256": "<graph.json과 같은 값>",
  "options": {"max_rows": 100000, "show_values": false, "data_map": true},
  "roots": [{"id": "d0", "path": "/abs/landing", "name": "landing"}],
  "datasets": [{"id": "...", "...": "discover 레코드",
                "status": "profiled | schema_only | skipped | error", "reason": "...",
                "reader": "stdlib", "rows_read": 100000, "rows_total": 2400000, "row_limit_hit": true,
                "malformed_rows": 0, "encoding_errors": 0, "schema_drift": false,
                "columns": [{"name": "id", "type": "integer", "null_ratio": 0.0, "distinct": 10000,
                             "distinct_capped": true, "min": "1", "max": "99812", "candidate_key": false,
                             "sensitive": false, "patterns": [], "length": null}]}],
  "matches": [{"table": "accounting.ledger_daily", "dataset": "d0:accounting/ledger_daily",
               "status": "inferred", "rule": "path"}],
  "unresolved": [{"dataset": "...", "reason": "ambiguous", "candidates": ["a.x", "b.x"]}],
  "orphan_datasets": ["d0:tmp/scratch.csv"],
  "tables_without_data": ["mart.revenue"],
  "skipped": {"hidden": 3, "symlink": 0, "unsupported": 5, "empty": 1},
  "truncated": false
}
```

- `rows_total`은 Parquet 메타데이터나 끝까지 읽은 경우에만 있다.
- CSV 헤더를 고친 경우 `header_fixed`에 `{새 이름: 원래 이름}`을 남긴다.
- 같은 입력(데이터 파일, 크기, mtime, 옵션)이면 바이트 단위로 같다. 시간, 리더 버전, 소요 시간은 `run.json`에만 남긴다.
- `render.py`는 `inventory_sha256`이 `graph.json`과 다르면 `IntegrityError`("profile.json describes a different snapshot; run data_profile.py again")를 낸다.

## 리포트

"Tables" 구역(`report.py`의 `_tables`)을 다음처럼 바꾼다. `profile.json`이 없으면 지금과 같다.

1. 맨 위에 요약 한 줄을 둔다. 프로파일한 데이터셋 수, 테이블에 맞춘 수, 데이터 없는 테이블 수, 코드가 모르는 데이터셋 수.
2. **Table data**: 맞춘 테이블마다 `<details>` 하나를 둔다. 요약 줄에는 테이블 이름, 상태(`verified`/`inferred` 배지), 행 수, 컬럼 수, 후보 키를 넣는다. 펼치면 다음이 나온다.
   - 쓰는 파일(`written_tables`)과 읽는 파일(`READS` edge의 `from`, `external_tables`의 `from`). 기존 파일 링크를 쓴다.
   - 컬럼 표: 이름, 타입, null %, distinct, 범위 또는 길이, 패턴. 키 컬럼에는 `key` 표시, 민감 컬럼에는 `sensitive` 표시를 단다.
   - 상태 메모: `schema_only`(Parquet 리더 없음), 행 상한 도달, `schema_drift`, `malformed_rows`.
3. **Tables without data**와 **Datasets the code does not name**: 목록 두 개를 둔다. 후자에는 `ambiguous` 후보도 함께 보인다.
4. 기존 External tables와 해석하지 못한 읽기 목록은 그 아래에 그대로 둔다.
5. 설명 문단에 "데이터는 상한까지 읽은 표본의 집계이며, 경로로 맞춘 테이블은 추정"이라고 적는다.

테이블 하나의 컬럼 표는 200개까지 그리고, 나머지는 "컬럼 N개 더"로 센다. 데이터셋은 최대 2,000개이므로 지연 렌더링은 두지 않는다.

## 읽기 전용과 안전

- `--data` 폴더가 대상 레포 안이거나, 출력 폴더 안이거나, 출력 폴더를 포함하면 `UsageError`이다(`paths.is_within`).
- 파일은 읽기 모드로만 연다. 심볼릭 링크는 열지 않는다. 걷는 동안 `os.scandir`의 `follow_symlinks=False`를 쓴다.
- 데이터셋을 읽은 뒤 지문을 다시 계산해 처음과 다르면, 실행 요약에 `data_changed_during_profile: true`를 남기고 그 데이터셋에 `status: "error"`, `reason: "changed_while_reading"`을 기록한다.
- 데이터 내용은 외부로 보내지 않는다. `pyarrow`·`duckdb`도 로컬 파일만 읽는다. duckdb에는 `:memory:` 연결을 쓰고, `discover`가 찾은 파일 경로만 매개변수(`?`)로 넘긴다. SQL 문자열에 경로나 데이터 값을 이어 붙이지 않는다.
- 한 데이터셋의 오류는 그 데이터셋에만 기록하고 다음 데이터셋으로 넘어간다. 인자 오류, 매핑 파일 오류, 스냅샷 불일치는 전체를 멈춘다.

## 오류 처리

| 상황 | 처리 |
|---|---|
| `--data` 경로 없음, 폴더 아님 | `UsageError` |
| `--data`가 레포·출력 폴더와 겹침 | `UsageError` |
| `graph.json` 없음 | `UsageError`("run imports.py first") |
| 매핑 파일 형식 오류, 없는 데이터셋 경로 | `UsageError`, 문제 키를 이름으로 알림 |
| 파일 하나의 해석 실패(CSV 필드 과대, JSON 오류, Parquet 푸터 손상) | 그 데이터셋 `status: "error"`와 `reason` |
| 권한 없음 | 그 데이터셋 `status: "error"`, `reason: "unreadable"` |
| Parquet 리더 없음 | `status: "schema_only"`, `reason: "no_reader"` |
| `pyarrow`·`duckdb`가 예외를 냄 | `status: "schema_only"`, `reason: "reader_failed"`, 예외 클래스 이름만 기록 |

`data_profile.py`는 다른 스크립트처럼 JSON 한 줄 요약을 낸다. 요약에는 `profile`, `datasets`, `profiled`, `schema_only`, `errors`, `matched_tables`, `tables_without_data`, `orphan_datasets`가 들어간다.

## 테스트

- **단위 테스트.**
  - `discover`: 묶음 규칙 세 가지, 건너뛰기, 상한, 지문 안정성
  - `readers`: 구분자 추정, 헤더 보정, 잘못된 행, 인코딩, JSON 형태별, 중첩 펼치기
  - `parquet_meta`: 테스트 안의 작은 Thrift compact 작성기로 만든 푸터, 손상·과대·깊이 초과 입력
  - `parquet_rows`: `sys.modules`에 가짜 `pyarrow.parquet`·`duckdb`를 넣어 연결을 확인하고, 둘 다 없을 때 `None`을 확인. 실제 라이브러리 시험은 `pytest.importorskip`로 둔다.
  - `stats`: 타입 경계(`007`, 18자리, 지수 표기), 95% 규칙, distinct 상한, 패턴, 민감 판정, 후보 키
  - `match`: 다섯 규칙과 매핑 파일 오류
  - `graph`: `written_tables`
  - `report_data`: 이스케이프, 민감 컬럼 값 미노출, `profile.json` 없음
- **CLI 테스트.** `data_profile.py`와 `arche.py --data`를 끝까지 실행하고 `report.html`에 테이블 상세가 나오는지 본다. 같은 입력을 두 번 실행해 `profile.json`이 같은지 본다. 레포 안 `--data`를 거부하는지 본다.
- **fixture.** `evals/fixtures/airflow-sql/` 옆에 `evals/fixtures/data-profile/`를 둔다. 안에 합성 레포(`repo/`), 합성 데이터 폴더(`data/`), `data-map.json`, 정답 `expected_profile.json`(맞춤, 해석하지 못한 데이터셋, 데이터 없는 테이블, 고아 데이터셋, 컬럼 타입·키·민감 컬럼, 심어 둔 개인정보 값)을 넣는다. 새 채점기 `evals/score_profile.py`가 정답과 정확히 비교하고 CI에서 `evals/score.py` 다음에 돈다. `evals/score.py`는 `expected_graph.json`이 있는 폴더만 읽으므로 이 fixture를 건너뛴다.
- **값 노출 검사.** fixture에 심어 둔 이메일·전화번호 문자열이 `--show-values` 유무와 상관없이 `report.html`과 `profile.json`에 나오지 않는지 확인한다.
- 시작 시점의 전체 테스트는 716개 통과이다.
- 이 설계의 코드는 임시 폴더에서 구현해 위 테스트(새 테스트 91개)와 두 채점기를 통과하는 것을 확인했다. pyarrow·duckdb는 설치되지 않은 환경이라, 실제 라이브러리 경로는 가짜 모듈로만 확인했다.

## 문서와 버전

- `arche_lib/__init__.py`: `__version__ = "1.1.0"`
- `docs/release-notes.md`: 맨 위에 `## 1.1.0` 절을 두고 데이터 프로파일, `written_tables`, 값 보호 규칙을 적는다.
- `docs/artifact-compatibility.md`: `profile.json`(`profile_version: 1`) 줄을 더하고, `graph.json`의 `written_tables`가 추가 키임을 적는다.
- `skills/arche/SKILL.md`: "Profile local data" 절을 둔다. 사용 시점, 명령, 매핑 파일, 값 보호, 결과 읽는 법(`inferred` 맞춤은 확인할 것)을 적는다.
- `README.md`: 기능 목록에 한 줄을 더하고 예시 명령을 넣는다.

## 이후 단계로 미루는 것

- 2단계(1.2): dbt 모델 이름을 테이블로 보기, `schema.yml` 설명과 테스트, SELECT 컬럼 해석, 컬럼 단위 영향
- 3단계(1.3): `INFORMATION_SCHEMA` 덤프 읽기, Airflow 실행 로그, 품질 판정, 두 데이터셋 비교
- `.gz` 단일 파일(stdlib `gzip`), `map.html`의 테이블 카드
