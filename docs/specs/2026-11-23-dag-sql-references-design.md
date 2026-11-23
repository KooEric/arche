# DAG↔SQL 파일 참조 설계

2026-11-23 제안. 대상 브랜치는 `erkoo/dag-sql-refs`(GitHub `main` 기준)이다.

## 왜 바꾸는가

Arche를 사내 Airflow DAG 레포의 팀 폴더 하나에 실행한 결과는 다음과 같다.

| 항목 | 결과 |
|---|---|
| 실행 시간 | 5.5초 |
| 분석된 파일 | Python 131개 |
| 건너뛴 파일 | SQL 514개와 HQL 51개가 `unsupported_lang` |
| 의존성 edge | 0개 |

edge가 0개인 이유는 구조에 있다. 어댑터 계약은 "파일이 다른 파일을 import한다"만 표현하고 edge 종류는 `IMPORTS` 하나다. DAG 레포의 Python 파일은 서로 거의 import하지 않는다. 실제 연결은 `sql="x.sql"`처럼 문자열 속 경로에 있다.

## 목표와 범위

**목표.** DAG 레포에서 두 질문에 근거 줄과 함께 답한다.

1. 이 Python 파일은 어떤 SQL 파일을 쓰는가
2. 이 SQL 파일은 어느 Python 파일이 쓰는가

**이번 범위.**

1. `.sql`·`.hql` 파일을 그래프 노드로 만든다.
2. Python 문자열에서 SQL 경로를 모아 레포 파일로 해석한다.
3. 결과를 `REFERENCES` edge로 기록하고 리포트·지도·리비전 비교에 반영한다.

**하지 않는 것.** 각각 다음 하위 프로젝트로 넘긴다.

- SQL 내용 해석과 테이블 lineage
- `task >> task` 의존성과 task 노드
- `template_searchpath` 해석
- operator 이름·`task_id` 같은 호출 맥락
- 모노레포용 `--include`와 배치 실행
- 사내 배포 가이드

## 대상 패턴

실제 레포에서 확인된 형태는 세 가지다.

| 형태 | 예 | 처리 |
|---|---|---|
| A. operator 인자의 리터럴 경로 | `SQLExecuteQueryOperator(sql="sql/x.sql")` | 해석한다 |
| C. 직접 읽기 | `open(os.path.join(BASE, "sql", "x.sql"))`, `read_sql("x.sql")` | 해석한다 |
| D. 동적 조합 | `f"{table}.sql"` | 해석하지 않고 `dynamic`으로 기록한다 |

A와 C를 한 규칙으로 처리하기 위해 호출 위치를 보지 않는다. 문자열이 어느 함수에 넘겨졌는지는 판단에 쓰지 않는다. 사내 헬퍼 이름을 설정으로 받지 않아도 된다.

## 구성 요소

| 단위 | 책임 | 의존 |
|---|---|---|
| `langs/sql.py` (신규) | `.sql`·`.hql`을 언어 `sql`로 등록한다. `extract`는 빈 import와 빈 심볼을 돌려준다. | 없음 |
| `langs/python_filerefs.py` (신규) | Python AST에서 경로 문자열을 모아 `file_refs` 레코드로 돌려준다. | `ast` |
| `fileref.py` (신규) | 경로 문자열 하나를 레포 파일로 해석한다. 언어와 무관하다. | 전체 경로 목록 |
| `langs/python.py` (수정) | `extract` 결과에 `file_refs` 키를 추가한다. HTTP 라우트를 더하는 기존 방식과 같다. | `python_filerefs` |
| `graph.py` (수정) | `file_refs`를 `REFERENCES` edge로 만든다. | `fileref` |

`file_refs`는 `imports`와 섞지 않는다. import 해석기와 감사 규칙이 import 레코드의 형태에 의존하기 때문이다.

## 수집 규칙

`python_filerefs.file_refs(path, text)`는 다음 레코드를 돌려준다.

