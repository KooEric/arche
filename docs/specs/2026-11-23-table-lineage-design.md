# SQL 테이블 lineage 설계

2026-11-23 제안. 대상 브랜치는 `erkoo/table-lineage`다. [DAG↔SQL 파일 참조](2026-11-23-dag-sql-references-design.md)(`erkoo/dag-sql-refs`, PR 14) 위에 쌓는다. PR 14가 먼저 병합되면 `main` 기준으로 옮긴다.

## 왜 바꾸는가

파일 참조 작업으로 DAG 파일에서 SQL 파일로 가는 연결은 생겼다. SQL 파일끼리는 여전히 연결이 없다. SQL 파일은 서로를 직접 가리키지 않고, 한 파일이 쓴 테이블을 다른 파일이 읽는 것으로 이어진다. 지금은 SQL·HQL 파일의 내용을 읽지 않으므로 이 연결이 보이지 않는다.

## 목표와 범위

**목표.** 두 질문에 근거 줄과 함께 답한다.

1. 이 SQL 파일을 바꾸면 어떤 SQL 파일과 DAG 파일이 영향을 받는가
2. 이 SQL 파일이 읽는 테이블은 어느 파일이 만드는가

여기에 한 가지를 더한다.

3. 이 폴더가 읽기만 하고 레포 안에서 만들지 않는 테이블은 무엇인가

**이번 범위.**

1. SQL·HQL 텍스트에서 테이블 읽기·쓰기를 뽑는다.
2. 읽는 파일에서 쓰는 파일로 가는 `READS` edge를 만든다.
3. 쓰는 파일이 없는 테이블을 `external_tables`로 기록하고 리포트에 보여 준다.

**하지 않는 것.**

- Python 문자열 안의 인라인 SQL
- 컬럼 단위 lineage
- `USE db;`로 정해지는 기본 스키마 해석
- 뷰 정의를 펼쳐 그 안의 테이블까지 따라가기
- `ALTER`, `DROP`, `TRUNCATE`
- 레포의 테이블 전체 목록
- 외부 SQL 파서 사용

## 전제

사용자가 확인한 대상 레포의 테이블 이름 형태는 다음과 같다.

| 형태 | 예 | 비중 | 처리 |
|---|---|---|---|
| A. 고정 이름 | `accounting.ledger_daily` | 대부분 | 확정 |
| B. 스키마만 변수 | `{{ params.schema }}.ledger_daily`, `${hiveconf:db}.ledger_daily` | 가끔 | 추정 |
| C. 테이블 이름에 변수 | `ledger_{{ ds_nodash }}`, `tmp_${run_id}` | 가끔 | `dynamic` |

엔진은 확인하지 못했다. Hive, Presto/Trino, Spark SQL의 공통 문법만 가정한다.

## 추출 방식

전체 문법을 해석하지 않는다. 주석과 문자열을 가린 텍스트에서 정해진 키워드 뒤의 이름만 읽는다. 표준 라이브러리만 쓴다는 제약 때문에 외부 파서는 쓰지 않는다. 미니 파서는 방언 차이를 모두 떠안아야 하고, 고정 이름이 대부분인 레포에서는 얻는 것이 적다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `langs/sql_tables.py` (신규) | SQL 텍스트를 가리고(mask) 테이블 읽기·쓰기 기록을 돌려준다. | 없음 |
| `langs/sql.py` (수정) | `extract` 결과에 `tables` 키를 추가한다. | `sql_tables` |
| `lineage.py` (신규) | 읽기 기록 하나를 쓰는 파일 색인과 맞춰 본다. 언어와 무관하다. | 쓰기 색인 |
| `graph.py` (수정) | `READS` edge와 `external_tables`를 만든다. | `lineage` |

기존 `lexer.py`는 `//`·`/* */` 주석과 JS 계열 문자열용이다. SQL의 `--` 주석, `'…'` 문자열, 백틱 이름, Jinja 태그는 `sql_tables.py` 안의 전용 마스커가 처리한다.

## 가리기(mask) 규칙

글자 위치와 줄바꿈을 유지한 채 다음을 공백으로 바꾼다.

- `-- …` 줄 주석과 `/* … */` 블록 주석
- `'…'` 문자열의 내용. `''`는 따옴표 하나로 본다.
- Jinja 블록 태그 `{% … %}`와 주석 `{# … #}`. 블록 안쪽의 SQL은 그대로 둔다.

다음은 가리지 않고 이름의 일부로 읽는다.

- 백틱 이름 `` `a`.`b` ``과 큰따옴표 이름 `"a"."b"`
- 변수 자리 `{{ … }}`, `${…}`, `$name`, `:name`

## 추출 규칙

`sql_tables.tables(path, text)`는 다음 레코드의 목록을 돌려준다.

```
{"table": "accounting.ledger_daily", "access": "read", "line": 12, "quote": "...",
 "dynamic": False, "temp": False}
```

**읽기.**

- `FROM` 뒤의 이름. `FROM a, b`처럼 쉼표로 이은 이름도 읽는다.
- `JOIN` 뒤의 이름. `LEFT`, `INNER`, `CROSS` 같은 수식어는 상관하지 않는다.
- 이름 뒤의 별칭(`AS x`, `x`)은 버린다.

