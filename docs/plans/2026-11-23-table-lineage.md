# SQL 테이블 lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQL·HQL 파일에서 테이블 읽기·쓰기를 뽑아, 읽는 파일에서 쓰는 파일로 가는 `READS` edge와 레포 안에 쓰는 파일이 없는 `external_tables` 목록을 만든다.

**Architecture:** `langs/sql_tables.py`가 주석과 문자열을 가린 SQL 텍스트에서 키워드 뒤의 테이블 이름만 읽어 `tables` 레코드를 낸다. 언어와 무관한 `lineage.py`가 읽기 레코드를 쓰는 파일 색인과 맞추고, `graph.py`가 결과를 `READS` edge와 `external_tables`로 `graph.json`에 더한다. 파일 상세와 의존성 지도는 edge 종류를 가리지 않으므로 새 edge가 그대로 나타나고, 종류를 가려 읽는 곳(채점기, 리비전 비교, 지도 부품 연결, 폴더 요약)만 고친다.

**Tech Stack:** Python 3.9+ 표준 라이브러리(`re`), pytest.

이 문서는 [SQL 테이블 lineage 설계](../specs/2026-11-23-table-lineage-design.md)의 구현 계획이다. 브랜치는 `erkoo/table-lineage`이고 `erkoo/dag-sql-refs`(PR 14, 94f3ad6) 위에 쌓는다. Task 1과 2의 추출기·연결 코드는 임시 폴더에서 Task 4의 합성 fixture와 이 문서의 테스트 사례로 실행해 적힌 결과가 나오는 것을 확인했다. Task 3 이후의 코드는 실행해 보지 않았다. 시작 시점의 전체 테스트는 544개 통과다.

## Global Constraints

- Python 3.9에서 동작해야 한다. `match` 문, 런타임의 `X | Y` 타입 표기, 3.10 이상 전용 API를 쓰지 않는다.
- 표준 라이브러리만 쓴다. 외부 SQL 파서를 쓰지 않는다. `tests/test_stdlib_only.py`가 검사한다.
- 대상 레포는 읽기만 한다. 프로젝트 코드를 실행하지 않는다.
- Python 문자열 안의 SQL은 읽지 않는다. SQL·HQL 파일만 읽는다.
- edge 상태 이름은 기존과 같다: `verified`, `inferred`, `unresolved`.
- `READS` edge의 `rule` 값은 `name`, `table_name`이다. `reason` 값은 `dynamic`, `ambiguous`다.
- 테이블 이름은 소문자로 정규화하고, 변수 자리는 `{}`로 적는다.
- 기존 `IMPORTS`·`REFERENCES` edge의 필드와 `evals/score.py`의 정밀도·재현율 계산은 바꾸지 않는다. `READS`는 그 계산에 들어가지 않는다.
- 같은 입력이면 `graph.json`이 바이트 단위로 같아야 한다.
- fixture에 사내 코드를 넣지 않는다. 모두 합성이다.
- 커밋 메시지는 이 저장소의 관례대로 영어 한 문장으로 쓰고, 귀속(Co-Authored-By) 줄은 붙이지 않는다.
- 모든 명령은 worktree 루트에서 실행한다.

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `skills/arche/scripts/arche_lib/langs/sql_tables.py` | 신규 | SQL 텍스트 가리기와 테이블 읽기·쓰기 추출 |
| `skills/arche/scripts/arche_lib/langs/sql.py` | 수정 | `extract` 결과에 `tables`, `degraded` 추가 |
| `skills/arche/scripts/arche_lib/lineage.py` | 신규 | 읽는 테이블을 쓰는 파일과 맞추기 |
| `skills/arche/scripts/arche_lib/graph.py` | 수정 | `READS` edge와 `external_tables` 생성 |
| `evals/score.py` | 수정 | `reads`, `external_tables` 정답 집합 채점 |
| `evals/fixtures/airflow-sql/` | 수정 | SQL 두 개 추가, 정답 확장 |
| `skills/arche/scripts/arche_lib/changes.py` | 수정 | 리비전 비교에 `READS` 포함 |
| `skills/arche/scripts/arche_lib/atlas.py` | 수정 | 부품 연결 종류 `read` |
| `skills/arche/assets/map-template.html` | 수정 | `read` 연결을 두 번 그리지 않음 |
| `skills/arche/scripts/brief.py` | 수정 | 폴더 간 테이블 읽기 구역 |
| `skills/arche/scripts/arche_lib/report.py` | 수정 | 관계 줄의 테이블 이름, "Tables" 구역 |
| `skills/arche/scripts/arche_lib/i18n.py` | 수정 | 새 문구의 한국어 |
| `README.md`, `docs/artifact-compatibility.md`, `docs/release-notes.md`, `skills/arche/SKILL.md` | 수정 | 문서 |

---

### Task 1: SQL 테이블 추출기

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/sql_tables.py`
- Modify: `skills/arche/scripts/arche_lib/langs/sql.py`
- Test: `tests/unit/test_sql_tables.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `sql_tables.mask(text: str) -> Tuple[str, bool]` (가린 텍스트, 닫히지 않은 따옴표·주석 여부)
  - `sql_tables.tables(path: str, text: str) -> dict`. 반환은 `{"tables": [{"table": str, "access": "read"|"write", "line": int, "quote": str, "dynamic": bool, "temp": bool}], "degraded": bool}`이고 `tables`는 `(line, access, table)` 순이다.
  - `sql.extract(path, text)`의 결과에 `"tables"` 키가 생기고, 가리기가 닫히지 않았으면 `"degraded": True`가 붙는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_sql_tables.py`:

```python
from arche_lib.langs import sql
from arche_lib.langs.sql_tables import mask, tables


def _found(source):
    return [(item["access"], item["table"]) for item in tables("q.sql", source)["tables"]]


def test_mask_blanks_comments_strings_and_jinja_tags_and_keeps_newlines():
    masked, unterminated = mask("a -- x 'q\nb /* y\nz */ c 'it''s' d {% if x %}e{# n #}f")

    assert masked == "a        \nb     \n     c         d           e       f"
    assert unterminated is False


def test_an_unterminated_string_marks_the_file_degraded_and_keeps_what_was_read():
    result = tables("q.sql", "select * from q.r where s = 'unterminated")

    assert [(item["access"], item["table"]) for item in result["tables"]] == [("read", "q.r")]
    assert result["degraded"] is True


def test_a_record_carries_its_line_and_quote():
    record = tables("q.sql", "SELECT 1\n  FROM   acct.ledger  -- note\n")["tables"][0]

    assert record == {"table": "acct.ledger", "access": "read", "line": 2, "quote": "FROM   acct.ledger  -- note",
                      "dynamic": False, "temp": False}


def test_from_and_join_read_tables_and_drop_aliases():
    assert _found("SELECT *\nFROM a.b x\nJOIN c.d AS y ON 1=1") == [("read", "a.b"), ("read", "c.d")]


def test_comma_lists_subqueries_and_table_functions():
    source = ("select * from a.x t1, b.y, (select 1 from c.z) q left join `d`.`W` as w on 1=1 "
              "join lateral unnest(arr) u on true")

    assert _found(source) == [("read", "a.x"), ("read", "b.y"), ("read", "c.z"), ("read", "d.w")]
    assert _found("select * from generate_series(1, 3) g, t.real") == []