```
{"spec": "sql/x.sql", "line": 12, "quote": "...", "dynamic": False}
```

- **리터럴.** 문자열 상수가 `.sql` 또는 `.hql`로 끝나고(대소문자 무시) 공백과 줄바꿈이 없으면 수집한다. Jinja `{{ … }}` 안의 공백은 허용한다. 길이는 200자 이하로 제한한다.
- **docstring 제외.** docstring을 포함해 문장으로 홀로 놓인 문자열은 수집하지 않는다.
- **경로 조합.** `os.path.join(...)`, `.joinpath(...)`, `Path(...) / "a" / "b"`는 마지막 인자부터 거슬러 올라가며 연속된 리터럴 조각만 `/`로 잇는다. `os.path.join(BASE, "sql", "x.sql")`의 `spec`은 `sql/x.sql`이다. 조합에 쓰인 리터럴은 따로 다시 수집하지 않는다.
- **f-string.** 값이 들어가는 자리를 `{}`로 바꾼 문자열을 `spec`으로 둔다. `f"sql/{t}.sql"`의 `spec`은 `sql/{}.sql`이다.
- **동적.** `spec`에 `{`, `%`, `*`, `?` 중 하나가 있으면 `dynamic: True`다. f-string, `"%s.sql" % x`, `"{}.sql".format(x)`, Jinja 템플릿, glob 패턴이 모두 여기에 든다.

## 해석 규칙

`fileref.resolve(spec, importer_path, sql_paths)`는 위에서부터 처음 맞는 규칙을 적용한다. `spec`은 먼저 정규화한다(`./` 제거, 역슬래시를 `/`로, 앞쪽 `/` 제거). `..`은 규칙 1에서만 따른다.

| 순서 | 규칙 | 상태 | `rule` / `reason` |
|---|---|---|---|
| 0 | `dynamic` 레코드 | `unresolved` | `reason: dynamic` |
| 1 | 참조한 파일의 폴더 기준 상대 경로가 존재 | `verified` | `rule: relative` |
| 2 | 스캔 루트 기준 경로가 존재 | `verified` | `rule: root` |
| 3 | 경로 조각 경계에서 끝부분이 일치하는 SQL 파일이 하나 | `inferred` | `rule: suffix` |
| 4 | 끝부분이 일치하는 SQL 파일이 여럿 | `unresolved` | `reason: ambiguous`, `candidates`에 정렬된 경로 |
| 5 | 일치 없음 | `unresolved` | `reason: not_found` |

규칙 3의 "경로 조각 경계"는 `x.sql`이 `sql/x.sql`과는 맞고 `sql/ax.sql`과는 맞지 않는다는 뜻이다. 적힌 경로가 레포 경로보다 길면(예: `/opt/airflow/dags/a/sql/x.sql`) 앞 조각을 하나씩 떼어 가며 처음 일치가 나오는 가장 긴 꼬리로 판단한다. 폴더가 적힌 경로는 마지막 폴더와 파일 이름까지는 남긴다. 파일 이름만으로 맞추는 것은 폴더 없이 이름만 적힌 경우뿐이다. 대상은 스캔에서 분석된 SQL 파일로 한정한다. `skipped`인 파일(예: `too_large`)은 대상이 아니다.

상태 이름은 기존 `IMPORTS` edge와 같다(`verified`, `inferred`, `unresolved`).

## edge 형식

```
{"kind": "REFERENCES", "from": "file:dags/a.py", "to": "file:dags/sql/x.sql",
 "origin": "script", "status": "verified", "rule": "relative",
 "evidence": {"path": "dags/a.py", "line": 12, "quote": "..."}}
```

미해결이면 `to`는 `null`이고 `spec`, `reason`, 필요하면 `candidates`를 둔다. 같은 줄에서 같은 대상을 두 번 가리키면 하나로 합친다.

## 기존 기능에 미치는 영향