**쓰기.**

- `INSERT INTO [TABLE] t`
- `INSERT OVERWRITE [TABLE] t`
- `CREATE [OR REPLACE] [EXTERNAL] [TEMPORARY|TEMP] TABLE [IF NOT EXISTS] t`
- `CREATE [OR REPLACE] [TEMPORARY|TEMP] VIEW [IF NOT EXISTS] t`
- `MERGE INTO t`
- `DELETE FROM t`. 이 `FROM`은 읽기로 세지 않는다.

**읽지 않는 것.**

- `FROM (`, `JOIN (`: 서브쿼리다. 안쪽의 `FROM`은 따로 읽힌다.
- 뒤에 `(`가 붙는 이름: `unnest(...)` 같은 테이블 함수다.
- `LATERAL VIEW …`: 테이블이 아니다.
- CTE 이름: `WITH x AS (`와 `, y AS (`로 선언된 이름은 그 파일에서 읽기 대상으로 치지 않는다.
- 함수 안의 `FROM`: `EXTRACT(DAY FROM dt)`, `SUBSTRING(x FROM 1)`, `TRIM(BOTH ' ' FROM x)`. 같은 괄호 깊이에서 그 괄호가 열린 뒤 `SELECT`나 `DELETE`가 나오지 않았으면 `FROM`을 무시한다. 괄호 밖(깊이 0)의 `FROM`은 항상 읽는다. Hive의 `FROM t INSERT …` 형태가 있기 때문이다.

**이름 정규화.**

- 백틱과 큰따옴표를 뗀다.
- 소문자로 바꾼다.
- 점으로 이은 조각은 세 개(`catalog.schema.table`)까지 받는다. 네 개 이상이거나 조각이 비면 레코드를 만들지 않는다.
- 변수 자리는 `{}`로 바꾼다. `{{ params.schema }}.ledger`는 `{}.ledger`가 된다.

**동적과 임시.**

- 마지막 조각(테이블 이름)에 `{}`가 있으면 `dynamic: True`다.
- 앞 조각에만 `{}`가 있으면 동적이 아니다. 연결 규칙에서 추정으로 처리한다.
- `CREATE TEMPORARY|TEMP TABLE|VIEW`로 만든 이름은 `temp: True`다.

같은 파일에서 같은 테이블을 같은 방식으로 여러 번 쓰면 첫 줄 하나만 남긴다.

## 연결 규칙

`graph.py`는 모든 SQL 파일의 쓰기 레코드로 색인을 만든다. `temp`와 `dynamic` 쓰기는 색인에 넣지 않는다. 색인은 두 개다.

- 전체 이름 → 쓰는 파일 목록
- 테이블 이름(마지막 조각) → 전체 이름 목록

`lineage.resolve(read, index)`는 읽기 레코드 하나에 대해 위에서부터 처음 맞는 규칙을 적용한다.

| 순서 | 조건 | 결과 |
|---|---|---|
| 0 | 읽기가 `dynamic` | `unresolved`, `reason: dynamic` |
| 1 | 같은 파일의 `temp` 쓰기와 이름이 같음 | 결과 없음(파일 안의 임시 테이블) |
| 2 | 이름에 변수 자리가 없고 전체 이름이 색인에 있음 | 쓰는 파일마다 `verified`, `rule: name` |
| 3 | 앞 조각이 변수이거나 없고, 테이블 이름이 색인의 전체 이름 하나와만 맞음 | 쓰는 파일마다 `inferred`, `rule: table_name` |
| 4 | 3과 같되 전체 이름 여럿과 맞음 | `unresolved`, `reason: ambiguous`, `candidates`에 전체 이름 |
| 5 | 맞는 것이 없음 | 외부 테이블 |

읽는 쪽과 쓰는 쪽이 모두 `{}.ledger`처럼 같은 변수 형태여도 규칙 2가 아니라 규칙 3으로 간다. 두 변수가 같은 값이라는 보장이 없으므로 확정하지 않는다.

규칙 2에서 쓰는 파일이 여럿이면 모두 실제로 쓰는 파일이다. `CREATE`하는 파일과 `INSERT`하는 파일이 따로 있는 경우가 그렇다. 모호한 것으로 보지 않고 각각 edge를 만든다.

읽는 파일과 쓰는 파일이 같으면 edge를 만들지 않는다.

## 산출물 형식

`graph.json`의 `edges`에 더한다.

```
{"kind": "READS", "from": "file:dags/a/sql/settle.hql", "to": "file:dags/a/sql/load.sql",
 "origin": "script", "status": "verified", "rule": "name", "table": "billing.orders",
 "evidence": {"path": "dags/a/sql/settle.hql", "line": 1, "quote": "..."}}
```

미해결이면 `to`는 `null`이고 `spec`(정규화한 테이블 이름), `reason`, 필요하면 `candidates`를 둔다. 기존 edge와 같은 모양이라 파일 상세와 의존성 지도가 그대로 읽는다.

