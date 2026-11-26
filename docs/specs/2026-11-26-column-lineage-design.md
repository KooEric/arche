# 컬럼 단위 lineage 설계

2026-11-26 작성. 대상 버전은 **1.3.0**이다. 기준은 `main` e878e64(v1.2.0)이다. [dbt 모델 테이블과 속성 문서](2026-11-26-dbt-docs-design.md)(1.2.0) 위에 쌓는다. 출발점은 [1.3.0 브리프](../plans/2026-11-26-v1.3-column-lineage-brief.md)이고, 브리프 4장의 결정 질문 Q1~Q14는 모두 권장안으로 정했다. [1.1.0 설계](2026-11-25-data-profile-design.md)는 컬럼 lineage를 1.2에 두었지만, 실제 순서는 1.2 = dbt 메타데이터, 1.3 = 컬럼 lineage로 바뀌었다.

이 문서의 숫자와 지원 표는 임시 사본에서 만든 프로토타입(브리프 Task 0)으로 확인했다. 측정 대상은 합성 fixture, 공개 레포 `dbt-labs/jaffle-shop`과 `dbt-labs/dbt-project-evaluator`, 합성 대형 레포(SQL 파일 3,000개)이다.

## 왜 바꾸는가

1.2.0까지 Arche는 SQL 파일이 어떤 **테이블**을 읽고 쓰는지만 안다. `langs/sql_tables.py`는 키워드 뒤의 이름만 읽고 `SELECT` 목록은 보지 않는다. 그래서 다음 질문에 답하지 못한다.

- `mart.revenue.net_amount`는 어느 상류 컬럼에서 어떤 식으로 오는가. 지금은 `mart.revenue`를 쓰는 파일까지만 알고, 사람이 파일을 열어 읽어야 한다.
- `stage.orders.cust_id`를 바꾸면 무엇이 깨지는가. 지금은 `stage.orders`를 읽는 파일 전부가 후보이다(과대 추정).

## 목표와 범위

**목표.** 두 질문에 근거 줄과 함께 답한다.

1. **출처:** 테이블 T의 컬럼 X는 어느 상류 `table.column`에서, 어떤 식(그대로, 이름만 바꿈, 변환, 집계, 윈도)을 거쳐 오는가.
2. **영향:** 테이블 T의 컬럼 C를 바꾸거나 지우면, 어느 하류 컬럼·파일·테이블이 영향을 받는가. JOIN 키나 필터로만 쓰는 곳도 따로 보인다.

답은 세 곳에 낸다: 새 산출물 `columns.json`, `changes.json`의 `column_impact`, 리포트 "Tables" 구역.

**이번 범위.**

1. SQL·HQL 파일의 쿼리 식을 해석하는 작은 파서(브리프 방안 B). 지원 문법은 아래 "지원하는 SQL" 표와 같다.
2. 컬럼 참조를 소스에 붙이는 scope 해석. CTE, 파생 테이블, 서브쿼리, 별칭 가림, `SELECT *` 펼치기를 다룬다.
3. 파일 사이 연결. 같은 테이블 이름을 쓰고 읽는 파일, dbt `ref()`·`source()`를 잇는다.
4. `imports.py`가 `graph.json` 다음에 `columns.json`을 쓴다. `graph.json`은 바뀌지 않는다.
5. `changes.py`의 컬럼 영향(`column_impact`)
6. 리포트의 컬럼 패널, "이 컬럼을 지우면" 보기, 따라가지 못한 컬럼 목록
7. fixture `evals/fixtures/column-lineage/`와 채점기 `evals/score_columns.py`

**하지 않는 것.**

- 저장 프로시저, `DECLARE`·`BEGIN … END`, 절차형 SQL, 동적 SQL(`EXECUTE IMMEDIATE`)
- UDF 본문. 호출은 변환으로만 본다.
- `PIVOT`·`UNPIVOT`·`MATCH_RECOGNIZE`. 그 문장은 `unsupported`이다.
- Python 문자열 안의 SQL. 테이블 lineage도 읽지 않는다.
- 레포 밖에서 정의된 뷰 펼치기, `USE db;`로 정해지는 기본 스키마
- `UPDATE … SET`, `ALTER TABLE … ADD|RENAME COLUMN`. ALTER로 바뀐 이름은 추적하지 않고 리포트 안내문에 한계로 적는다.
- 구조체 필드·배열 원소 단위 lineage. `a.b`가 구조체 접근이면 최상위 컬럼 `a`까지만 잇는다.
- Jinja `{% for %}`로 만드는 컬럼, `dbt_utils.star()` 같은 매크로 결과. `dynamic`으로 남긴다.
- 1.1.0 `profile.json`의 데이터 컬럼을 컬럼 목록으로 쓰는 것(Q5)
- 문서 컬럼과 SQL 출력 컬럼의 비교(Q11). 1.3.x로 미룬다.
- `map.html`의 컬럼 표시(Q12)
- 실제 쿼리 실행, 웨어하우스 접속, `INFORMATION_SCHEMA`

## 전제

- 테이블 이름 정규화와 테이블 맞추기는 테이블 lineage와 같은 함수를 쓴다. 이름은 `sql_tables`의 정규화(소문자, 따옴표 제거, 세 부분까지)를 쓰고, 맞추기는 `lineage.resolve`를 쓴다. 그래서 컬럼이 붙는 테이블 이름은 `graph.json`의 테이블 이름과 항상 같다.
- 1.2.0 산출물은 `graph.json`을 다시 읽지 않고, `build_graph` 안에서 만든 값을 그대로 받는다(`projects`, `models`, `dbt_sources`, `dbt_models`).
- 1.2.0 산출물을 `main` 코드와 대조한 결과, 브리프 0장과 다른 점은 다음과 같다. 설계는 이 사실을 기준으로 한다.
  - alias를 리터럴로 알 수 없는 모델의 `dbt_models[].table`은 키가 빠지는 것이 아니라 `null`이다.
  - dbt 모델 테이블은 스키마 자리가 항상 변수이다(`{}.orders`, `{}_marts.orders`). 그래서 이름으로 맞추면 언제나 `inferred`(`rule: table_name`)이다. `ref()` 읽기는 `dbt.resolve_ref`가 모델 파일을 바로 찾으므로 `verified`(`rule: dbt_ref`)로 본다.
  - `dbt_sources[]`에는 `status: resolved | ambiguous`가 있고, `ambiguous`이면 `columns`가 비어 있다.
  - 문서 컬럼 이름은 원래 철자 그대로이다. 비교할 때 소문자로 바꾼다.