def test_from_inside_a_function_call_is_not_a_table():
    source = "select substring(n from 1), trim(both ' ' from n) from e.t where d in (select d from f.g)"

    assert _found(source) == [("read", "e.t"), ("read", "f.g")]


def test_cte_names_are_not_tables():
    source = "with a as (select 1 from x.one), b as (select * from a) select * from b join a on 1=1"

    assert _found(source) == [("read", "x.one")]


def test_lateral_view_is_not_a_table():
    assert _found("select * from t.base lateral view explode(arr) e as item") == [("read", "t.base")]


def test_hive_from_first_insert():
    source = "FROM src.t a INSERT OVERWRITE TABLE dst.x PARTITION (dt='1') SELECT a.* WHERE a.k > 0"

    assert _found(source) == [("read", "src.t"), ("write", "dst.x")]


def test_create_insert_delete_and_merge_write_tables():
    source = (
        "CREATE EXTERNAL TABLE IF NOT EXISTS w.h (a int) USING parquet;\n"
        "CREATE OR REPLACE VIEW v.v AS SELECT * FROM w.h;\n"
        "DROP TABLE IF EXISTS w.old;\n"
        "DELETE FROM w.h WHERE a = 1;\n"
        "INSERT OVERWRITE LOCAL DIRECTORY '/x' SELECT * FROM w.h"
    )

    assert _found(source) == [("write", "w.h"), ("read", "w.h"), ("write", "v.v")]
    assert _found("MERGE INTO tgt.t AS t USING src.s AS s ON t.k = s.k WHEN MATCHED THEN UPDATE SET v = s.v") == [
        ("read", "src.s"), ("write", "tgt.t")]


def test_temporary_tables_are_marked():
    source = ("CREATE TEMP VIEW s.tv AS SELECT 1;\ncreate temporary table tmp_a as select * from a.b;\n"
              "select * from tmp_a;\nselect * from a.b")

    assert [(item["line"], item["access"], item["table"], item["temp"]) for item in tables("q.sql", source)["tables"]] == [
        (1, "write", "s.tv", True), (2, "read", "a.b", False), (2, "write", "tmp_a", True), (3, "read", "tmp_a", False)]


def test_names_are_unquoted_lowercased_and_limited_to_three_parts():
    assert _found('select * from `Acct`.`Ledger` join "S"."T" on 1=1 join 1abc on 1=1') == [
        ("read", "acct.ledger"), ("read", "s.t")]
    assert _found("select * from cat.sch.t3 join a.b.c.d on 1=1") == [("read", "cat.sch.t3")]


def test_run_time_values_become_placeholders():
    source = "select * from ${hiveconf:db}.t1 join $db.t2 on 1=1 join tmp_${run} on 1=1"
    found = {item["table"]: item["dynamic"] for item in tables("q.sql", source)["tables"]}

    assert found == {"{}.t1": False, "{}.t2": False, "tmp_{}": True}


def test_jinja_blocks_are_read_through():
    source = "{% for d in days %} insert into a.b select * from c.d_{{ d }} {% endfor %}"

    assert [(item["access"], item["table"], item["dynamic"]) for item in tables("q.sql", source)["tables"]] == [
        ("read", "c.d_{}", True), ("write", "a.b", False)]


def test_the_sql_adapter_reports_tables_and_degraded():
    assert [item["table"] for item in sql.extract("q.sql", "select * from a.b")["tables"]] == ["a.b"]
    assert "degraded" not in sql.extract("q.sql", "select * from a.b")
    assert sql.extract("q.sql", "select 'open")["degraded"] is True
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_sql_tables.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'arche_lib.langs.sql_tables'`

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/langs/sql_tables.py`:

```python
"""Tables a SQL file reads and writes, found by keyword. Not a parser."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

MAX_QUOTE_CHARS = 200
MAX_NAME_PARTS = 3
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\$\{[^}\n]*\}|\$[A-Za-z_]\w*", re.S)
_PART = r"(?:`[^`\n]*`|\"[^\"\n]*\"|(?:[\w$]|\{\})+)"
_TOKEN = re.compile(rf"{_PART}(?:\s*\.\s*{_PART})*|[(),;]")
_CREATE_MODIFIERS = frozenset({"OR", "REPLACE", "EXTERNAL", "GLOBAL", "TRANSIENT", "MATERIALIZED", "UNLOGGED"})
_TEMP_WORDS = frozenset({"TEMPORARY", "TEMP"})
# Words that can follow a table name; never an alias and never a table.
_KEYWORDS = frozenset("""
    ANTI AS CLUSTER CROSS DELETE DIRECTORY DISTRIBUTE EXCEPT FETCH FOR FROM FULL GROUP HAVING INNER INSERT
    INTERSECT INTO JOIN LATERAL LEFT LIMIT LOCAL MERGE NATURAL OFFSET ON ORDER OUTER PARTITION PIVOT QUALIFY
    RIGHT SELECT SEMI SET SORT TABLE TABLESAMPLE UNION UNNEST UNPIVOT USING VALUES WHEN WHERE WINDOW WITH
""".split())
_CLOSERS = {"/*": "*/", "{%": "%}", "{#": "#}"}


def mask(text: str) -> Tuple[str, bool]:
    """Blank comments, string bodies and Jinja block tags, keeping every newline. (masked, unterminated)."""
    out: List[str] = []
    i, size, unterminated = 0, len(text), False
    while i < size:
        pair = text[i:i + 2]
        if pair == "--":
            end = text.find("\n", i)
            end = size if end < 0 else end
        elif pair in _CLOSERS:
            found = text.find(_CLOSERS[pair], i + 2)
            unterminated = unterminated or found < 0
            end = size if found < 0 else found + 2
        elif text[i] == "'":
            end = i + 1
            while end < size and (text[end] != "'" or text[end:end + 2] == "''"):
                end += 2 if text[end] == "'" else 1
            unterminated = unterminated or end >= size
            end = min(end + 1, size)
        else:
            out.append(text[i])
            i += 1
            continue
        out.append("".join(char if char == "\n" else " " for char in text[i:end]))
        i = end
    return "".join(out), unterminated


def _placeholders(masked: str) -> str:
    """Each run-time value becomes `{}`; newlines inside it are kept after it."""
    return _PLACEHOLDER.sub(lambda match: "{}" + "\n" * match.group().count("\n"), masked)


def _name(token: str) -> Optional[str]:
    parts = [part.strip().strip('`"').lower() for part in token.split(".")]
    if not 1 <= len(parts) <= MAX_NAME_PARTS or not all(parts) or parts[0][0].isdigit():
        return None
    return ".".join(parts)


def tables(path: str, text: str) -> dict:
    """{"tables": [{"table", "access", "line", "quote", "dynamic", "temp"}], "degraded": bool}."""
    masked, unterminated = mask(text)
    source = _placeholders(masked)
    lines = text.splitlines()
    tokens = [(match.group(), match.start()) for match in _TOKEN.finditer(source)]
    words = [token.upper() for token, _ in tokens]
    found: Dict[Tuple[str, str], dict] = {}
    ctes: Set[str] = set()
    selects = [True]  # one flag per open parenthesis: has SELECT or DELETE been seen at this depth
    merging = False

    def is_name(index: int) -> bool:
        return index < len(tokens) and words[index] not in _KEYWORDS and tokens[index][0] not in "(),;"

    def record(index: int, access: str, temp: bool = False) -> None:
        name = _name(tokens[index][0])
        if name is None:
            return
        line = source.count("\n", 0, tokens[index][1]) + 1
        quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if line <= len(lines) else ""
        found.setdefault((name, access), {"table": name, "access": access, "line": line, "quote": quote,
                                         "dynamic": "{}" in name.rsplit(".", 1)[-1], "temp": temp})

    def read_from(index: int, many: bool) -> None:
        """Names after FROM or JOIN: `a`, `a x`, `a AS x`, and for FROM also `a, b`."""
        while is_name(index) and not (index + 1 < len(tokens) and tokens[index + 1][0] == "("):
            record(index, "read")
            index += 1
            if index < len(tokens) and words[index] == "AS":
                index += 1
            if is_name(index):
                index += 1
            if not many or index >= len(tokens) or tokens[index][0] != ",":
                return
            index += 1

    for index, word in enumerate(words):
        if word == "(":
            selects.append(False)
            if index >= 2 and words[index - 1] == "AS" and words[index - 3:index - 2] in (["WITH"], ["RECURSIVE"], [","]):
                ctes.add(tokens[index - 2][0].lower())
        elif word == ")":
            if len(selects) > 1:
                selects.pop()
        elif word == ";":
            merging = False
        elif word in ("SELECT", "DELETE"):
            selects[-1] = True
        elif word == "FROM":
            if index and words[index - 1] == "DELETE":
                if is_name(index + 1):
                    record(index + 1, "write")
            elif selects[-1]:
                read_from(index + 1, many=True)
        elif word == "JOIN":
            read_from(index + 1, many=False)
        elif word == "USING" and merging:
            read_from(index + 1, many=False)
        elif word in ("INTO", "OVERWRITE") and index and words[index - 1] in ("INSERT", "MERGE"):
            merging = merging or words[index - 1] == "MERGE"
            target = index + 2 if words[index + 1:index + 2] == ["TABLE"] else index + 1
            if is_name(target):
                record(target, "write")
        elif word == "CREATE":
            cursor, temp = index + 1, False
            while cursor < len(words) and (words[cursor] in _CREATE_MODIFIERS or words[cursor] in _TEMP_WORDS):
                temp = temp or words[cursor] in _TEMP_WORDS
                cursor += 1
            if words[cursor:cursor + 1] in (["TABLE"], ["VIEW"]):
                cursor += 1
                if words[cursor:cursor + 3] == ["IF", "NOT", "EXISTS"]:
                    cursor += 3
                if is_name(cursor):
                    record(cursor, "write", temp)
    records = [item for item in found.values() if not (item["access"] == "read" and item["table"] in ctes)]
    return {"tables": sorted(records, key=lambda item: (item["line"], item["access"], item["table"])),
            "degraded": unterminated}
```

`tables` 함수는 50줄을 넘는다. 토큰 목록과 괄호 상태를 함께 쓰는 한 번의 순회라서 나누지 않았다. 검토에서 나누라는 지적이 나오면 `CREATE` 분기와 `FROM` 분기를 모듈 함수로 뺀다.

`skills/arche/scripts/arche_lib/langs/sql.py` 전체를 다음으로 바꾼다.

```python
"""SQL and HQL files: graph nodes plus the tables each file reads and writes."""
from __future__ import annotations

from typing import List

from .sql_tables import tables


def extract(path: str, text: str) -> dict:
    found = tables(path, text)
    degraded = {"degraded": True} if found["degraded"] else {}
    return {"imports": [], "symbols": [], "tables": found["tables"], **degraded}


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_sql_tables.py tests/test_stdlib_only.py -q`
Expected: PASS

Run: `python3 -m pytest -q`
Expected: 전체 통과

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/sql_tables.py skills/arche/scripts/arche_lib/langs/sql.py tests/unit/test_sql_tables.py
git commit -m "Read the tables a SQL file reads and writes"
```

---

### Task 2: 연결 규칙 `lineage`

**Files:**
- Create: `skills/arche/scripts/arche_lib/lineage.py`
- Test: `tests/unit/test_lineage.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `lineage.index(writes: Iterable[Tuple[str, str]]) -> dict`. 입력은 `(파일 경로, 테이블)` 쌍이고, 반환은 `{"files": {테이블: 정렬된 파일 목록}, "names": {마지막 조각: 정렬된 테이블 목록}}`이다.
  - `lineage.resolve(table: str, dynamic: bool, writers: dict) -> List[dict]`. 각 항목은 `{"status": "verified"|"inferred", "to": 파일, "rule": ...}`, `{"status": "unresolved", "spec": 테이블, "reason": ..., "candidates"?: [...]}`, `{"status": "external", "table": 테이블}` 중 하나다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_lineage.py`:

```python
from arche_lib import lineage

WRITERS = lineage.index([("w1.sql", "{}.ledger"), ("w2.sql", "acct.ledger"), ("w3.sql", "x.only"),
                         ("w4.sql", "acct.ledger")])


def test_index_lists_sorted_files_per_table_and_tables_per_name():
    assert WRITERS == {
        "files": {"{}.ledger": ["w1.sql"], "acct.ledger": ["w2.sql", "w4.sql"], "x.only": ["w3.sql"]},
        "names": {"ledger": ["acct.ledger", "{}.ledger"], "only": ["x.only"]},
    }


def test_a_dynamic_table_name_is_never_resolved():
    assert lineage.resolve("ledger_{}", True, WRITERS) == [
        {"status": "unresolved", "spec": "ledger_{}", "reason": "dynamic"}]


def test_a_fixed_name_is_verified_against_every_file_that_writes_it():
    assert lineage.resolve("acct.ledger", False, WRITERS) == [
        {"status": "verified", "to": "w2.sql", "rule": "name"}, {"status": "verified", "to": "w4.sql", "rule": "name"}]


def test_a_variable_schema_is_inferred_from_the_table_name():
    assert lineage.resolve("{}.only", False, WRITERS) == [{"status": "inferred", "to": "w3.sql", "rule": "table_name"}]
    assert lineage.resolve("only", False, WRITERS) == [{"status": "inferred", "to": "w3.sql", "rule": "table_name"}]


def test_the_same_variable_schema_on_both_sides_is_not_verified():
    assert lineage.resolve("{}.ledger", False, WRITERS) == [
        {"status": "unresolved", "spec": "{}.ledger", "reason": "ambiguous", "candidates": ["acct.ledger", "{}.ledger"]}]
    alone = lineage.index([("w.sql", "{}.daily")])
    assert lineage.resolve("{}.daily", False, alone) == [{"status": "inferred", "to": "w.sql", "rule": "table_name"}]


def test_a_fixed_schema_matches_a_writer_whose_schema_is_variable():
    assert lineage.resolve("other.ledger", False, WRITERS) == [
        {"status": "inferred", "to": "w1.sql", "rule": "table_name"}]


def test_a_fixed_schema_never_matches_another_fixed_schema():
    assert lineage.resolve("y.only", False, WRITERS) == [{"status": "external", "table": "y.only"}]


def test_a_table_nothing_writes_is_external():
    assert lineage.resolve("none.t", False, WRITERS) == [{"status": "external", "table": "none.t"}]
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_lineage.py -q`
Expected: FAIL with `ImportError: cannot import name 'lineage'`

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/lineage.py`:

```python
"""Match a table a file reads to the files that write it. Language independent."""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple


def index(writes: Iterable[Tuple[str, str]]) -> dict:
    """(file, table) pairs -> {"files": table -> sorted files, "names": last part -> sorted tables}."""
    files: Dict[str, set] = {}
    for path, table in writes:
        files.setdefault(table, set()).add(path)
    names: Dict[str, List[str]] = {}
    for table in sorted(files):
        names.setdefault(table.rsplit(".", 1)[-1], []).append(table)
    return {"files": {table: sorted(paths) for table, paths in files.items()}, "names": names}


def resolve(table: str, dynamic: bool, writers: dict) -> List[dict]:
    """verified (rule name), inferred (rule table_name), unresolved (dynamic|ambiguous) or external."""
    if dynamic:
        return [{"status": "unresolved", "spec": table, "reason": "dynamic"}]
    if "{}" not in table and table in writers["files"]:
        return [{"status": "verified", "to": path, "rule": "name"} for path in writers["files"][table]]
    prefix, _, last = table.rpartition(".")
    same_name = writers["names"].get(last, [])
    # A schema filled in at run time, on either side, can only be matched by the table name.
    matches = same_name if not prefix or "{}" in prefix else [name for name in same_name if "{}" in name]
    if len(matches) == 1:
        return [{"status": "inferred", "to": path, "rule": "table_name"} for path in writers["files"][matches[0]]]
    if matches:
        return [{"status": "unresolved", "spec": table, "reason": "ambiguous", "candidates": matches}]
    return [{"status": "external", "table": table}]
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_lineage.py tests/test_stdlib_only.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/lineage.py tests/unit/test_lineage.py
git commit -m "Match a table a file reads to the files that write it"
```

---

### Task 3: `READS` edge와 `external_tables`

**Files:**
- Modify: `skills/arche/scripts/arche_lib/graph.py`
- Test: `tests/unit/test_graph_reads.py`

**Interfaces:**
- Consumes: `extracted[path]["tables"]` (Task 1), `lineage.index`, `lineage.resolve` (Task 2)
- Produces:
  - `graph["edges"]`의 새 항목. 해석됨: `{"kind": "READS", "from": "file:<읽는 파일>", "to": "file:<쓰는 파일>", "origin": "script", "status": "verified"|"inferred", "rule": ..., "table": <이름>, "evidence": {"path", "line", "quote"}}`. 미해결: `"to": None`이고 `"spec"`, `"reason"`, 필요하면 `"candidates"`가 붙는다. `rule`은 없다.
  - `graph["external_tables"]`: `[{"from": "file:<path>", "table": <이름>, "line": <줄>}]`, `(from, line, table)` 순.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_graph_reads.py`:

```python
from test_graph_multilang import _graph

SOURCES = {
    "q/load.sql": "INSERT INTO a.orders SELECT * FROM staging.orders;\n",
    "q/more.sql": "CREATE TEMPORARY TABLE tmp AS SELECT * FROM a.orders;\nINSERT INTO a.orders SELECT * FROM tmp;\n",
    "q/report.sql": "SELECT *\nFROM a.orders o\nJOIN {{ params.schema }}.orders p ON 1=1\nJOIN a.daily_{{ ds }} d ON 1=1;\n",
}


def _reads(graph):
    return [edge for edge in graph["edges"] if edge["kind"] == "READS"]


def test_a_table_read_becomes_an_edge_to_every_file_that_writes_it():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/report.sql" and e["status"] == "verified"]

    assert edges[0] == {
        "kind": "READS", "from": "file:q/report.sql", "to": "file:q/load.sql", "origin": "script",
        "status": "verified", "rule": "name", "table": "a.orders",
        "evidence": {"path": "q/report.sql", "line": 2, "quote": "FROM a.orders o"}}
    assert [e["to"] for e in edges] == ["file:q/load.sql", "file:q/more.sql"]


def test_variable_schema_is_inferred_and_dynamic_names_stay_unresolved():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/report.sql" and e["status"] != "verified"]

    assert [(e["evidence"]["line"], e["status"], e["to"], e["table"], e.get("reason")) for e in edges] == [
        (3, "inferred", "file:q/load.sql", "{}.orders", None), (3, "inferred", "file:q/more.sql", "{}.orders", None),
        (4, "unresolved", None, "a.daily_{}", "dynamic")]
    assert all("rule" not in e and e["spec"] == e["table"] for e in edges if e["to"] is None)


def test_a_file_does_not_read_from_itself_or_from_its_own_temporary_table():
    edges = [e for e in _reads(_graph(SOURCES)) if e["from"] == "file:q/more.sql"]

    assert [(e["to"], e["table"]) for e in edges] == [("file:q/load.sql", "a.orders")]


def test_tables_nothing_writes_are_listed_as_external():
    assert _graph(SOURCES)["external_tables"] == [{"from": "file:q/load.sql", "table": "staging.orders", "line": 1}]


def test_reads_leave_other_edges_and_non_sql_repositories_alone():
    graph = _graph({"app/main.py": "import os\n"})

    assert graph["edges"] == [] and graph["external_tables"] == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_graph_reads.py -q`
Expected: FAIL. `_reads(...)`가 빈 목록이고 `graph["external_tables"]`는 `KeyError`다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/graph.py`의 모듈 docstring과 import를 바꾼다.

```python
"""Build graph.json content: file and symbol nodes plus IMPORTS, REFERENCES and READS edges."""
```

```python
from . import SCHEMA_VERSION, fileref, lineage
```

`_references` 함수 바로 아래에 추가한다.

```python
def _written_tables(paths: List[str], extracted: Dict[str, dict]) -> dict:
    """Tables each SQL file writes for others to read: no temporary and no run-time names."""
    return lineage.index(
        (path, item["table"]) for path in paths for item in extracted[path]["tables"]
        if item["access"] == "write" and not item["temp"] and not item["dynamic"])


def _reads(analyzed: List[dict], extracted: Dict[str, dict]) -> Tuple[List[dict], List[dict]]:
    """(READS edges from a file to the files writing a table it reads, tables nothing scanned writes)."""
    paths = [entry["path"] for entry in analyzed if "tables" in extracted.get(entry["path"], {})]
    writers = _written_tables(paths, extracted)
    edges: Dict[tuple, dict] = {}
    external: Dict[tuple, dict] = {}
    for path in paths:
        records = extracted[path]["tables"]
        local = {item["table"] for item in records if item["temp"]}
        for item in records:
            if item["access"] != "read" or item["table"] in local:
                continue
            for result in lineage.resolve(item["table"], item["dynamic"], writers):
                if result["status"] == "external":
                    external.setdefault((path, item["table"]),
                                        {"from": _file_id(path), "table": item["table"], "line": item["line"]})
                elif result.get("to") != path:
                    edge = _read_edge(path, item, result)
                    edges[(path, item["line"], edge["to"] or "", item["table"])] = edge
    return list(edges.values()), sorted(external.values(), key=lambda x: (x["from"], x["line"], x["table"]))


def _read_edge(path: str, item: dict, result: dict) -> dict:
    base = {
        "kind": "READS",
        "from": _file_id(path),
        "origin": "script",
        "status": result["status"],
        "table": item["table"],
        "evidence": {"path": path, "line": item["line"], "quote": item["quote"]},
    }
    if result["status"] in _RESOLVED:
        return {**base, "rule": result["rule"], "to": _file_id(result["to"])}
    candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
    return {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}
```

`build_graph`에서 `connections, routes = _http(extracted, edges)` 줄 아래에 한 줄을 넣고, 반환값의 `"edges"` 줄을 바꾸고 `"externals"` 줄 아래에 한 줄을 더한다.

```python
    reads, external_tables = _reads(analyzed, extracted)
```

```python
        "edges": sorted(edges + _references(analyzed, extracted) + reads, key=_edge_key),
        "externals": _dedupe_externals(externals),
        "external_tables": external_tables,
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 새 테스트 통과. `evals/fixtures/airflow-sql`을 도는 테스트(`tests/evals/test_fixtures.py`)는 실패할 수 있다. `READS` edge가 `score.py`의 미해결 집합 등에 섞이지는 않지만(이미 `IMPORTS`만 걸러 읽는다), 그래프의 키 집합이나 fixture 결과를 통째로 비교하는 테스트가 있으면 `external_tables`를 기대값에 더한다. `evals/fixtures/airflow-sql/expected_graph.json`은 Task 4에서 고치므로 여기서 건드리지 않는다.

Run: `python3 evals/score.py`
Expected: 종료 코드 0. `score.py`는 아직 `READS`를 비교하지 않는다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/graph.py tests/unit/test_graph_reads.py
git commit -m "Record table reads between SQL files as READS edges"
```

---

### Task 4: fixture 확장과 채점

**Files:**
- Modify: `evals/score.py`
- Create: `evals/fixtures/airflow-sql/repo/dags/ledger/sql/summary.sql`, `evals/fixtures/airflow-sql/repo/dags/billing/sql/restage.sql`
- Modify: `evals/fixtures/airflow-sql/expected_graph.json`
- Test: `tests/evals/test_fixtures.py`

**Interfaces:**
- Consumes: `READS` edge와 `graph["external_tables"]` (Task 3)
- Produces: `expected_graph.json`의 선택 키 `reads`(항목 `{"from", "target", "table", "status", "how"}`; `target`은 해석된 경로 또는 `spec`, `how`는 `rule` 또는 `reason`)와 `external_tables`(항목 `{"from", "table"}`). `score.observed_sets(graph)`에 같은 이름의 튜플 집합이 생긴다.

- [ ] **Step 1: fixture SQL 추가**

`evals/fixtures/airflow-sql/repo/dags/ledger/sql/summary.sql`:

```sql
-- FROM ledger.ignored is only a comment
WITH recent AS (
    SELECT account, EXTRACT(DAY FROM closed_at) AS d FROM ledger.closed
)
INSERT INTO ledger.summary
SELECT r.account, o.amount FROM recent r JOIN {{ params.schema }}.orders o ON r.account = o.account
WHERE o.note <> 'FROM text.inside';
```

`evals/fixtures/airflow-sql/repo/dags/billing/sql/restage.sql`:

```sql
CREATE TEMPORARY TABLE tmp_orders AS SELECT * FROM billing.orders;
INSERT INTO billing.orders SELECT * FROM tmp_orders;
INSERT INTO ledger.daily_{{ ds_nodash }} SELECT * FROM tmp_orders;
SELECT COUNT(*) FROM ledger.daily_{{ ds_nodash }};
```

- [ ] **Step 2: 정답 확장**

`evals/fixtures/airflow-sql/expected_graph.json`의 `analyzed` 목록에 두 경로를 정렬 위치에 맞춰 넣는다.

```json
    "dags/billing/sql/restage.sql",
```

```json
    "dags/ledger/sql/summary.sql",
```

같은 파일의 최상위에 두 키를 더한다(`references` 뒤).

```json
  "reads": [
    {"from": "dags/billing/sql/restage.sql", "target": "dags/billing/sql/load_orders.sql", "table": "billing.orders", "status": "verified", "how": "name"},
    {"from": "dags/billing/sql/restage.sql", "target": "ledger.daily_{}", "table": "ledger.daily_{}", "status": "unresolved", "how": "dynamic"},
    {"from": "dags/billing/sql/settle.hql", "target": "dags/billing/sql/load_orders.sql", "table": "billing.orders", "status": "verified", "how": "name"},
    {"from": "dags/billing/sql/settle.hql", "target": "dags/billing/sql/restage.sql", "table": "billing.orders", "status": "verified", "how": "name"},
    {"from": "dags/ledger/sql/close.sql", "target": "dags/billing/sql/settle.hql", "table": "billing.settled", "status": "verified", "how": "name"},
    {"from": "dags/ledger/sql/report.sql", "target": "dags/ledger/sql/close.sql", "table": "ledger.closed", "status": "verified", "how": "name"},
    {"from": "dags/ledger/sql/summary.sql", "target": "dags/ledger/sql/close.sql", "table": "ledger.closed", "status": "verified", "how": "name"},
    {"from": "dags/ledger/sql/summary.sql", "target": "dags/billing/sql/load_orders.sql", "table": "{}.orders", "status": "inferred", "how": "table_name"},
    {"from": "dags/ledger/sql/summary.sql", "target": "dags/billing/sql/restage.sql", "table": "{}.orders", "status": "inferred", "how": "table_name"}
  ],
  "external_tables": [
    {"from": "dags/billing/sql/load_orders.sql", "table": "staging.orders"},
    {"from": "dags/shared/sql/report.sql", "table": "shared.calendar"}
  ]
```

새 SQL 두 개는 어떤 Python 파일도 가리키지 않으므로 `references` 정답은 그대로다.

- [ ] **Step 3: 실패하는 테스트 작성**

`tests/evals/test_fixtures.py`의 `test_file_references_stay_out_of_import_precision_and_recall` 아래에 추가한다.

```python
def test_table_reads_are_scored_as_their_own_exact_sets():
    observed = score.observed_sets(_graph("airflow-sql"))

    assert ("dags/ledger/sql/close.sql", "dags/billing/sql/settle.hql", "billing.settled", "verified", "name") in observed["reads"]
    assert observed["external_tables"] == {("dags/billing/sql/load_orders.sql", "staging.orders"),
                                           ("dags/shared/sql/report.sql", "shared.calendar")}
    assert "reads" in score.EXACT_SETS and "external_tables" in score.EXACT_SETS
```

- [ ] **Step 4: 실패 확인**

Run: `python3 -m pytest tests/evals/test_fixtures.py -q`
Expected: 새 테스트가 `KeyError: 'reads'`로 FAIL.

- [ ] **Step 5: `score.py` 구현**

모듈 docstring의 `REFERENCES` 문장 뒤에 한 문장을 더한다.

```python
READS edges (a SQL file reading a table another SQL file writes) and external tables are
compared exactly through the `reads` and `external_tables` lists, also outside precision and recall.
```

`EXACT_SETS`를 바꾼다.

```python
EXACT_SETS = ("analyzed", "skipped", "inferred", "unresolved", "externals", "symbols", "http", "references",
              "reads", "external_tables")
```

`_reference_key` 아래에 추가한다.

```python
def _read_key(edge):
    """(from, writing file or written name, table, status, rule or reason)."""
    target = _path(edge["to"]) if edge["to"] else edge["spec"]
    return (_path(edge["from"]), target, edge["table"], edge["status"], edge.get("rule") or edge["reason"])
```

`observed_sets`의 반환 딕셔너리에 마지막 두 항목으로 더한다.

```python
        "reads": {_read_key(e) for e in graph["edges"] if e["kind"] == "READS"},
        "external_tables": {(_path(x["from"]), x["table"]) for x in graph.get("external_tables", [])},
```

`expected_sets`의 반환 딕셔너리에 마지막 두 항목으로 더한다.

```python
        "reads": {(r["from"], r["target"], r["table"], r["status"], r["how"]) for r in expected.get("reads", [])},
        "external_tables": {(x["from"], x["table"]) for x in expected.get("external_tables", [])},
```

- [ ] **Step 6: 통과 확인**

Run: `python3 evals/score.py`
Expected: 종료 코드 0. `airflow-sql` 줄은 `0 0 0`이고 `MISMATCH`가 없다. `MISMATCH reads`나 `MISMATCH external_tables`가 나오면 정답 파일을 관찰값에 맞춰 고치지 않는다. 정답은 fixture에서 손으로 계산한 값이므로 차이는 fixture 오타이거나 추출기 결함이다. 무엇이 다른지 보고한다.

Run: `python3 -m pytest -q`
Expected: 전체 통과

- [ ] **Step 7: 커밋**

```bash
git add evals/score.py evals/fixtures/airflow-sql tests/evals/test_fixtures.py
git commit -m "Score table reads and external tables on the Airflow fixture"
```

---

### Task 5: 종류를 가려 읽는 곳 고치기

**Files:**
- Modify: `skills/arche/scripts/arche_lib/changes.py:41`
- Modify: `skills/arche/scripts/arche_lib/atlas.py:300`
- Modify: `skills/arche/assets/map-template.html:277`
- Modify: `skills/arche/scripts/brief.py:65-83`
- Modify: `skills/arche/scripts/arche_lib/report.py:63-66`
- Test: `tests/unit/test_changes_cli.py`, `tests/unit/test_atlas.py`, `tests/unit/test_report_file_references.py`

**Interfaces:**
- Consumes: `READS` edge (Task 3)
- Produces:
  - `changes.json`: SQL 파일이 바뀌면 그 파일이 쓰는 테이블을 읽는 SQL과, 그 SQL을 가리키는 Python 파일이 `impact`에 든다.
  - `atlas.json`의 `links`: 부품 사이 테이블 읽기는 `"kind": "read"`다.
  - `brief.txt`: `TABLE READS BETWEEN FOLDERS` 구역.
  - 리포트 파일 상세의 관계 줄에 테이블 이름이 붙는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_changes_cli.py` 끝에 추가한다.

```python
def test_changing_a_sql_file_reaches_readers_of_its_table_and_the_code_naming_them(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"dag.py": 'QUERY = "q/report.sql"\n', "q/load.sql": "INSERT INTO a.t SELECT 1;\n",
                      "q/report.sql": "SELECT * FROM a.t;\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    (repo / "q" / "load.sql").write_text("INSERT INTO a.t SELECT 2;\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "edit")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    files = {item["path"]: item for item in json.loads((out / "changes.json").read_text())["files"]}
    assert files["q/load.sql"]["impact"] == ["dag.py", "q/report.sql"]
```

`tests/unit/test_atlas.py` 끝에 추가한다.

```python
def test_a_table_read_between_parts_is_a_read_link():
    graph = _graph({"load/a.sql": "INSERT INTO a.t SELECT 1;\n", "report/b.sql": "SELECT * FROM a.t;\n"})
    inventory = {"files": [{"path": n["id"][5:], "status": "analyzed", "lines": n["lines"], "sha256": n["sha256"]}
                           for n in graph["nodes"] if n["type"] == "file"]}
    document = {"version": 1, "project": {"name": "demo", "summary": "s"}, "parts": [
        {"id": "load", "label": "Load", "kind": "data", "summary": "writes", "paths": ["load"]},
        {"id": "report", "label": "Report", "kind": "data", "summary": "reads", "paths": ["report"]}],
        "edges": [], "flows": []}

    atlas = build_atlas(inventory, graph, document, {})

    assert [(l["from"], l["to"], l["status"], l["kind"], l["count"]) for l in atlas["links"]] == [
        ("report", "load", "verified", "read", 1)]
    assert atlas["file_links"] == [[1, 0, "verified", 1]]


def test_brief_lists_table_reads_between_folders_apart_from_imports_and_references(make_repo, run_script, tmp_path):
    repo = make_repo({"dags/a/x.sql": "INSERT INTO a.t SELECT 1;\n", "dags/b/y.sql": "SELECT * FROM a.t;\n"})
    out = tmp_path / "out"
    assert run_script("arche.py", repo, "--out", out).returncode == 0
    assert run_script("brief.py", "--out", out).returncode == 0
    text = (out / "brief.txt").read_text()

    def section(title):
        return text.split(title)[1].split("\n\n")[0]

    assert "  dags/b -> dags/a  1" in section("TABLE READS BETWEEN FOLDERS")
    assert "dags/b -> dags/a" not in section("IMPORTS BETWEEN FOLDERS")
    assert "dags/b -> dags/a" not in section("FILE REFERENCES BETWEEN FOLDERS")
```

`tests/unit/test_report_file_references.py` 끝에 추가한다.

```python
def test_a_file_detail_names_the_table_a_read_goes_through():
    read = {"kind": "READS", "from": "file:dags/sql/load.sql", "to": "file:dags/sql/unused.sql", "origin": "script",
            "status": "verified", "rule": "name", "table": "billing.<orders>",
            "evidence": {"path": "dags/sql/load.sql", "line": 1, "quote": "q"}}
    html = render(TEMPLATE, _graph([read]), [], "match")

    assert "<code>billing.&lt;orders&gt;</code>" in html and "billing.<orders>" not in html
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_changes_cli.py tests/unit/test_atlas.py tests/unit/test_report_file_references.py -q`
Expected: 새 테스트 네 개가 FAIL. `impact`가 `[]`이고, 연결 종류가 `import`이며, `TABLE READS BETWEEN FOLDERS`가 없어 `IndexError`가 나고, 테이블 이름이 HTML에 없다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/changes.py`:

```python
_DEPENDENCY_KINDS = ("IMPORTS", "REFERENCES", "READS")
```

`skills/arche/scripts/arche_lib/atlas.py`. 모듈 수준 상수를 다른 상수 옆에 두고, `kind = "reference" if ... else "import"` 줄을 바꾼다.

```python
_LINK_KINDS = {"REFERENCES": "reference", "READS": "read"}
```

```python
            kind = _LINK_KINDS.get(edge.get("kind"), "import")
```

`skills/arche/assets/map-template.html`의 `for (const item of D.links)` 줄을 바꾼다. 파일 사이의 선은 `D.file_links`가 이미 그리므로, 스캔이 만든 세 종류는 `D.links`에서 다시 그리지 않는다.

```javascript
  for (const item of D.links) if (item.status === 'cited' || !['import', 'reference', 'read'].includes(item.kind)) link(end(item, 'from'), end(item, 'to'), item.status, item.count, item.label);
```

`skills/arche/scripts/brief.py`. 카운터 선언 줄과 분기 줄을 바꾼다.

```python
    fan_in, fan_out, between, referenced, table_reads = Counter(), Counter(), Counter(), Counter(), Counter()
```

```python
            {"REFERENCES": referenced, "READS": table_reads}.get(kind, between)[a, b] += 1
```

`FILE REFERENCES BETWEEN FOLDERS` 구역을 만드는 두 줄 바로 아래에 추가한다.

```python
    lines += ["", "TABLE READS BETWEEN FOLDERS (a SQL file reading a table another SQL file writes; two levels deep)"]
    lines += [f"  {a} -> {b}  {count}" for (a, b), count in sorted(table_reads.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP * 2]] or ["  none"]
```

`skills/arche/scripts/arche_lib/report.py`의 `_relation`을 바꾼다.

```python
def _relation(edge, other, anchors):
    label = _file_link(other, anchors) if other else _code(edge["spec"])
    table = f" · {_code(edge['table'])}" if "table" in edge and other else ""
    reason = f" · {escape(edge['reason'])}" if "reason" in edge else ""
    return f'{label}{table} · {_status(edge["status"])} · {_evidence(edge)}{reason}'
```

미해결 `READS`는 `spec`이 곧 테이블 이름이라 `other`가 없을 때는 테이블을 다시 붙이지 않는다.

파일이 500개를 넘는 리포트는 파일 상세를 데이터로 보내 JavaScript가 그린다(`_file_details`). 그 경로에는 테이블 이름을 넣지 않는다. 이 계획의 범위가 아니다.

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과

Run: `python3 evals/score.py`
Expected: 종료 코드 0

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/changes.py skills/arche/scripts/arche_lib/atlas.py skills/arche/assets/map-template.html skills/arche/scripts/brief.py skills/arche/scripts/arche_lib/report.py tests/unit/test_changes_cli.py tests/unit/test_atlas.py tests/unit/test_report_file_references.py
git commit -m "Carry table reads into change impact, the map, the brief and file details"
```

---

### Task 6: 리포트의 "Tables" 구역

**Files:**
- Modify: `skills/arche/scripts/arche_lib/report.py`
- Modify: `skills/arche/scripts/arche_lib/i18n.py`
- Test: `tests/unit/test_report_tables.py`

**Interfaces:**
- Consumes: `READS` edge와 `graph["external_tables"]` (Task 3)
- Produces: 리포트 HTML의 `<section id="tables">`. 외부 테이블도 `READS` edge도 없으면 구역과 목차 항목이 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_report_tables.py`:

```python
"""The report section for external tables and table names that were not followed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()
FILES = ("q/load.sql", "q/report.sql")


def _unresolved(line, table, reason):
    return {"kind": "READS", "from": "file:q/report.sql", "to": None, "origin": "script", "status": "unresolved",
            "table": table, "spec": table, "reason": reason, "evidence": {"path": "q/report.sql", "line": line, "quote": "q"}}


def _graph(edges=(), external=()):
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": "file:" + path, "lang": "sql", "lines": 1} for path in FILES],
        "edges": list(edges), "externals": [], "skipped": [],
        "external_tables": [{"from": "file:" + path, "table": table, "line": line} for path, table, line in external],
    }


def _section(html):
    return html.split('<section id="tables">')[1].split("</section>")[0]


def test_external_tables_are_folded_by_schema_with_their_readers():
    external = [("q/load.sql", "staging.orders", 1), ("q/report.sql", "staging.orders", 4),
                ("q/report.sql", "staging.users", 5), ("q/report.sql", "calendar", 6)]
    section = _section(render(TEMPLATE, _graph(external=external), [], "match"))

    assert "External tables" in section
    assert '<code>staging</code> <span class="count">2</span>' in section
    assert section.index("<code>staging</code>") < section.index("no schema")
    assert "<code>staging.orders</code>" in section
    assert "<code>q/load.sql:1</code>" in section and "<code>q/report.sql:4</code>" in section


def test_names_that_were_not_followed_are_folded_by_reason():
    edges = [_unresolved(2, "a.daily_{}", "dynamic"), _unresolved(3, "{}.ledger", "ambiguous")]
    section = _section(render(TEMPLATE, _graph(edges=edges), [], "match"))

    assert "<code>dynamic</code> <span" in section and "<code>ambiguous</code> <span" in section
    assert "<code>a.daily_{}</code> · <code>q/report.sql:2</code>" in section


def test_names_from_the_repository_are_escaped():
    section = _section(render(TEMPLATE, _graph(external=[("q/load.sql", "<b>.<i>", 1)]), [], "match"))

    assert "<b>" not in section and "<i>" not in section
    assert "&lt;b&gt;.&lt;i&gt;" in section


def test_a_repository_without_tables_has_no_section_or_navigation_entry():
    html = render(TEMPLATE, _graph(), [], "match")

    assert 'id="tables"' not in html and 'href="#tables"' not in html


def test_a_graph_written_before_this_feature_still_renders():
    graph = _graph()
    del graph["external_tables"]

    assert 'id="tables"' not in render(TEMPLATE, graph, [], "match")


def test_the_section_is_translated():
    html = render(TEMPLATE, _graph(external=[("q/load.sql", "staging.orders", 1)]), [], "match", lang="ko")

    assert "외부 테이블" in _section(html)
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_report_tables.py -q`
Expected: FAIL. 구역이 없어 `_section`에서 `IndexError`가 난다. `test_a_repository_without_tables_…`와 `test_a_graph_written_before_…`만 통과한다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/report.py`. `_file_references` 함수 아래, `_graph_data` 위에 추가한다.

```python
_UNRESOLVED_READS = (
    ("dynamic", "Table names built at run time"),
    ("ambiguous", "Table names matching several tables"),
)


def _external_tables(external, anchors):
    """External tables grouped by schema, each with the files reading it."""
    readers = defaultdict(list)
    for item in external:
        location = _code(item["from"].removeprefix("file:") + ":" + str(item["line"]))
        readers[item["table"]].append(f'{_file_link(item["from"], anchors)} {location}')
    schemas = defaultdict(list)
    for table in sorted(readers):
        schemas[table.rpartition(".")[0]].append(table)
    blocks = []
    for schema, tables in sorted(schemas.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        label = _code(schema) if schema else T("no schema")
        rows = (f'{_code(table)} · ' + ", ".join(readers[table]) for table in tables)
        blocks.append(f'<details class="group"><summary>{label} <span class="count">{len(tables)}</span></summary>'
                      + _items(rows, "") + '</details>')
    return "".join(blocks)


def _tables(graph, anchors):
    """Tables read here and written nowhere scanned, and table names that were not followed."""
    external = graph.get("external_tables", [])
    by_reason = defaultdict(list)
    for edge in graph["edges"]:
        if edge.get("kind") == "READS" and not edge["to"]:
            by_reason[edge["reason"]].append(f'{_code(edge["spec"])} · {_evidence(edge)}')
    if not external and not by_reason and not any(edge.get("kind") == "READS" for edge in graph["edges"]):
        return ""
    count = len({item["table"] for item in external})
    body = (f'<h3>{T("External tables")} <span class="count">{count}</span></h3>'
            + (_external_tables(external, anchors) or f'<p class="empty">{T("Every table read here is written by a scanned SQL file.")}</p>'))
    for reason, title in _UNRESOLVED_READS:
        if by_reason[reason]:
            body += (f'<details class="group"><summary>{T(title)} <code>{reason}</code> <span class="count">{len(by_reason[reason])}</span></summary>'
                     + _items(by_reason[reason], "", "columns") + '</details>')
    return _section("tables", T("Tables"), body,
                    T("A table is external when no scanned SQL file writes it. Only table names written in SQL files are read. SQL inside Python, default schemas and tables inside views are not, so a table listed here may still be made in this repository. Open a SQL file under File dependencies to see which files read what it writes."))
```

`_render`의 `sections` 목록에서 `("file-references", ...)` 항목 바로 아래에 한 항목을 넣는다.

```python
        ("tables", T("Tables"), len({item["table"] for item in graph.get("external_tables", [])}) or None,
         _tables(graph, anchors)),
```

`skills/arche/scripts/arche_lib/i18n.py`의 `_KO`에서 `"SQL file references": "SQL 파일 참조",` 줄 위에 추가한다.

```python
    "Tables": "테이블",
    "A table is external when no scanned SQL file writes it. Only table names written in SQL files are read. SQL inside Python, default schemas and tables inside views are not, so a table listed here may still be made in this repository. Open a SQL file under File dependencies to see which files read what it writes.":
        "외부 테이블은 스캔한 SQL 파일 중 쓰는 파일이 없는 테이블입니다. SQL 파일에 적힌 테이블 이름만 읽습니다. Python 안의 SQL, 기본 스키마, 뷰 안의 테이블은 읽지 않으므로, 여기 나온 테이블도 이 레포에서 만들 수 있습니다. 파일 의존성에서 SQL 파일을 열면 그 파일이 쓰는 테이블을 읽는 파일을 볼 수 있습니다.",
    "External tables": "외부 테이블",
    "Every table read here is written by a scanned SQL file.": "여기서 읽는 모든 테이블을 스캔한 SQL 파일이 씁니다.",
    "no schema": "스키마 없음",
    "Table names built at run time": "실행 시점에 만들어지는 테이블 이름",
    "Table names matching several tables": "여러 테이블과 일치하는 테이블 이름",
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과

- [ ] **Step 5: 리포트 텍스트 확인**

```bash
out=$(mktemp -d) && python3 -B skills/arche/scripts/arche.py evals/fixtures/airflow-sql/repo --out "$out" --lang ko && grep -o '<section id="tables">.*</section>' "$out/report.html" | sed 's/<[^>]*>/ /g' | cut -c1-900
```

출력에서 다음을 확인한다.

1. **외부 테이블 2**가 있고 `staging.orders`와 `shared.calendar`가 읽는 파일과 함께 나온다.
2. 접힌 구역 `dynamic` 1개가 있고 `ledger.daily_{}`가 나온다.

브라우저로 보는 확인은 실행자가 할 수 없으면 건너뛰고 보고서에 적는다.

- [ ] **Step 6: 커밋**

```bash
git add skills/arche/scripts/arche_lib/report.py skills/arche/scripts/arche_lib/i18n.py tests/unit/test_report_tables.py
git commit -m "List external tables and unfollowed table names in the report"
```

---

### Task 7: 문서와 최종 확인

**Files:**
- Modify: `README.md`
- Modify: `docs/artifact-compatibility.md`
- Modify: `docs/release-notes.md`
- Modify: `skills/arche/SKILL.md`

**Interfaces:**
- Consumes: Task 1~6의 동작
- Produces: 없음

- [ ] **Step 1: `README.md`**

`SQL·HQL 파일은 내용을 해석하지 않고 노드로만 둡니다.`로 시작하는 문장을 찾아, 그 문장 하나를 다음으로 바꾼다. 뒤따르는 문장은 그대로 둔다.

```markdown
SQL·HQL 파일에서는 테이블 이름만 읽습니다. 한 파일이 읽는 테이블을 다른 파일이 쓰면 두 파일을 연결하고, 스캔한 SQL 중 쓰는 파일이 없는 테이블은 리포트의 **테이블**에 외부 테이블로 보여 줍니다. 컬럼, Python 안의 SQL, 기본 스키마는 읽지 않습니다.
```

- [ ] **Step 2: `docs/artifact-compatibility.md`**

첫 행(`inventory.json, graph.json, changes.json`)의 Reader contract 칸 끝에 문장을 더한다.

```markdown
`graph.json` edges may also carry `kind: "READS"` (a SQL file reading a table another SQL file writes; `table` holds the normalized name, `rule` is `name` or `table_name`, `reason` is `dynamic` or `ambiguous`), and the document gained the optional `external_tables` list (`from`, `table`, `line`) for tables no scanned SQL file writes. Both are additive.
```

- [ ] **Step 3: `docs/release-notes.md`**

`## 1.0 출시 후보` 아래 첫 항목으로 넣는다.

```markdown
- 테이블 lineage: SQL·HQL 파일에서 `FROM`·`JOIN`으로 읽는 테이블과 `INSERT`·`CREATE`·`MERGE`·`DELETE`로 쓰는 테이블을 읽어, 읽는 파일에서 쓰는 파일로 가는 `READS` 연결을 기록합니다. 스키마가 변수인 이름은 테이블 이름으로 맞춰 "추론됨"으로 표시하고, 이름 자체가 실행 시점에 정해지면 따라가지 않습니다. 리포트의 **테이블**에서 외부 테이블을 스키마별로 볼 수 있고, SQL 파일을 바꾸면 그 테이블을 읽는 SQL과 이를 가리키는 DAG 파일이 영향 범위에 듭니다. 컬럼 단위 lineage, Python 안의 SQL, `USE`로 정하는 기본 스키마는 읽지 않습니다.
```

- [ ] **Step 4: `skills/arche/SKILL.md`**

`plus SQL and HQL files as unparsed nodes`를 `plus SQL and HQL files, read only for table names`로 바꾼다.

`A `REFERENCES` edge is a SQL file path written as text in a Python file`로 시작하는 문장 뒤에 문장을 더한다.

```markdown
A `READS` edge runs from a SQL file to a SQL file that writes a table it reads; it is found by table name, so an `inferred` one matched only the table part of the name, and a table in `external_tables` may still be written by SQL the scan does not read (inside Python, or in a file that was skipped).
```

- [ ] **Step 5: 전체 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과. 통과 수는 544에 이 계획에서 더한 테스트 수를 더한 값이다.

Run: `python3 evals/score.py`
Expected: 종료 코드 0. fixture 10개가 나오고 `MISMATCH`가 없다.

문서의 숫자나 문구를 검사하는 테스트가 실패하면 그 테스트가 요구하는 문서 위치를 함께 고친다.

- [ ] **Step 6: 커밋**

```bash
git add README.md docs/artifact-compatibility.md docs/release-notes.md skills/arche/SKILL.md
git commit -m "Document table lineage"
```

---

## 계획 밖에 남는 일

- 사내 팀 폴더 재실행과 설계 문서의 성공 기준 확인은 사용자가 한다.
- 파일이 500개를 넘는 리포트의 파일 상세(JavaScript가 그리는 경로)에는 테이블 이름이 나오지 않는다.
- 지도에서 테이블 읽기 선을 다른 모양으로 그리는 일은 하지 않는다.
- `UPDATE`, `ALTER`, `DROP`, `TRUNCATE`는 쓰기로 세지 않는다. `:name` 형태의 바인드 변수는 변수 자리로 보지 않는다.