`graph.json`에 새 목록을 더한다.

```
"external_tables": [{"from": "file:dags/a/sql/load.sql", "table": "staging.orders", "line": 1}]
```

기존 `externals`(외부 패키지)와 같은 모양이다. 파일과 테이블 쌍마다 첫 줄 하나만 남기고 `(from, line, table)` 순으로 정렬한다.

둘 다 추가 사항이다. 기존 필드는 바꾸지 않는다. `docs/artifact-compatibility.md`에 적는다.

## 기존 기능에 미치는 영향

| 기능 | 변경 |
|---|---|
| 리비전 비교 (`changes.py`) | 해석된 `READS`도 따라간다. SQL 하나를 바꾸면 그 결과를 읽는 SQL과, 그 SQL들을 가리키는 DAG 파일이 영향 범위에 든다. |
| 감사 (`audit.py`) | 없음. `IMPORTS`만 본다. |
| 지도 (`atlas.py`) | 부품 연결 종류 `read`로 센다. 뷰어는 import·reference와 같은 선으로 그린다. |
| 폴더 요약 (`brief.py`) | "TABLE READS BETWEEN FOLDERS" 구역을 더한다. |
| 채점 (`evals/score.py`) | `reads`와 `external_tables` 정답 집합을 비교한다. import 정밀도·재현율에는 넣지 않는다. |
| 리포트 (`report.py`) | 아래 구역을 더한다. |

**리포트의 "Tables" 구역.**

- **외부 테이블.** 스키마(마지막 조각을 뺀 앞부분)별로 접는다. 테이블마다 읽는 파일과 근거 줄을 보여 준다.
- **따라가지 못한 이름.** `dynamic`과 `ambiguous`를 사유별로 접어 보여 준다.
- 안내문에 한계를 적는다. SQL 파일에 적힌 이름만 읽는다. Python 안의 SQL, 기본 스키마, 뷰 안의 테이블은 읽지 않으므로 외부로 나온 테이블도 레포 안에서 만들 수 있다.

SQL 파일도 테이블 레코드도 없으면 구역과 목차 항목을 만들지 않는다.

SQL 파일마다 "누가 이 파일의 결과를 읽는가"는 기존 파일 상세가 보여 준다. edge에 `table`이 있으면 파일 상세의 관계 줄에 테이블 이름을 함께 표시한다.

## 오류 처리

- 가리기에서 따옴표나 블록 주석이 닫히지 않으면 그 파일을 `degraded`로 표시하고 그때까지 읽은 레코드는 유지한다. 기존 다른 언어와 같은 처리다.
- 1MB를 넘는 SQL은 기존처럼 `too_large`로 건너뛴다. 그 파일이 쓰는 테이블은 외부 테이블로 나온다.
- 이름으로 읽을 수 없는 토큰(숫자로 시작, 빈 조각)은 레코드를 만들지 않는다.

## 검증

**단위 테스트.** 구현보다 먼저 쓴다.

- 가리기: 줄 주석, 블록 주석, 문자열 안의 `FROM`, `''` 이스케이프, Jinja 블록 태그, 닫히지 않은 따옴표
- 추출: 읽기·쓰기 각 형태, 쉼표 목록, 별칭, 서브쿼리, 테이블 함수, `LATERAL VIEW`, CTE, 함수 안의 `FROM`, Hive의 `FROM t INSERT`, 이름 정규화, 변수 자리, 임시 테이블
- 연결: 표의 규칙 0~5 각각, 쓰는 파일이 둘인 테이블, 자기 자신

**fixture.** `evals/fixtures/airflow-sql`을 넓힌다. 기존 SQL 여섯 개에서 다음이 나와야 한다.

| 읽는 파일 | 쓰는 파일 | 테이블 |
|---|---|---|
| `dags/billing/sql/settle.hql` | `dags/billing/sql/load_orders.sql` | `billing.orders` |
| `dags/ledger/sql/close.sql` | `dags/billing/sql/settle.hql` | `billing.settled` |
| `dags/ledger/sql/report.sql` | `dags/ledger/sql/close.sql` | `ledger.closed` |

외부 테이블은 `staging.orders`(`load_orders.sql`)와 `shared.calendar`(`shared/sql/report.sql`)다. 여기에 B형, C형, CTE, 함수 안의 `FROM`, 임시 테이블, 쓰는 파일이 둘인 테이블을 담은 SQL을 더한다. 모두 합성이다.

**실측.** 사내 팀 폴더 재실행은 사용자가 한다. 성공 기준은 다음과 같다.

1. 해석된 `READS` edge에서 무작위로 고른 30개 중 오탐이 3개 이하다.
2. 외부 테이블에서 무작위로 고른 30개 중 "레포 안의 SQL 파일이 만드는 테이블"이 3개 이하다.
3. 기존 테스트가 모두 통과하고 `evals/score.py`가 통과한다.

기준 2를 넘기면 원인을 먼저 분류한다. 인라인 SQL이 원인이면 다음 하위 프로젝트로 넘기고, 추출 규칙이 원인이면 규칙을 고친다.