- 상한과 동작 숫자는 아래 "상한과 성능"의 실측을 따른다.

## 구성 요소

| 단위 | 책임 | 의존 | 프로토타입 줄 수 |
|---|---|---|---|
| `sqlcol/__init__.py` (신규) | `COLUMNS_VERSION = 1`, 상한 상수 | 없음 | 10 |
| `sqlcol/tokens.py` (신규) | 원문을 위치가 있는 토큰으로 나누고 `;`로 문장을 자른다. 주석과 `{# #}`·`{% %}` 태그를 버리고, `{{ ref() }}`·`{{ source() }}`는 `dbt` 토큰 하나로, 그 밖의 `{{ }}`·`${}`·`$x`는 단어 안의 `{}`로 바꾼다. | `sql_tables`의 공개 함수 | 180 |
| `sqlcol/ast.py` (신규) | 불변 결과(`NamedTuple`): `Ref`, `Expr`, `Item`, `Star`, `TableName`, 세 가지 소스, `Select`, `SetOp`, `Values`, `Cte`, `Query`, `Statement`, `Merge` | 없음 | 145 |
| `sqlcol/parser.py` (신규) | 쿼리 식의 재귀 하강 파서. 식은 피연산자와 연산자의 평평한 나열로 읽고, 컬럼 참조와 그 맥락(집계, 윈도, 조건, 정렬 키)만 모은다. 깊이·항목 상한을 직접 검사한다. | `tokens`, `ast` | 800 → 둘로 나눈다(아래) |
| `sqlcol/statements.py` (신규) | 쿼리를 감싼 문장 머리: `INSERT`, `CREATE … AS`, `CREATE TABLE (…)`, `MERGE`, Hive `FROM … INSERT …`. `parse(tokens)` 진입점 | `parser` | 250 |
| `sqlcol/scope.py` (신규) | 관계(테이블, CTE·파생·임시 테이블, 테이블 함수)와 컬럼 참조 찾기, 상태 규칙 | 없음 | 250 |
| `sqlcol/derive.py` (신규) | 문장 하나의 출력 컬럼을 실제 테이블 컬럼까지 거슬러 올라가고, 필터·조인·그룹·정렬 키 사용처를 모은다. | `scope`, `ast` | 340 |
| `column_lineage.py` (신규) | 파일마다 한 번 파싱하고, 컬럼 목록 색인을 만들며 반복하고, `columns.json` 문서를 만든다. | `sqlcol`, `lineage`, `dbt` | 450 → 둘로 나눈다(아래) |
| `column_impact.py` (신규) | 컬럼 그래프 하류 BFS, 두 `columns.json` 비교 | 없음 | — |
| `report_columns.py` (신규) | 컬럼 패널, 영향 보기, 미해결 목록 HTML | `report`의 공용 함수 | — |
| `langs/sql_tables.py` (수정) | `literal_args`, `table_name`, `ref_version` 공개 함수를 더한다. 기존 동작은 그대로이다. | 없음 | +15 |
| `lineage.py` (수정) | `resolve_matched`: `resolve`와 같은 결과에 맞춘 테이블 이름(`matched`)을 더한다. `resolve`는 그 키를 뺀 결과를 그대로 돌려준다. | 없음 | +10 |
| `graph.py` (수정) | `build_graph_and_columns`가 `(graph, columns)`를 돌려준다. `build_graph`는 그 첫 값만 돌려준다. | `column_lineage` | +10 |
| `scripts/imports.py` (수정) | `columns.json`을 쓰고 요약에 수를 더한다. | `graph` | +15 |
| `changes.py`, `scripts/changes.py` (수정) | `column_impact` | `column_impact` | — |
| `scripts/render.py`, `report.py`, `report_data.py`, `i18n.py` (수정) | `columns.json` 읽기, 호출 몇 줄, 한국어 문구 | `report_columns` | — |

파일당 800줄 상한을 지키기 위해 프로토타입의 큰 파일 두 개는 구현에서 나눈다.

- `parser.py`: 식 부분(`expressions.py`, 약 400줄)과 쿼리·FROM 부분(`parser.py`, 약 400줄)
- `column_lineage.py`: 파일 분석과 반복(`column_lineage.py`)과 문서 조립(`column_document.py`)

`report.py`는 지금 727줄이다. 새 HTML은 전부 `report_columns.py`에 두고, `report.py`에는 호출과 인자 전달만 넣는다.

## 토큰