| 기능 | 현재 동작 | 변경 |
|---|---|---|
| 감사 (`audit.py`) | `IMPORTS`가 아닌 edge를 건너뛴다. | 없음. 순환·반복 import 규칙은 그대로다. |
| 리비전 비교 (`changes.py`) | 해석된 `IMPORTS`만 비교한다. | 해석된 `REFERENCES`도 비교한다. |
| 지도 (`atlas.py`) | 그래프 edge를 종류와 무관하게 `import` 연결로 센다. | `REFERENCES`는 `atlas.json`에 `reference` 연결로 따로 센다. 뷰어는 import와 같은 선으로 그린다. 선 모양 구분은 이번 범위가 아니다. |
| 리포트 (`report.py`) | "확정된 import" 목록은 `IMPORTS`만 본다. | 그대로 둔다. 아래 두 목록을 추가한다. |
| 폴더 요약 (`brief.py`) | 폴더 간 import 수를 낸다. | 폴더 간 참조 수를 한 구역 추가한다. |

리포트에 추가하는 것은 두 가지다.

- **SQL 파일 상세.** SQL 파일마다 그 파일을 참조하는 Python 파일과 근거 줄을 보여준다. 기존 파일 상세가 edge 종류를 가리지 않으므로 새 코드 없이 나온다. 제목은 "Imported by" 그대로다.
- **정적 참조가 없는 SQL.** 해석된 `REFERENCES`가 하나도 없는 SQL 파일 목록이다. 제목에 "정적 참조 없음"이라고 쓰고 "미사용"이라고 쓰지 않는다. 스캔 범위 안에 `dynamic` 참조가 하나라도 있으면 목록 위에 그 건수와 "동적 참조로 쓰일 수 있음"을 표시한다.

`not_found` 참조는 기록하되 리포트에서는 접힌 구역에 둔다. 삭제된 SQL을 가리키는 죽은 참조와 쓰기용 파일명(`"output.sql"`)이 섞여 있어 기본으로 펼치지 않는다.

`graph.json`에 새 edge 종류와 새 언어가 생기므로 [artifact-compatibility.md](../artifact-compatibility.md)에 추가 사항으로 적는다. 기존 필드는 바꾸지 않는다.

## 오류 처리

- Python 파일이 파싱에 실패하면 기존처럼 `parse_error`로 건너뛴다. `file_refs`도 없다.
- 1MB를 넘는 SQL 파일은 기존처럼 `too_large`로 건너뛴다. 그 파일을 가리키는 참조는 `not_found`가 된다.
- SQL 파일은 내용을 해석하지 않으므로 파싱 오류가 없다. 인코딩 문제는 기존 인벤토리 규칙을 따른다.

## 검증

**단위 테스트.** 구현보다 먼저 쓴다.

- 수집기: 리터럴, docstring 제외, `os.path.join`, `Path /`, f-string, `%`, `.format`, Jinja, 조합에 쓰인 리터럴의 중복 수집 없음
- 해석기: 표의 규칙 0~5 각각, 경로 조각 경계, 정규화, `..`

**fixture.** `evals/fixtures/airflow-sql`을 합성으로 만든다. 사내 코드는 넣지 않는다. A형, C형, 경로 조합, D형, 후보가 여럿인 경우, 없는 파일, 참조가 없는 SQL을 담고 기대 edge를 기존 언어 fixture와 같은 방식으로 CI에서 채점한다.

**실측.** 사내 팀 폴더 재실행은 사용자가 한다. 성공 기준은 다음과 같다.

1. 해석된 `REFERENCES` edge가 0개가 아니다.
2. 해석된 edge에서 무작위로 고른 30개 중 오탐이 3개 이하다.
3. 리포트에 `dynamic` 건수가 나온다.
4. 기존 테스트 478개가 모두 통과한다.

기준 2를 넘기면 수집 규칙을 좁히기 전에 오탐의 형태를 먼저 분류한다.