- 원문을 한 번 앞으로만 읽는다. 찾기는 바늘마다 마지막 답을 기억해서(`sql_tables._Seek`와 같은 방식) 닫히지 않은 `/*`, `{{`, `'`가 여러 번 나와도 선형이다.
- 문자열 규칙은 `sql_tables.mask`와 같다(`''`와 `\` 이스케이프). 문자열 내용은 토큰에 담지 않는다.
- 큰따옴표: 파일에 백틱이 하나도 없으면 식별자로, 있으면 문자열로 본다. Hive·Spark·BigQuery는 백틱으로 이름을 감싸고 `"x"`를 문자열로 쓰기 때문이다.
- 줄 번호는 `\n`만 센다. 근거 줄은 테이블 lineage와 같은 규칙이다.
- 문장 하나가 200,000 토큰을 넘으면 나머지를 버리고 그 문장을 `too_large`로 기록한다. 메모리는 문장 하나만큼만 쓴다.

## 지원하는 SQL

| 구분 | 지원 | 결과 |
|---|---|---|
| 문장 | `SELECT`·`WITH`·`VALUES`, `INSERT INTO|OVERWRITE [TABLE] t [PARTITION (…)] [(cols)] query`, `WITH … INSERT …`, `CREATE [OR REPLACE] [TEMP] TABLE|VIEW t [(cols)] … AS query`, `CREATE TABLE t (col type, …) [PARTITIONED BY (…)]`, `MERGE INTO … USING … WHEN [NOT] MATCHED … UPDATE SET|INSERT (…) VALUES (…)|DELETE`, Hive `FROM src INSERT … SELECT … INSERT … SELECT …` | 문장마다 `parsed`·`partial` |
| 건너뜀 | 그 밖의 문장(`UPDATE`, `DELETE`, `ALTER`, `DROP`, `SET`, `USE`, `GRANT`, `CREATE FUNCTION` 등), `(`로 시작하지만 쿼리가 아닌 매크로 본문 | 기록하지 않음 |
| 해석 안 함 | `PIVOT`·`UNPIVOT`, `DECLARE`·`BEGIN`·`CALL`·`EXECUTE`, `SELECT … INTO`, `INSERT ALL|FIRST`, `BY NAME`, `MERGE`의 `UPDATE SET *`·`INSERT *`, `INSERT … DEFAULT VALUES` | `unsupported` |
| 쿼리 | CTE(열 목록, `RECURSIVE`, `MATERIALIZED`), 집합 연산(`UNION [ALL]`, `INTERSECT`, `EXCEPT`, `MINUS`), 괄호 쿼리, `ORDER BY`·`LIMIT`·`OFFSET`·`FETCH`, `DISTINCT [ON]`, `TOP n` | |
| FROM | 테이블, 별칭(`AS` 있음·없음), 열 목록 별칭, 파생 테이블, `LATERAL`, 괄호로 묶은 JOIN, 모든 JOIN 종류, `ON`·`USING`, `CROSS|OUTER APPLY`, `LATERAL VIEW [OUTER] f(x) t AS c, …`, `UNNEST(…) [WITH ORDINALITY] AS t(c)`, 테이블 함수(`FLATTEN(input => x)`), `VALUES`, `TABLESAMPLE`·`SAMPLE`·`AT|BEFORE (…)`·`FOR SYSTEM_TIME AS OF`·`WITH (힌트)` 건너뛰기 | |
| 절 | `WHERE`, `GROUP BY`(번호, `ALL`, `ROLLUP`·`CUBE`·`GROUPING SETS`, `WITH ROLLUP`), `HAVING`, `QUALIFY`, `WINDOW w AS (…)`, `DISTRIBUTE|SORT|CLUSTER BY` | 사용처 |
| 식 | 연산자, 함수 호출, `CASE` 두 형태, `CAST`·`TRY_CAST`·`SAFE_CAST`·`CONVERT`, `x::type`, `EXTRACT(part FROM x)`, `DATEADD(day, …)` 같은 날짜 단위 인자, `INTERVAL '1' DAY`, `IS [NOT] DISTINCT FROM`, 타입 리터럴(`DATE '…'`), 윈도 `OVER (PARTITION BY … ORDER BY … ROWS …)`·`OVER w`, `FILTER (WHERE …)`, `WITHIN GROUP (ORDER BY …)`, `IGNORE|RESPECT NULLS`, 람다(`x -> …`), 이름 있는 인자(`a => x`), `col:path`, 첨자 `[…]`, 스칼라·`IN`·`EXISTS` 서브쿼리 | |
| 별 | `*`, `t.*`, `* EXCEPT (…)`, `* EXCLUDE (…)`·`EXCLUDE c`, `* REPLACE (e AS c)`, `* RENAME (a AS b)` | |
| Jinja | `{{ config() }}`와 문장 앞뒤의 매크로 호출은 무시한다. `{{ ref() }}`·`{{ source() }}`는 테이블이다. 식 안의 `{{ … }}`는 런타임 값(`dynamic`)이다. `{% if %}…{% else %}…{% endif %}`는 두 분기의 SQL이 모두 남는다. 본문이 매크로 하나뿐인 CTE·파생 테이블(`( {{ dbt.date_spine(…) }} )`)은 컬럼을 모르는 소스이다. | |
| 그 밖 | 끝에 남은 쉼표(`a, FROM`), 문장 뒤에 남은 토큰 | 끝 쉼표는 허용, 남은 토큰은 `partial` |

모르는 함수와 모르는 키워드는 특별 규칙 없이 "괄호 안 토큰을 모두 인자로 보는" 관대한 규칙으로 읽는다. 그래서 오탐이 생겨도 그 식 안에서 그친다.

MERGE와 Hive 다중 INSERT는 각각 약 100줄과 40줄로 처리되어 Q14의 기준(200줄 이내)을 넘지 않는다. 이번 범위에 넣는다.

## 컬럼 참조 찾기

SELECT 하나마다 FROM 순서대로 소스 목록을 두고, 컬럼 참조를 안쪽 scope부터 찾는다.

- **소스의 종류.**
  - 테이블: 카탈로그(아래 "컬럼 목록")가 아는 컬럼 목록이 있거나 없다.
  - CTE·파생 테이블·같은 파일의 임시 테이블: 출력 컬럼이 그대로 컬럼 목록이다. 노드를 만들지 않고, 지나간 경로를 `via`에 `cte:<이름>`, `subquery:<줄>`, `temp:<이름>`으로 남긴다.
  - 테이블 함수(`UNNEST`, `LATERAL VIEW`, `FLATTEN`): 어느 컬럼이든 입력 식의 컬럼에서 온다(`transform`, `inferred`, `rule: table_function`).
- **이름 가림.** 한정자 없는 테이블 이름은 CTE, 같은 파일의 임시 테이블, 카탈로그 순서로 찾는다. 안쪽 CTE가 바깥 CTE와 실제 테이블을 가린다. CTE는 처음 읽힐 때 계산한다. 쓰이지 않는 CTE의 필터는 기록하지 않는다. `RECURSIVE` CTE는 첫 분기의 컬럼만 따른다.
- **한정자 있는 참조**(`o.amt`, `billing.orders.amt`). 별칭이나 테이블 전체 이름, 또는 그 뒷부분과 맞는 소스를 안쪽부터 찾는다. 맞는 소스가 없고 첫 부분이 컬럼으로 찾아지면 구조체 접근으로 보고 그 컬럼에 잇는다(`inferred`, `rule: struct_field`). 아니면 `unknown_qualifier`이다.
- **한정자 없는 참조.** 같은 SELECT의 소스를 다음 순서로 본다.

| 경우 | 결과 |
|---|---|
| `USING` 열 | 목록에 있거나 목록을 모르는 모든 소스에 잇는다(`inferred`, `rule: using`). |
| 목록에 그 이름이 있는 소스가 정확히 하나, 목록을 모르는 소스 없음 | 그 소스(`rule: listed`). 상태는 아래 표를 따른다. |
| 목록에 있는 소스 하나, 목록을 모르는 소스도 있음 | 그 소스, `inferred`(`rule: known_source`) |
| 목록에 있는 소스가 둘 이상 | `ambiguous`(후보 목록) |
| 목록에 있는 소스 없음, 목록을 모르는 소스 하나 | 그 소스, `inferred`(소스가 하나뿐이면 `single_source`, 아니면 `only_open_source`) |
| 목록에 있는 소스 없음, 목록을 모르는 소스 여럿 | `ambiguous` |
| 어느 소스에도 없음 | 바깥 scope(상관 서브쿼리), 그래도 없으면 `unknown_column` |

- WHERE·GROUP BY·ORDER BY·QUALIFY에서 소스에 없는 이름이 SELECT 별칭이면 그 출력 컬럼의 상류를 쓴다(`inferred`, `rule: select_alias_ref`). `GROUP BY 1`은 첫 출력 컬럼이다.

## 상태 규칙

Q2 권장안을 따른다. 애매하면 낮은 쪽을 고른다. 경로 하나가 여러 단계를 지나면 가장 낮은 상태를 쓰고, `rule`에는 상태를 낮춘 단계를 남긴다.

| 상태 | 조건 |
|---|---|
| `verified` | 참조가 한정자로 소스 하나에 붙거나(`rule: qualified`) 컬럼 목록으로 하나가 정해지고(`rule: listed`), 그 소스가 다음 중 하나이다. ① 같은 파일의 CTE·파생·임시 테이블, ② 테이블 lineage가 `verified`로 맞춘 테이블(`rule: name`, dbt `ref()`는 `dbt_ref`)이고 그 컬럼 목록을 **레포 SQL**(출력 컬럼 또는 DDL)이 알며 컬럼이 목록에 있음, ③ 외부 테이블이고 한정자로 붙음 |
| `inferred` | 한정자 없이 소스가 하나뿐임(`single_source`), 목록을 모르는 소스가 섞임(`known_source`, `only_open_source`), 목록이 1.2.0 문서에서 옴(`documented`), 목록에 없는 컬럼(`unlisted_column`), `SELECT *` 펼치기(`star`), 같은 이름 통과(`star_passthrough`), 위치로 맞춘 INSERT(`positional`, `alias_mismatch`), SELECT 별칭으로 맞춘 INSERT(`select_alias`), 같은 출력 이름이 둘(`duplicate_name`, Jinja 분기), 테이블 lineage가 `inferred`(`table_name`), 대상이 dynamic 모델(`dynamic_model`), `USING`, 구조체 접근, 테이블 함수 |
| unresolved | `unresolved` 목록에 사유와 함께 남긴다: `ambiguous`, `unknown_star`, `unknown_column`, `unknown_qualifier`, `dynamic`, `arity`, `unnamed`, `too_deep`, `too_many_columns`, 테이블을 맞추지 못함(`table_dynamic`, `table_ambiguous`, `table_not_found` 등. 테이블 하나에 한 번만) |

Q2를 적용하며 정한 해석 세 가지. 사용자 검토 대상이다.

1. **목록으로 정해진 한정자 없는 참조.** 소스가 하나뿐이어도 그 소스의 레포 SQL 목록에 이름이 있으면 `verified`(`rule: listed`)이다. 브리프 8장의 "둘 다 앎: 정확히 한쪽이면 verified"와 같은 규칙이다. 이 규칙이 없으면 한정자를 거의 쓰지 않는 dbt 모델의 edge가 모두 `inferred`가 된다. 소스의 목록을 모르면 Q2대로 `inferred`(`single_source`)이다.
2. **외부 테이블.** 한정자로 붙은 외부 테이블 참조는 `verified`이다. 그 이름은 SQL에 적힌 그대로이고, 맞출 대상이 없다. 컬럼 노드에는 `external: true`를 붙인다.
3. **문서 컬럼 목록은 열린 목록이다.** 문서는 컬럼을 빠뜨리기 쉽다. 그래서 문서에 없는 이름도 "있을 수 있음"으로 보고, 문서에서 얻은 목록으로는 `verified`를 주지 않는다.

## 식 종류

Q4 권장안대로 열 가지를 쓴다. 참조마다 맥락으로 정하고, CTE 체인을 지나면 가장 강한 것을 남긴다. 강도 순서는 `aggregate` > `window` > `transform` > `rename`·`direct`이다.

| kind | 뜻 | 대상 |
|---|---|---|
| `direct` | 컬럼 그대로, 이름도 같음 | 출력 컬럼 |
| `rename` | 컬럼 그대로, 이름만 다름 | 출력 컬럼 |
| `transform` | 함수·연산·`CAST`·`CASE`의 결과 | 출력 컬럼 |
| `aggregate` | 집계 함수 안 | 출력 컬럼 |
| `window` | 윈도 함수의 인자 | 출력 컬럼 |
| `constant` | 리터럴로만 만든 컬럼. `from`이 `null`이다. `COUNT(*)`, `ROW_NUMBER()`처럼 함수 호출이 있으면 상수가 아니다. | 출력 컬럼 |
| `filter` | WHERE·HAVING·QUALIFY | 테이블(`table:<이름>`) |
| `join_key` | JOIN ON·USING, MERGE ON | 테이블 |
| `group_key` | GROUP BY | 테이블 |
| `order_key` | ORDER BY, 윈도의 PARTITION BY·ORDER BY, `WITHIN GROUP`·집계 안 ORDER BY, `DISTRIBUTE|SORT|CLUSTER BY` | 테이블 |

- 사용처 kind의 대상은 그 문장이 쓰는 테이블이다. CTE·서브쿼리 안의 사용처도 그 문장의 대상으로 모인다.
- CASE의 `WHEN` 조건과 단순 CASE의 피연산자, `IF(cond, …)`·`IFF`·`IIF`의 첫 인자, `FILTER (WHERE …)`에 쓰인 컬럼은 `role: "condition"`을 붙인다.

## 쓰기 대상과 컬럼 맞추기

| 문장 | 대상 테이블 | 컬럼 이름 |
|---|---|---|
| 대상 없는 SELECT | `query:<경로>#<문장 번호>`(Q13) | 출력 이름. 이름 없는 식은 `_c<위치>` |
| dbt 모델 파일의 첫 SELECT | 1.2.0 `dbt_models[].table`. `null`이면 모델 이름(파일 이름)이고 그 컬럼의 모든 edge가 `inferred`(`dynamic_model`) | 출력 이름 |
| `CREATE … AS` | 대상 이름 | 열 목록이 있으면 위치로, 없으면 출력 이름. 개수가 다르면 `arity` |
| `INSERT … (cols)` | 대상 이름 | 열 목록 + Hive 동적 파티션 열을 위치로. 개수가 다르면 `arity` |
| `INSERT …`(열 목록 없음) | 대상 이름 | 대상 목록을 알면 정적 파티션 열을 뺀 목록과 위치로 맞춘다(`positional`). 별칭이 다르면 `alias_mismatch: true`. 목록을 모르면 SELECT 별칭으로 맞춘다(`select_alias`). 이름 없는 식은 `unnamed`. 개수가 다르면 `arity` |
| `PARTITION (dt = '…')` | 대상 이름 | 정적 값은 `constant` edge |
| `MERGE` | 대상 이름 | `UPDATE SET c = e`와 `INSERT (c) VALUES (e)`의 각 짝 |
| `CREATE TABLE t (…)` DDL | — | 컬럼 목록만 기록한다. edge는 없다. |
| `CREATE TEMP TABLE` | 노드 없음 | 같은 파일의 뒤 문장에서 `temp:<이름>`으로 지나간다. |
| 대상이 런타임 이름(`ledger.daily_{}`, `insert into {{ ref() }}`) | — | 문장은 `partial`(`dynamic`), `unresolved`에 남긴다. |

## 컬럼 목록

`SELECT *` 펼치기, 한정자 없는 참조, 위치 맞추기에 쓰는 테이블별 컬럼 목록이다. Q5대로 출처의 우선순위는 다음과 같다.

1. `sql_output`: 레포 SQL이 그 테이블을 정의하며 만든 출력 컬럼(`CREATE … AS`, dbt 모델, 열 목록 있는 `INSERT`). 같은 테이블을 여러 문장이 정의하면 (경로, 문장 번호) 순으로 첫 것을 쓴다.
2. `ddl`: `CREATE TABLE (…)`의 컬럼과 `PARTITIONED BY` 컬럼
3. `documented`: 1.2.0 `dbt_models[].columns`, `dbt_sources[].columns`(`status: resolved`만). 열린 목록이다.
4. `insert_alias`: 위 셋이 없을 때 열 목록 없는 INSERT의 별칭. `verified` 근거가 되지 않는다.

- 목록을 정의한 문장의 SELECT에 컬럼을 모르는 `*`가 있으면 그 목록은 열린 목록이다. 아는 이름만 담고, 다른 이름도 "있을 수 있음"으로 본다.
- 출처가 여럿이고 서로 다르면 위 순서대로 고르고, 그 차이를 `column_sources_disagree`에 남긴다.
- **반복.** 파일 B의 `*` 펼치기가 파일 A의 출력에 기대므로, 모든 파일을 경로 순으로 유도한 뒤 목록을 다시 만들고, 목록이 바뀌지 않을 때까지 되풀이한다. 파싱은 파일마다 한 번이다. 최대 6회이며, 6회 안에 멈추지 않으면 마지막 결과를 쓰고 `truncated: true`로 둔다. 실측에서는 사슬 3,000개짜리 합성 레포도 3회에 멈췄다.

**같은 이름 통과**(Q3). `SELECT * FROM a`로 만든 `b`에서 `a`의 컬럼을 모르면, 출력 쪽에 `unknown_star`를 남긴다. 하류가 `b.x`를 실제로 읽을 때만 `a.x → b.x`를 `inferred`(`star_passthrough`)로 잇는다. `a`도 같은 경우이면 사슬을 따라 올라간다. 같은 파일의 CTE 안에서도 같은 규칙이다.

## `columns.json`

`imports.py`가 `graph.json` 다음에 쓴다. 분석한 SQL 파일이 하나도 없으면 쓰지 않고, 이전 실행이 남긴 `columns.json`이 있으면 지운다. `graph.json`은 바이트 단위로 1.2.0과 같다.

```json
{
  "columns_version": 1,
  "inventory_sha256": "<graph.json과 같은 값>",
  "columns": [
    {"id": "col:stage.orders.amount", "table": "stage.orders", "column": "amount", "display": "amount",
     "known_from": ["sql_output"], "external": false,
     "defined": {"path": "dags/sql/stage.sql", "line": 3, "quote": "SELECT o.id AS order_id, …"}},
    {"id": "col:raw.orders.amt", "table": "raw.orders", "column": "amt", "display": "amt",
     "known_from": [], "external": true}
  ],
  "edges": [
    {"from": "col:raw.orders.amt", "to": "col:stage.orders.amount", "kind": "rename",
     "status": "verified", "rule": "qualified", "expression": "o.amt AS amount", "via": [],
     "evidence": {"path": "dags/sql/stage.sql", "line": 3, "quote": "SELECT o.id AS order_id, …"}},
    {"from": "col:stage.orders.amount", "to": "col:mart.revenue.size_class", "kind": "aggregate",
     "role": "condition", "status": "inferred", "rule": "positional",
     "expression": "CASE WHEN b.total > 100 THEN 'big' ELSE NULL END AS size_class",
     "via": ["cte:by_customer", "cte:paid", "cte:orders"], "evidence": {"…": "…"}},
    {"from": null, "to": "col:mart.revenue.dt", "kind": "constant", "status": "verified", "rule": "literal",
     "expression": "'{{ ds }}'", "via": [], "evidence": {"…": "…"}},
    {"from": "col:stage.orders.status", "to": "table:mart.revenue", "kind": "filter",
     "status": "verified", "rule": "listed", "via": ["cte:orders"], "evidence": {"…": "…"}}
  ],
  "unresolved": [
    {"to": "table:stage.events", "spec": "*", "reason": "unknown_star", "source": "raw.events",
     "evidence": {"…": "…"}},
    {"to": "table:mart.mixed", "spec": "region", "reason": "ambiguous",
     "candidates": ["raw.people", "stage.customers"], "evidence": {"…": "…"}}
  ],
  "statements": [
    {"path": "dags/sql/report.sql", "index": 3, "line": 4, "target": null,
     "status": "unsupported", "reason": "pivot"}
  ],
  "column_sources_disagree": [],
  "truncated": false
}
```

- 컬럼 id는 `col:<테이블>.<컬럼>`이다. 테이블 이름에 `.`과 `{}`가 들어가므로 id를 쪼개지 말고 `table`·`column` 키를 쓴다. 이름은 소문자로 비교하고 원래 철자는 `display`에 둔다(Q6).
- `known_from`은 `sql_output`, `ddl`, `documented` 중 그 컬럼을 알려 준 것들이다. 외부 테이블에서 참조로만 알게 된 컬럼은 빈 목록이다.
- `expression`은 원문의 그 출력 항목을 공백 정리하고 200자에서 자른 것이다. 리터럴은 가리지 않는다. 레포 코드이고 이미 `quote`에 나온다.
- `table_rule`: 테이블 lineage가 이름 외의 규칙(`table_name`, `dbt_ref`)으로 테이블을 맞췄으면 그 규칙을 남긴다.
- `statements`에는 해석한 문장과 해석하지 못한 문장을 모두 넣는다. 건너뛴 문장(`UPDATE` 등)은 넣지 않는다. `status`는 `parsed`, `partial`(`tail`, `dynamic`, `arity`, `unknown_star`, `unnamed`), `unsupported`, `parse_error`, `too_large`이다.
- 정렬은 다음과 같다. 같은 입력이면 바이트 단위로 같다.
  - `columns`: `id` 순
  - `edges`: (`to`, `from`, `kind`, `evidence.path`, `evidence.line`) 순. 같은 열쇠의 edge는 하나만 남긴다.
  - `unresolved`: (`evidence.path`, `evidence.line`, `spec`, `reason`) 순
  - `statements`: 경로와 문장 번호 순
- `imports.py` 요약에 `columns: {columns, edges(상태별), unresolved, statements(상태별)}`를 더한다.
- `render.py`는 `profile.json`과 같은 방식으로 `columns.json`을 읽고, `inventory_sha256`이 `graph.json`과 다르면 거부한다.

## `changes.json`의 `column_impact`

Q9 권장안 (c)대로 한다. 기존 `files[].impact`는 바꾸지 않는다. `SCHEMA_VERSION`은 `"1.0"` 그대로이다.

```json
"column_impact": {
  "available": true,
  "seeds": [{"column": "col:stage.orders.cust_id", "change": "removed", "seed": "column_diff",
             "path": "dags/sql/stage.sql"}],
  "affected": [{"column": "col:mart.revenue.customer_key", "depth": 3, "kinds": ["rename"],
                "status": "inferred", "files": ["dags/sql/revenue.sql"]}],
  "used_by": [{"table": "table:mart.revenue", "kinds": ["group_key", "join_key"], "status": "verified",
               "files": ["dags/sql/revenue.sql"]}],
  "affected_files": ["dags/sql/revenue.sql"],
  "affected_tables": ["mart.revenue"],
  "truncated": false
}
```

- **씨앗.**
  - `--base-out`이 있으면 base와 head의 `columns.json`을 비교한다(`seed: column_diff`). `change`는 다음 셋이다.
    - `removed`: base에서 SQL이 만든 컬럼이 head에 없다.
    - `added`: 반대 경우
    - `redefined`: 들어오는 edge 집합(`from`, `kind`, `role`, `expression`)이 다르다.
  - 스냅샷이 하나뿐이면 바뀐 SQL 파일이 쓰는 모든 출력 컬럼을 씨앗으로 삼는다(`seed: file`). 거친 추정이다.
- **따라가기.** `removed`는 base의 edge로, 나머지는 head의 edge로 하류를 BFS로 따라간다. 출력 컬럼 edge는 계속 따라가고, 사용처 edge(`filter` 등)는 `used_by`에서 멈춘다.
- **경로의 상태.** 지나간 edge 중 가장 낮은 상태이다. 결과가 5,000개를 넘으면 멈추고 `truncated: true`로 둔다.
- **결과가 없을 때.** `columns.json`이 없으면 `column_impact` 키를 넣지 않는다. 그래서 기존 출력이 그대로이다.
- **안내 문구.** 리포트에 "컬럼 단위로 정확한 결과는 두 스냅샷을 비교할 때만 나온다"를 적고, `seed` 값을 화면에 보인다.

## 리포트

"Tables" 구역과 변경 구역만 바꾼다. 테이블 블록의 1.1.0·1.2.0 내용은 그대로이다.

1. **컬럼 lineage 패널.** `columns.json`이 있으면 SQL이 쓰는 테이블마다 `<details>`를 하나 둔다. 1.1.0·1.2.0 블록이 있는 테이블은 그 안에 하위 블록으로 넣는다.
   - 열: 컬럼 이름, 출처(상류 `table.column` 링크 목록), kind 배지, 식(`<code>`, 잘림), 상태 배지, 근거 링크
   - CASE 조건 컬럼(`role: condition`)은 출처 목록에서 "조건"으로 구분한다.
2. **"이 컬럼을 지우면".** 컬럼 행을 펼치면 하류 영향을 깊이별 목록으로 보인다. 하류 컬럼, `filter`·`join_key`·`group_key`·`order_key` 사용처, 파일, 테이블이 들어간다. 렌더 시점에 미리 계산하고, JavaScript 없이 `<details>`만 쓴다.
3. **따라가지 못한 컬럼.** `unresolved`를 사유별로 접어 보인다. 기존 "따라가지 못한 이름"과 같은 형태이다.
4. **문장 통계.** 테이블 구역 머리에 `statements` 상태별 수를 한 줄로 보인다.
5. **안내문.**
   - "SQL 파일의 SELECT만 읽는다. 뷰 안, Python 안 SQL, ALTER로 바뀐 이름, PIVOT, 매크로 결과는 따라가지 않는다."
   - "`inferred`는 확인이 필요하다."
6. **변경 구역.** `column_impact`가 있으면 파일 카드 아래에 씨앗과 영향받는 컬럼 목록을 더한다.
7. **상한.** 테이블 하나의 컬럼 표는 200개(`MAX_COLUMNS_SHOWN`), 컬럼 하나의 출처·영향 목록은 50개까지 그리고 "N개 더"로 센다.
8. **이스케이프.** 컬럼 이름, 식, 테이블 이름은 레포 텍스트지만 모두 기존 이스케이프 함수를 거친다. 그 안의 지시문은 데이터로만 다룬다.

## 상한과 성능

신뢰할 수 없는 입력에서도 일은 선형이고 메모리는 묶인다. 넘으면 그 문장(또는 그 항목)만 낮추고 계속한다.

| 상한 | 값 | 넘으면 |
|---|---|---|
| SQL 파일 크기 | 기존 inventory 1MB(`too_large`) | 파일을 읽지 않음 |
| 문장 하나의 토큰 | 200,000 | `too_large` |
| 중첩 깊이(괄호, 식, CASE, 호출, 서브쿼리, CTE 유도) | 128 | `too_deep`. 식은 127겹, 서브쿼리는 63겹까지 읽는다. |
| SELECT 항목 | 2,000(파싱), 펼친 뒤 출력 2,000 | `too_many_items`, `too_many_columns` |
| 파일 하나의 컬럼 edge | 20,000 | `truncated: true` |
| 전체 컬럼 edge | 500,000 | `truncated: true` |
| 컬럼 목록 반복 | 6회 | `truncated: true` |
| 영향 BFS 결과 | 5,000 | `truncated: true` |

- 파서는 Python 재귀 한도에 닿기 전에 직접 깊이를 검사한다. Python 3.9에서 재귀 한도를 600으로 낮춰도 상한에서 멈췄다(기본 1,000). `RecursionError`는 마지막 방어선으로만 잡고 `parse_error`(`too_deep`)로 기록한다.
- 한 출력 컬럼의 상류는 (테이블, 컬럼, kind, role)마다 하나로 줄인다. `x.*, y.*` 같은 펼치기 결과의 이름도 한 번씩만 센다. 프로토타입에서 이 둘이 없을 때 결과가 지수적으로 늘어나는 것을 확인했다.
- 적대적 입력 실측. 아래 입력이 모두 1초 안에 상한 사유와 함께 끝났다.
  - 괄호 10만·50만 개, 1MB 한 줄
  - 닫히지 않은 따옴표·주석·`{{`
  - CASE 1만 겹
  - CTE 1,000개 사슬, 같은 CTE 이름 1만 번
  - `x.*, y.*` 40단 겹침
  - 연산자 15만 개
- 문장 5만 개 파일은 1.4초가 걸렸다. 프로토타입에서 문장마다 줄 위치를 다시 세던 2차 시간 문제를 고친 결과이다.
- 공개 레포 실측(Python 3.11, 이 기기)

| 레포 | SQL 파일 | `imports.py` | 문장 | 컬럼 edge | 주요 미해결 |
|---|---|---|---|---|---|
| jaffle-shop | 15 | 0.2초, RSS 29MB | parsed 13 | 91(`inferred` 전부: 문서 없는 source의 `*`) | `unknown_star` 6, 매크로 컬럼 `dynamic` 4 |
| dbt-project-evaluator | 123 | 0.3초, RSS 31MB | parsed 102, `parse_error` 1(`{% for %}`로 만든 UNION) | 56 | `table_dynamic` 39(`from {{ evaluator_models() }}`), `unknown_star` 33 |
| 합성 3,000 파일(12MB, 파일마다 CTE 2개·컬럼 31개·JOIN·윈도) | 3,000 | 11.3초, RSS 1.1GB, `columns.json` 155MB | parsed 3,300 | 204,589 | — |

  대형 합성 레포의 RSS 대부분은 `columns.json`(155MB)을 들여쓰기 있는 JSON 문자열로 만드는 데서 온다. 유도 중 Python 객체의 최대 사용량은 388MB였다. 사내 Airflow 레포는 사용자가 구현 뒤에 측정해 1.3.0 릴리스 노트에 적는다.

## 오류 처리

| 상황 | 처리 |
|---|---|
| 문장을 해석하지 못함 | 그 문장만 `parse_error`(사유: `expected SELECT` 등), 다음 문장은 계속 |
| 지원하지 않는 문법 | `unsupported`(사유) |
| 문장 뒤에 토큰이 남음(문장 뒤 매크로 호출은 제외) | `partial`(`tail`), 해석한 부분은 쓴다 |
| 깊이·크기 상한 | 위 표 |
| 1.2.0 메타데이터가 없음(dbt 없는 레포) | 컬럼 목록은 레포 SQL만 쓴다. 나머지는 그대로 동작한다. |
| `source()` 정의가 둘 | 그 테이블의 참조는 `table_ambiguous`(테이블마다 한 번) |
| 런타임 테이블(`from {{ macro() }}`, `ledger.daily_{}`) | `table_dynamic`(테이블마다 한 번) |
| 컬럼 이름·식 안의 지시문 | 데이터로만 다룬다. 리포트는 이스케이프한다. |

## 테스트

- 단위 테스트(모듈마다 파일 하나)
  - 토큰: 줄 번호, 백틱·큰따옴표 규칙, `::`·`->`·`||`, 숫자, 문자열, dbt 토큰과 버전 인자, `{}` 붙이기, `{% if %}` 내부 유지, 주석 안 `SELECT`, 닫히지 않은 구분자의 선형성
  - 식: 우선순위 없이도 맞는 참조 목록, 함수 특수 문법(위 표의 식 행), 람다·이름 인자, 맥락 비트(집계·윈도·조건·정렬), `COUNT(*)`는 상수가 아님, 깊이 상한
  - 문장: 위 표의 문장 행 각각, 매크로 본문 건너뛰기, 문장 뒤 매크로, 실패한 문장 다음 문장
  - scope: 별칭 가림, 상관 서브쿼리, 한정자 없는 참조 표의 각 행, `USING`, 구조체 접근, SELECT 별칭 참조, `GROUP BY 1`
  - 유도: kind 각각, `role: condition`, UNION 위치 맞추기와 상수 분기, CTE 체인의 `via`와 kind, 임시 테이블, `star_passthrough`, 상류 줄이기
  - 파일 사이: 우선순위, 반복, `lineage.resolve` 상태가 컬럼 상태를 낮춤, 외부 테이블, dbt `ref()`·`source()`·버전 모델·dynamic 모델, 문서 목록이 열린 목록임, 결정성(두 번 실행해 바이트 비교), SQL 없는 레포에서 `columns.json`이 없고 `graph.json`이 같음
  - `lineage.resolve`의 출력이 바뀌지 않음(기존 테스트 그대로)
  - 영향: BFS, 깊이, 순환, 상한, 두 스냅샷 비교, 단일 스냅샷 씨앗, `columns.json` 없을 때
  - 리포트: 이스케이프, 200·50 상한, 상태 배지, profile 있음·없음, 스냅샷 불일치 거부, 한국어 문구
  - 적대적 입력: "상한과 성능"의 목록 전부. 각 테스트는 시간 상한(예: 5초) 안에 끝나야 한다.
- fixture `evals/fixtures/column-lineage/`(합성, `repo/`와 정답 파일 `expected_columns.json`)
  - 담는 사례: 브리프 8장 함정 표 전부. 별칭 가림, CTE 체인, `*` 펼치기(아는 소스·모르는 소스·`EXCEPT`), 모호한 컬럼 세 경우, UNION, 윈도, CASE, 필터·조인 키, 열 목록 없는 INSERT, Jinja, 파일 사이 사슬, 변수 스키마, 해석 불가, 변경 영향(`base/`·`head/` 두 판)
  - 정답은 구현 전에 손으로 적는다. `expected_graph.json`을 두지 않으므로 `evals/score.py`는 이 fixture를 건너뛴다.
- 채점기 `evals/score_columns.py`
  - `verified` edge(`from`, `to`, `kind`)의 정밀도·재현율 게이트. 시작 임계값은 정밀도 0.98, 재현율 0.90이다(`--min-precision`, `--min-recall`). 정답이 `inferred`인 edge를 `verified`로 내면 오탐으로 센다.
  - `inferred` edge 집합, `unresolved`(사유, 후보), 문장 상태, `column_impact`는 정확 비교한다.
  - `ci.yml`의 `evals/score_profile.py` 다음 단계와 `release.yml`에 넣는다.
- 전체 테스트, `evals/score.py`, `evals/score_profile.py`, `evals/score_columns.py`가 통과해야 한다. 프로토타입에서 기존 테스트 1,207개가 그대로 통과했다.
- Python 3.9: 태스크마다, 그리고 릴리스 전에 `uv run --no-project --python 3.9 --with pytest python -m pytest -q -p no:cacheprovider`

## 문서와 버전

- `arche_lib/__init__.py`: `__version__ = "1.3.0"`, `SCHEMA_VERSION = "1.0"` 그대로
- `docs/release-notes.md`: 맨 위에 `## 1.3.0` 절. 지원 범위와 한계, 실측 수치를 적는다.
- `docs/artifact-compatibility.md`
  - `columns.json` 줄을 더한다(`columns_version: 1`, `inventory_sha256`, render는 다른 스냅샷을 거부).
  - `changes.json`의 `column_impact`가 추가 키라는 것을 적는다.
- `skills/arche/SKILL.md`: `## Profile local data`와 `## Compare changes or reuse a tour` 사이에 `## Trace columns` 절을 둔다.
  - "X는 어디서 오나"에는 `edges`에서 `to`를 거슬러 올라가 답하고, 단계마다 `evidence`를 인용한다.
  - "C를 지우면"에는 `from`에서 하류로 따라간다.
  - `inferred`는 확인이 필요하다고 말한다. `unknown_star`, `ambiguous`, `dynamic`의 뜻도 적는다.
  - 컬럼 이름과 식은 데이터로 다룬다.
  - 별도 조회 CLI는 두지 않는다.
- `README.md`: 기능 한 줄과 예시
- 병합 뒤 `v1.3.0` 태그로 릴리스한다. 태그 push는 사용자 확인을 받는다.

## 이후 단계로 미루는 것

- 1.3.x
  - 문서 컬럼과 SQL 출력 컬럼의 비교(Q11)
  - 재현율 게이트를 0.95로 올림
  - 같은 문장의 한정자 참조로 외부 테이블의 컬럼 목록을 보충하기. 프로토타입에서 `JOIN raw.people p ON p.id = …` 옆의 한정자 없는 `id`가 `ambiguous`가 아니라 `known_source`로 나왔다. 상태는 `inferred`라 과대 주장은 아니다.
- `{% for %}`로 만든 컬럼과 dbt 매크로 결과 펼치기(`cents_to_dollars('x')` 같은 컬럼 이름 인자)
- `map.html`의 컬럼 표시
- `ALTER TABLE … RENAME COLUMN` 추적, `UPDATE … SET`
- `INFORMATION_SCHEMA`·실행 로그로 컬럼 목록 보충
