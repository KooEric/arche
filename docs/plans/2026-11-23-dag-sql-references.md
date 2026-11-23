# DAG↔SQL 파일 참조 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Python 파일의 문자열에 적힌 SQL·HQL 경로를 레포 파일로 해석해 `REFERENCES` edge로 기록하고, 리포트·지도·리비전 비교가 이를 보여 주게 한다.

**Architecture:** `.sql`·`.hql`을 내용 해석 없는 언어 `sql`로 등록해 노드로 만든다. Python 어댑터가 `file_refs` 레코드를 내고, 언어와 무관한 `fileref.resolve`가 경로를 해석하며, `graph.py`가 결과를 `REFERENCES` edge로 `graph.json`의 `edges`에 더한다. 리포트의 파일 상세, 의존성 지도, 폴더 요약은 이미 edge 종류를 가리지 않고 읽으므로 새 edge가 그대로 나타난다. 종류를 가려 읽는 곳(채점기, 리비전 비교, 지도 부품 연결, 폴더 요약)만 고친다.

**Tech Stack:** Python 3.9+ 표준 라이브러리(`ast`, `posixpath`), pytest.

이 문서는 [DAG↔SQL 파일 참조 설계](../specs/2026-11-23-dag-sql-references-design.md)의 구현 계획이다. Task 2와 3의 수집기·해석기 코드는 `main`(43a0de0) 옆 임시 폴더에서 Task 5의 합성 fixture로 실행해 표의 결과가 나오는 것을 확인했다. 나머지 Task의 코드는 실행해 보지 않았다. 시작 시점의 전체 테스트는 478개 통과다.

## Global Constraints

- Python 3.9에서 동작해야 한다. `match` 문, 런타임의 `X | Y` 타입 표기, 3.10 이상 전용 API를 쓰지 않는다.
- 표준 라이브러리만 쓴다. `tests/test_stdlib_only.py`가 검사한다.
- 대상 레포는 읽기만 한다. 프로젝트 코드를 실행하지 않는다.
- SQL·HQL 파일의 내용은 해석하지 않는다. 테이블 이름을 읽지 않는다.
- 문자열이 어느 함수에 넘겨졌는지는 판단에 쓰지 않는다.
- edge 상태 이름은 기존과 같다: `verified`, `inferred`, `unresolved`.
- `rule` 값은 `relative`, `root`, `suffix`다. `reason` 값은 `dynamic`, `ambiguous`, `not_found`다.
- 기존 `IMPORTS` edge의 필드와 `evals/score.py`의 정밀도·재현율 계산은 바꾸지 않는다. `REFERENCES`는 그 계산에 들어가지 않는다.
- fixture에 사내 코드를 넣지 않는다. 모두 합성이다.
- 커밋 메시지는 이 저장소의 관례대로 영어 한 문장으로 쓰고, 귀속(Co-Authored-By) 줄은 붙이지 않는다.
- 모든 명령은 worktree 루트에서 실행한다.

## File Structure

| 파일 | 변경 | 책임 |
|---|---|---|
| `skills/arche/scripts/arche_lib/langs/sql.py` | 신규 | `sql` 어댑터. 빈 import와 빈 심볼을 돌려준다. |
| `skills/arche/scripts/arche_lib/langs/__init__.py` | 수정 | `.sql`, `.hql` 확장자 등록 |
| `skills/arche/scripts/arche_lib/langs/registry.py` | 수정 | `sql` 어댑터 등록 |
| `skills/arche/scripts/arche_lib/fileref.py` | 신규 | 경로 문자열 판별과 해석 |
| `skills/arche/scripts/arche_lib/langs/python_filerefs.py` | 신규 | Python AST에서 경로 문자열 수집 |
| `skills/arche/scripts/arche_lib/langs/python.py` | 수정 | `extract` 결과에 `file_refs` 추가 |
| `skills/arche/scripts/arche_lib/graph.py` | 수정 | `REFERENCES` edge 생성 |
| `evals/score.py` | 수정 | `references` 정답 집합 채점 |
| `evals/fixtures/airflow-sql/` | 신규 | 합성 fixture와 정답 |
| `skills/arche/scripts/arche_lib/changes.py` | 수정 | 리비전 비교에 `REFERENCES` 포함 |
| `skills/arche/scripts/arche_lib/atlas.py` | 수정 | 부품 연결 종류 `reference` |
| `skills/arche/assets/map-template.html` | 수정 | `reference` 연결을 두 번 그리지 않음 |
| `skills/arche/scripts/brief.py` | 수정 | 폴더 간 참조 구역 |
| `skills/arche/scripts/arche_lib/report.py` | 수정 | "SQL file references" 구역 |
| `skills/arche/scripts/arche_lib/i18n.py` | 수정 | 새 문구의 한국어 |
| `README.md`, `docs/artifact-compatibility.md`, `docs/release-notes.md`, `skills/arche/SKILL.md` | 수정 | 문서 |

---

### Task 1: SQL·HQL을 언어 `sql`로 등록

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/sql.py`
- Modify: `skills/arche/scripts/arche_lib/langs/__init__.py:7-13`
- Modify: `skills/arche/scripts/arche_lib/langs/registry.py:13-23`
- Test: `tests/unit/test_language_table.py`, `tests/unit/test_graph_multilang.py`

**Interfaces:**
- Consumes: 없음
- Produces: `language_for("x.sql") == "sql"`, `language_for("x.hql") == "sql"`. 그래프의 파일 노드 `{"id": "file:<path>", "type": "file", "lang": "sql", ...}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_language_table.py`의 `parametrize` 목록에서 `("README.md", None), ("style.css", None),` 줄 바로 앞에 한 줄을 넣는다.

```python
        ("q/load.sql", "sql"), ("q/settle.hql", "sql"), ("q/UPPER.SQL", "sql"),
```

같은 파일의 `test_typescript_and_javascript_share_one_adapter`에서 집합을 바꾼다.

```python
    assert set(ADAPTERS) == {"python", "typescript", "javascript", "dart", "go", "java", "kotlin", "sql"}
```

`tests/unit/test_graph_multilang.py` 끝에 추가한다.

```python
def test_sql_and_hql_files_are_nodes_with_no_symbols_or_edges():
    graph = _graph({"q/load.sql": "select 1\n", "q/settle.hql": "select 2\n"})

    assert [(n["id"], n["type"], n["lang"]) for n in graph["nodes"]] == [
        ("file:q/load.sql", "file", "sql"), ("file:q/settle.hql", "file", "sql")]
    assert graph["edges"] == [] and graph["skipped"] == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_language_table.py tests/unit/test_graph_multilang.py -q`
Expected: FAIL. `language_for("q/load.sql")`가 `None`을 돌려주고, 그래프 테스트는 노드 목록이 비어 있다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/langs/sql.py`:

```python
"""SQL and HQL files: graph nodes only. Their contents are not parsed."""
from __future__ import annotations

from typing import List


def extract(path: str, text: str) -> dict:
    return {"imports": [], "symbols": []}


def build_context(parsed_paths, all_paths, read_path) -> None:
    return None


def resolve(import_record: dict, importer_path: str, context) -> List[dict]:
    return []
```

`skills/arche/scripts/arche_lib/langs/__init__.py`의 `LANG_BY_EXT`에 마지막 항목으로 추가한다.

```python
    ".sql": "sql", ".hql": "sql",
```

`skills/arche/scripts/arche_lib/langs/registry.py`에서 import 줄과 `ADAPTERS`를 바꾼다.

```python
from . import dart, go, jvm, python, sql, typescript
```

```python
    "kotlin": jvm,
    "sql": sql,
}
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과(482개). `.sql` 파일을 `unsupported_lang`으로 기대하던 테스트는 없다. 실패가 나오면 그 테스트가 `.sql`·`.hql` 파일을 쓰는지 확인하고, 쓴다면 기대값을 `analyzed`로 고친다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs tests/unit/test_language_table.py tests/unit/test_graph_multilang.py
git commit -m "Scan SQL and HQL files as graph nodes"
```

---

### Task 2: 경로 해석기 `fileref`

**Files:**
- Create: `skills/arche/scripts/arche_lib/fileref.py`
- Test: `tests/unit/test_fileref.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `is_path(text: str) -> bool`
  - `is_template(text: str) -> bool`
  - `index(paths: Iterable[str]) -> Dict[str, List[str]]` (파일 이름 → 정렬된 경로 목록)
  - `resolve(spec: str, importer_path: str, by_name: Dict[str, List[str]], dynamic: bool = False) -> dict`. 반환은 `{"status": "verified"|"inferred", "to": <path>, "rule": ...}` 또는 `{"status": "unresolved", "spec": <spec>, "reason": ..., "candidates"?: [...]}`.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_fileref.py`:

```python
import pytest

from arche_lib import fileref


@pytest.mark.parametrize("text, expected", [
    ("sql/load.sql", True), ("settle.HQL", True), ("{{ params.dir }}/load.sql", True),
    (".sql", False), ("select 1 from a.sql", False), ("load.sql ", False), ("notes.txt", False),
    ("a" * 197 + ".sql", False),
])
def test_only_text_that_can_name_one_sql_file_is_a_path(text, expected):
    assert fileref.is_path(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("sql/load.sql", False), ("sql/{}.sql", True), ("%s.sql", True), ("sql/*.sql", True),
    ("load_?.sql", True), ("{{ params.dir }}/load.sql", True),
])
def test_placeholders_and_globs_make_a_template(text, expected):
    assert fileref.is_template(text) is expected


def test_index_groups_sorted_paths_by_file_name():
    assert fileref.index(["b/x.sql", "a/x.sql", "a/y.hql"]) == {"x.sql": ["a/x.sql", "b/x.sql"], "y.hql": ["a/y.hql"]}


def _resolve(spec, importer, paths, dynamic=False):
    return fileref.resolve(spec, importer, fileref.index(paths), dynamic)


def test_a_dynamic_reference_is_never_resolved():
    assert _resolve("sql/{}.sql", "dags/a.py", ["dags/sql/x.sql"], True) == {
        "status": "unresolved", "spec": "sql/{}.sql", "reason": "dynamic"}


def test_a_path_beside_the_importer_is_verified_first():
    assert _resolve("sql/x.sql", "dags/a/dag.py", ["dags/a/sql/x.sql", "sql/x.sql"]) == {
        "status": "verified", "to": "dags/a/sql/x.sql", "rule": "relative"}


def test_parent_segments_are_followed_only_from_the_importer():
    assert _resolve("../x.sql", "a/b/m.py", ["a/x.sql"])["to"] == "a/x.sql"
    assert _resolve("../../x.sql", "a/m.py", ["a/x.sql"]) == {
        "status": "unresolved", "spec": "../../x.sql", "reason": "not_found"}


def test_a_path_from_the_scan_root_is_verified():
    assert _resolve("dags/b/sql/x.sql", "dags/a/dag.py", ["dags/b/sql/x.sql"]) == {
        "status": "verified", "to": "dags/b/sql/x.sql", "rule": "root"}


def test_backslashes_and_leading_dot_slash_are_normalized():
    assert _resolve(".\\q\\x.sql", "m.py", ["q/x.sql"])["rule"] == "relative"


def test_one_file_ending_with_the_path_is_inferred():
    assert _resolve("x.sql", "dags/a/dag.py", ["dags/a/sql/x.sql"]) == {
        "status": "inferred", "to": "dags/a/sql/x.sql", "rule": "suffix"}


def test_a_suffix_matches_whole_path_segments_only():
    assert _resolve("x.sql", "m.py", ["q/ax.sql"])["reason"] == "not_found"
    assert _resolve("x.sql", "m.py", ["q/x.sql", "q/ax.sql"])["to"] == "q/x.sql"


def test_an_absolute_path_is_matched_by_its_longest_tail():
    assert _resolve("/opt/airflow/q/x.sql", "m.py", ["q/x.sql", "r/x.sql"]) == {
        "status": "inferred", "to": "q/x.sql", "rule": "suffix"}


def test_several_files_ending_with_the_path_are_listed_as_candidates():
    assert _resolve("x.sql", "m.py", ["r/x.sql", "q/x.sql"]) == {
        "status": "unresolved", "spec": "x.sql", "reason": "ambiguous", "candidates": ["q/x.sql", "r/x.sql"]}


def test_a_path_matching_nothing_is_not_found():
    assert _resolve("sql/gone.sql", "m.py", ["sql/x.sql"]) == {
        "status": "unresolved", "spec": "sql/gone.sql", "reason": "not_found"}
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_fileref.py -q`
Expected: FAIL with `ImportError: cannot import name 'fileref'`

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/fileref.py`:

```python
"""Resolve a path written in source text to a scanned file. Language independent."""
from __future__ import annotations

import posixpath
import re
from typing import Dict, Iterable, List, Sequence

SUFFIXES = (".sql", ".hql")
MAX_SPEC_CHARS = 200
_TEMPLATE_CHARS = "{%*?"
_JINJA = re.compile(r"\{\{.*?\}\}")


def is_path(text: str) -> bool:
    """True for text that can name one SQL file: right suffix, a stem, no whitespace outside `{{ }}`."""
    return (len(SUFFIXES[0]) < len(text) <= MAX_SPEC_CHARS and text.lower().endswith(SUFFIXES)
            and not any(char.isspace() for char in _JINJA.sub("{}", text)))


def is_template(text: str) -> bool:
    """True when part of the path is filled in at run time or is a glob."""
    return any(char in text for char in _TEMPLATE_CHARS)


def index(paths: Iterable[str]) -> Dict[str, List[str]]:
    """File name -> sorted paths carrying that name."""
    by_name: Dict[str, List[str]] = {}
    for path in sorted(paths):
        by_name.setdefault(posixpath.basename(path), []).append(path)
    return by_name


def _normalize(spec: str) -> str:
    spec = spec.replace("\\", "/")
    while spec.startswith("./"):
        spec = spec[2:]
    return spec.lstrip("/")


def _unresolved(spec: str, reason: str, candidates: Sequence[str] = ()) -> dict:
    return {"status": "unresolved", "spec": spec, "reason": reason,
            **({"candidates": list(candidates)} if candidates else {})}


def resolve(spec: str, importer_path: str, by_name: Dict[str, List[str]], dynamic: bool = False) -> dict:
    """verified (rule relative|root), inferred (rule suffix) or unresolved (reason dynamic|ambiguous|not_found)."""
    if dynamic:
        return _unresolved(spec, "dynamic")
    clean = _normalize(spec)
    known = by_name.get(posixpath.basename(clean), [])
    relative = posixpath.normpath(posixpath.join(posixpath.dirname(importer_path), clean))
    if relative in known:
        return {"status": "verified", "to": relative, "rule": "relative"}
    parts = clean.split("/")
    if ".." in parts:
        return _unresolved(spec, "not_found")
    if clean in known:
        return {"status": "verified", "to": clean, "rule": "root"}
    # The longest tail of the written path that ends a scanned path decides.
    for start in range(len(parts)):
        tail = "/".join(parts[start:])
        matches = [path for path in known if path == tail or path.endswith("/" + tail)]
        if len(matches) == 1:
            return {"status": "inferred", "to": matches[0], "rule": "suffix"}
        if matches:
            return _unresolved(spec, "ambiguous", matches)
    return _unresolved(spec, "not_found")
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_fileref.py tests/test_stdlib_only.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/fileref.py tests/unit/test_fileref.py
git commit -m "Resolve a written SQL path to a scanned file"
```

---

### Task 3: Python 경로 문자열 수집기

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/python_filerefs.py`
- Modify: `skills/arche/scripts/arche_lib/langs/python.py`
- Test: `tests/unit/test_python_filerefs.py`

**Interfaces:**
- Consumes: `fileref.is_path(text) -> bool`, `fileref.is_template(text) -> bool` (Task 2)
- Produces:
  - `python_filerefs.file_refs(path: str, text: str) -> List[dict]`. 각 항목은 `{"spec": str, "line": int, "quote": str, "dynamic": bool}`이고 `(line, spec)` 순으로 정렬된다.
  - `python.extract(path, text)`의 결과에 `"file_refs"` 키가 생긴다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_python_filerefs.py`:

```python
from arche_lib.langs import python
from arche_lib.langs.python_filerefs import file_refs


def _specs(source):
    return [(ref["line"], ref["spec"], ref["dynamic"]) for ref in file_refs("m.py", source)]


def test_a_literal_path_is_collected_with_its_line_and_quote():
    refs = file_refs("m.py", 'x = 1\nload = Op(task_id="load", sql="sql/load.sql")\n')

    assert refs == [{"spec": "sql/load.sql", "line": 2, "quote": 'load = Op(task_id="load", sql="sql/load.sql")',
                     "dynamic": False}]


def test_the_call_a_path_is_passed_to_does_not_matter():
    source = 'a = read_sql("a.sql")\nb = open("b.hql").read()\nC = "c.SQL"\n'

    assert _specs(source) == [(1, "a.sql", False), (2, "b.hql", False), (3, "c.SQL", False)]


def test_docstrings_and_bare_strings_are_left_out():
    source = '"""x.sql"""\ndef f():\n    "doc.sql"\n    return 1\n'

    assert _specs(source) == []


def test_text_that_is_not_one_path_is_left_out():
    assert _specs('A = "select 1 from a.sql"\nB = ".sql"\nC = "notes.txt"\nD = 4 / 2\n') == []


def test_path_join_keeps_the_trailing_literal_pieces():
    source = (
        'import os\nfrom os.path import join\n'
        'a = os.path.join(BASE, "sql", "a.sql")\n'
        'b = join("q", "b.sql")\n'
        'c = P.joinpath("s", "c.hql")\n'
        'd = os.path.join("s", name, "d.sql")\n'
    )

    assert _specs(source) == [(3, "sql/a.sql", False), (4, "q/b.sql", False), (5, "s/c.hql", False), (6, "d.sql", False)]


def test_pieces_used_in_a_join_are_not_collected_again():
    assert _specs('a = os.path.join("x.sql", "y.sql")\n') == [(1, "x.sql/y.sql", False)]


def test_a_string_join_is_not_a_path_join():
    assert _specs('a = ", ".join(["a.sql"])\n') == [(1, "a.sql", False)]


def test_path_division_keeps_the_trailing_literal_pieces():
    assert _specs('a = (Path(BASE) / "sql" / "a.sql").read_text()\n') == [(1, "sql/a.sql", False)]


def test_formatted_and_templated_paths_are_dynamic():
    source = (
        'a = f"sql/stage_{table}.sql"\n'
        'b = "%s.sql" % table\n'
        'c = "{}.sql".format(table)\n'
        'd = "{{ params.dir }}/d.sql"\n'
        'e = "sql/*.sql"\n'
    )

    assert _specs(source) == [(1, "sql/stage_{}.sql", True), (2, "%s.sql", True), (3, "{}.sql", True),
                              (4, "{{ params.dir }}/d.sql", True), (5, "sql/*.sql", True)]


def test_an_f_string_without_values_is_a_plain_path():
    assert _specs('a = f"plain.sql"\n') == [(1, "plain.sql", False)]


def test_a_file_that_does_not_parse_has_no_references():
    assert file_refs("broken.py", "def (:\n") == []


def test_the_python_adapter_reports_file_references():
    assert [ref["spec"] for ref in python.extract("m.py", 'Q = "q/x.sql"\n')["file_refs"]] == ["q/x.sql"]
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_python_filerefs.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'arche_lib.langs.python_filerefs'`

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/langs/python_filerefs.py`:

```python
"""SQL file paths written as text in a Python file, read with the built-in ast module."""
from __future__ import annotations

import ast
from typing import Dict, List, Optional, Sequence, Set, Tuple

from ..fileref import is_path, is_template

MAX_QUOTE_CHARS = 200
_JOIN_NAMES = ("join", "joinpath")
Found = Optional[Tuple[str, int]]


def file_refs(path: str, text: str) -> List[dict]:
    """[{"spec", "line", "quote", "dynamic"}], ordered by line. Empty when the file does not parse."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    lines = text.splitlines()
    consumed: Set[int] = set()
    found: Dict[Tuple[int, str], dict] = {}
    for node in ast.walk(tree):  # breadth first: a parent is seen before the text inside it
        if id(node) in consumed:
            continue
        hit = _read(node, consumed)
        if hit is None:
            continue
        spec, line = hit
        quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if 1 <= line <= len(lines) else ""
        found.setdefault((line, spec), {"spec": spec, "line": line, "quote": quote, "dynamic": is_template(spec)})
    return [found[key] for key in sorted(found)]


def _text(node: ast.AST) -> Optional[str]:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _read(node: ast.AST, consumed: Set[int]) -> Found:
    if isinstance(node, ast.Expr) and _text(node.value) is not None:
        consumed.add(id(node.value))  # a docstring or a bare string statement names no file
        return None
    if isinstance(node, ast.JoinedStr):
        consumed.update(id(value) for value in node.values)
        spec = "".join("{}" if _text(value) is None else _text(value) for value in node.values)
        return (spec, node.lineno) if is_path(spec) else None
    if isinstance(node, ast.Call) and _is_join(node.func):
        return _joined(list(node.args), consumed)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _joined(_division_operands(node), consumed)
    text = _text(node)
    return (text, node.lineno) if text is not None and is_path(text) else None


def _is_join(func: ast.AST) -> bool:
    if isinstance(func, ast.Name):
        return func.id == "join"
    return isinstance(func, ast.Attribute) and func.attr in _JOIN_NAMES and _text(func.value) is None


def _division_operands(node: ast.BinOp) -> List[ast.AST]:
    """`a / "b" / "c"` as [a, "b", "c"]."""
    operands = [node.right]
    left = node.left
    while isinstance(left, ast.BinOp) and isinstance(left.op, ast.Div):
        operands.append(left.right)
        left = left.left
    operands.append(left)
    return operands[::-1]


def _joined(operands: Sequence[ast.AST], consumed: Set[int]) -> Found:
    """The trailing run of text operands joined with `/`; operands before it are dropped."""
    run = []
    for operand in reversed(operands):
        if _text(operand) is None:
            break
        run.append(operand)
    if not run:
        return None
    spec = "/".join(piece for piece in (_text(operand).strip("/") for operand in reversed(run)) if piece)
    if not is_path(spec):
        return None
    consumed.update(id(operand) for operand in run)
    return spec, run[0].lineno
```

`skills/arche/scripts/arche_lib/langs/python.py` 전체를 다음으로 바꾼다.

```python
"""Python adapter: the three functions every language adapter provides."""
from __future__ import annotations

from .python_extract import extract as extract_imports
from .python_filerefs import file_refs
from .python_http import routes_and_mounts
from .python_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_routes"], result["http_mounts"], result["http_routers"] = routes_and_mounts(path, text)
    result["file_refs"] = file_refs(path, text)
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_python_filerefs.py tests/test_stdlib_only.py -q`
Expected: PASS

Run: `python3 -m pytest -q`
Expected: 전체 통과. `python.extract`의 결과를 딕셔너리 전체로 비교하는 테스트가 실패하면 그 기대값에 `"file_refs": []`를 더한다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/python_filerefs.py skills/arche/scripts/arche_lib/langs/python.py tests/unit/test_python_filerefs.py
git commit -m "Collect SQL paths written as text in Python files"
```

---

### Task 4: `REFERENCES` edge 생성

**Files:**
- Modify: `skills/arche/scripts/arche_lib/graph.py`
- Test: `tests/unit/test_graph_references.py`

**Interfaces:**
- Consumes: `extracted[path]["file_refs"]` (Task 3), `fileref.index`, `fileref.resolve` (Task 2), 언어 `sql` 노드 (Task 1)
- Produces: `graph["edges"]`의 새 항목.
  - 해석됨: `{"kind": "REFERENCES", "from": "file:<py>", "to": "file:<sql>", "origin": "script", "status": "verified"|"inferred", "rule": ..., "evidence": {"path", "line", "quote"}}`
  - 미해결: 위에서 `"to": None`이고 `"spec"`, `"reason"`, 필요하면 `"candidates"`가 붙는다. `rule`은 없다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_graph_references.py`:

```python
from test_graph_multilang import _graph

SOURCES = {
    "dags/a/dag.py": (
        'import os\n'
        'LOAD = "sql/load.sql"\n'
        'AGAIN = "sql/load.sql"; SAME = "sql/load.sql"\n'
        'GONE = "sql/gone.sql"\n'
        'STAGE = f"sql/{name}.sql"\n'
    ),
    "dags/a/sql/load.sql": "select 1\n",
}


def _references(graph):
    return [edge for edge in graph["edges"] if edge["kind"] == "REFERENCES"]


def test_a_path_in_python_text_becomes_a_reference_edge_with_evidence():
    assert _references(_graph(SOURCES))[0] == {
        "kind": "REFERENCES", "from": "file:dags/a/dag.py", "to": "file:dags/a/sql/load.sql",
        "origin": "script", "status": "verified", "rule": "relative",
        "evidence": {"path": "dags/a/dag.py", "line": 2, "quote": 'LOAD = "sql/load.sql"'}}


def test_one_edge_per_line_and_target_and_unresolved_paths_keep_their_reason():
    edges = _references(_graph(SOURCES))

    assert [(e["evidence"]["line"], e["to"], e.get("spec"), e.get("reason")) for e in edges] == [
        (2, "file:dags/a/sql/load.sql", None, None), (3, "file:dags/a/sql/load.sql", None, None),
        (4, None, "sql/gone.sql", "not_found"), (5, None, "sql/{}.sql", "dynamic")]
    assert all("rule" not in e for e in edges if e["to"] is None)


def test_several_matching_files_are_kept_as_candidates():
    graph = _graph({"m.py": 'Q = "x.sql"\n', "q/x.sql": "select 1\n", "r/x.sql": "select 2\n"})

    assert [(e["status"], e["reason"], e["candidates"]) for e in _references(graph)] == [
        ("unresolved", "ambiguous", ["q/x.sql", "r/x.sql"])]


def test_a_skipped_sql_file_is_not_a_target():
    big = {"path": "dags/a/sql/big.sql", "status": "skipped", "reason": "too_large"}
    graph = _graph({"dags/a/dag.py": 'BIG = "sql/big.sql"\n'}, extra_files=[big])

    assert [(e["to"], e["reason"]) for e in _references(graph)] == [(None, "not_found")]


def test_reference_edges_leave_imports_and_externals_as_they_were():
    graph = _graph(SOURCES)

    assert {e["kind"] for e in graph["edges"]} == {"REFERENCES"}
    assert graph["externals"] == [{"from": "file:dags/a/dag.py", "package": "os", "line": 1}]
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_graph_references.py -q`
Expected: FAIL. `_references(...)`가 빈 목록이라 `IndexError` 또는 비교 실패가 난다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/graph.py`의 모듈 docstring과 import를 바꾼다.

```python
"""Build graph.json content: file and symbol nodes plus IMPORTS and REFERENCES edges."""
```

```python
from . import SCHEMA_VERSION, fileref
```

`_edge_key` 함수 바로 아래에 추가한다.

```python
def _references(analyzed: List[dict], extracted: Dict[str, dict]) -> List[dict]:
    """REFERENCES edges: a SQL file path written as text, resolved against the parsed SQL files."""
    by_name = fileref.index(entry["path"] for entry in analyzed if entry["lang"] == "sql" and entry["path"] in extracted)
    edges: Dict[tuple, dict] = {}
    for path in sorted(extracted):
        for ref in extracted[path].get("file_refs", []):
            result = fileref.resolve(ref["spec"], path, by_name, ref["dynamic"])
            base = {
                "kind": "REFERENCES",
                "from": _file_id(path),
                "origin": "script",
                "status": result["status"],
                "evidence": {"path": path, "line": ref["line"], "quote": ref["quote"]},
            }
            if result["status"] in _RESOLVED:
                edge = {**base, "rule": result["rule"], "to": _file_id(result["to"])}
            else:
                candidates = {"candidates": result["candidates"]} if "candidates" in result else {}
                edge = {**base, **candidates, "to": None, "spec": result["spec"], "reason": result["reason"]}
            edges.setdefault(_edge_key(edge), edge)
    return list(edges.values())
```

`build_graph`에서 `connections, routes = _http(extracted, edges)` 줄은 그대로 둔다. HTTP 마운트 해석은 import edge만 받아야 한다. 그 아래 반환값의 `"edges"` 줄만 바꾼다.

```python
        "edges": sorted(edges + _references(analyzed, extracted), key=_edge_key),
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과

Run: `python3 evals/score.py`
Expected: 종료 코드 0. 기존 fixture에는 SQL 경로 문자열이 없어 결과가 그대로다. `MISMATCH`가 나오면 그 fixture의 Python 파일에 `.sql` 문자열이 있는지 확인하고 Task 5에서 그 fixture의 정답에 `references`를 적는다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/graph.py tests/unit/test_graph_references.py
git commit -m "Record SQL paths in Python text as REFERENCES edges"
```

---

### Task 5: 합성 fixture와 채점

**Files:**
- Modify: `evals/score.py`
- Create: `evals/fixtures/airflow-sql/repo/` 아래 9개 파일, `evals/fixtures/airflow-sql/expected_graph.json`
- Test: `tests/evals/test_fixtures.py`

**Interfaces:**
- Consumes: `REFERENCES` edge (Task 4)
- Produces: `expected_graph.json`의 선택 키 `references`. 항목은 `{"from": <path>, "target": <해석된 경로 또는 spec>, "status": ..., "how": <rule 또는 reason>}`. `score.observed_sets(graph)["references"]`는 `(from, target, status, how)` 튜플의 집합이다.

- [ ] **Step 1: fixture 파일 작성**

`evals/fixtures/airflow-sql/repo/dags/billing/daily_dag.py`:

```python
"""Daily billing load."""
import os
from pathlib import Path

from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

from common.readers import read_sql

BASE = os.path.dirname(__file__)
TABLES = ["orders", "refunds"]

with DAG("billing_daily") as dag:
    load = SQLExecuteQueryOperator(task_id="load", sql="sql/load_orders.sql")
    settle = SQLExecuteQueryOperator(task_id="settle", sql=open(os.path.join(BASE, "sql", "settle.hql")).read())
    close = SQLExecuteQueryOperator(task_id="close", sql=read_sql("dags/ledger/sql/close.sql"))
    audit = SQLExecuteQueryOperator(task_id="audit", sql=(Path(BASE) / "sql" / "audit.sql").read_text())
    for table in TABLES:
        SQLExecuteQueryOperator(task_id=f"stage_{table}", sql=f"sql/stage_{table}.sql")
    load >> settle >> close >> audit
```

`evals/fixtures/airflow-sql/repo/dags/common/readers.py`:

```python
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def read_sql(name):
    with open(os.path.join(ROOT, name)) as handle:
        return handle.read()
```

`evals/fixtures/airflow-sql/repo/dags/ledger/ledger_dag.py`:

```python
from airflow import DAG
from airflow.providers.apache.hive.operators.hive import HiveOperator

with DAG("ledger") as dag:
    close = HiveOperator(task_id="close", hql="close.sql")
    report = HiveOperator(task_id="report", hql="report.sql")
    settle = HiveOperator(task_id="settle", hql="/opt/airflow/dags/billing/sql/settle.hql")
    close >> report >> settle
```

SQL 파일 여섯 개는 각각 한 줄이다.

| 경로 (`evals/fixtures/airflow-sql/repo/` 아래) | 내용 |
|---|---|
| `dags/billing/sql/load_orders.sql` | `INSERT INTO billing.orders SELECT * FROM staging.orders;` |
| `dags/billing/sql/settle.hql` | `INSERT OVERWRITE TABLE billing.settled SELECT * FROM billing.orders;` |
| `dags/billing/sql/unused.sql` | `SELECT 1;` |
| `dags/ledger/sql/close.sql` | `INSERT INTO ledger.closed SELECT * FROM billing.settled;` |
| `dags/ledger/sql/report.sql` | `SELECT * FROM ledger.closed;` |
| `dags/shared/sql/report.sql` | `SELECT * FROM shared.calendar;` |

`evals/fixtures/airflow-sql/expected_graph.json`:

```json
{
  "analyzed": [
    "dags/billing/daily_dag.py",
    "dags/billing/sql/load_orders.sql",
    "dags/billing/sql/settle.hql",
    "dags/billing/sql/unused.sql",
    "dags/common/readers.py",
    "dags/ledger/ledger_dag.py",
    "dags/ledger/sql/close.sql",
    "dags/ledger/sql/report.sql",
    "dags/shared/sql/report.sql"
  ],
  "skipped": [],
  "imports": [],
  "inferred": [],
  "unresolved": [],
  "externals": [
    {"from": "dags/billing/daily_dag.py", "package": "airflow"},
    {"from": "dags/billing/daily_dag.py", "package": "common"},
    {"from": "dags/billing/daily_dag.py", "package": "os"},
    {"from": "dags/billing/daily_dag.py", "package": "pathlib"},
    {"from": "dags/common/readers.py", "package": "os"},
    {"from": "dags/ledger/ledger_dag.py", "package": "airflow"}
  ],
  "symbols": [
    "sym:dags/billing/daily_dag.py#BASE",
    "sym:dags/billing/daily_dag.py#TABLES",
    "sym:dags/common/readers.py#ROOT",
    "sym:dags/common/readers.py#read_sql"
  ],
  "references": [
    {"from": "dags/billing/daily_dag.py", "target": "dags/billing/sql/load_orders.sql", "status": "verified", "how": "relative"},
    {"from": "dags/billing/daily_dag.py", "target": "dags/billing/sql/settle.hql", "status": "verified", "how": "relative"},
    {"from": "dags/billing/daily_dag.py", "target": "dags/ledger/sql/close.sql", "status": "verified", "how": "root"},
    {"from": "dags/billing/daily_dag.py", "target": "sql/audit.sql", "status": "unresolved", "how": "not_found"},
    {"from": "dags/billing/daily_dag.py", "target": "sql/stage_{}.sql", "status": "unresolved", "how": "dynamic"},
    {"from": "dags/ledger/ledger_dag.py", "target": "dags/ledger/sql/close.sql", "status": "inferred", "how": "suffix"},
    {"from": "dags/ledger/ledger_dag.py", "target": "report.sql", "status": "unresolved", "how": "ambiguous"},
    {"from": "dags/ledger/ledger_dag.py", "target": "/opt/airflow/dags/billing/sql/settle.hql", "status": "unresolved", "how": "placeholder"}
  ]
}
```

마지막 `references` 항목은 Step 3에서 실패를 확인하기 위해 일부러 틀리게 적었다. Step 5에서 고친다.

`from common.readers import read_sql`은 `dags/`가 import 루트라는 것을 Arche가 모르므로 외부 패키지 `common`으로 기록된다. 현재 동작이고 이 계획의 범위가 아니다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/evals/test_fixtures.py`의 `FIXTURES`에 `"airflow-sql"`을 더한다.

```python
FIXTURES = {"py-basic", "py-dynamic", "secrets", "ts-basic", "ts-alias", "ts-dynamic", "dart-basic", "hono-mobile", "jvm-basic", "airflow-sql"}
```

`test_every_language_is_scored` 아래에 추가한다.

```python
def test_file_references_stay_out_of_import_precision_and_recall(results):
    airflow = next(r for r in results if r["fixture"] == "airflow-sql")

    assert all(airflow[outcome] == [] for outcome in score.OUTCOMES)
    assert airflow["inferred_correct"] == [] and airflow["inferred_wrong"] == []
```

- [ ] **Step 3: 실패 확인**

Run: `python3 -m pytest tests/evals/test_fixtures.py -q`
Expected: FAIL. `score.py`가 `references`를 아직 비교하지 않아 `REFERENCES` edge가 `imports`·`inferred`·`unresolved` 집합에 섞이고, `test_every_fixture_matches_its_expected_sets`와 새 테스트가 실패한다.

- [ ] **Step 4: `score.py` 구현**

모듈 docstring 첫 문단 끝에 한 문장을 더한다.

```python
REFERENCES edges (a SQL file path written as text) are compared exactly through the
`references` list and never count toward precision or recall.
```

`EXACT_SETS`를 바꾼다.

```python
EXACT_SETS = ("analyzed", "skipped", "inferred", "unresolved", "externals", "symbols", "http", "references")
```

`observed_sets`를 다음으로 바꾼다.

```python
def _reference_key(edge):
    """(from, resolved path or written spec, status, rule or reason)."""
    target = _path(edge["to"]) if edge["to"] else edge["spec"]
    return (_path(edge["from"]), target, edge["status"], edge.get("rule") or edge["reason"])


def observed_sets(graph):
    edges = [e for e in graph["edges"] if e["kind"] == "IMPORTS"]
    return {
        "analyzed": {_path(n["id"]) for n in graph["nodes"] if n["type"] == "file"},
        "skipped": {(s["path"], s["reason"]) for s in graph["skipped"]},
        "imports": {(_path(e["from"]), _path(e["to"])) for e in edges if e["status"] == "verified"},
        "inferred": {(_path(e["from"]), _path(e["to"])) for e in edges if e["status"] == "inferred"},
        "unresolved": {(_path(e["from"]), e["spec"]) for e in edges if e["status"] == "unresolved"},
        "externals": {(_path(x["from"]), x["package"]) for x in graph["externals"]},
        "symbols": {n["id"] for n in graph["nodes"] if n["type"] == "symbol"},
        "http": {_http_key(c) for c in graph.get("http_connections", [])},
        "references": {_reference_key(e) for e in graph["edges"] if e["kind"] == "REFERENCES"},
    }
```

`expected_sets`의 반환 딕셔너리에 마지막 항목으로 더한다.

```python
        "references": {(r["from"], r["target"], r["status"], r["how"]) for r in expected.get("references", [])},
```

- [ ] **Step 5: 남은 한 건의 실패를 확인하고 정답을 고침**

Run: `python3 evals/score.py`
Expected: 종료 코드 1이고 `airflow-sql` 아래에 `MISMATCH references` 한 줄만 나온다. `missing`에는 `placeholder` 항목이, `extra`에는 `('dags/ledger/ledger_dag.py', 'dags/billing/sql/settle.hql', 'inferred', 'suffix')`가 있다.

`expected_graph.json`의 마지막 `references` 항목을 바꾼다.

```json
    {"from": "dags/ledger/ledger_dag.py", "target": "dags/billing/sql/settle.hql", "status": "inferred", "how": "suffix"}
```

- [ ] **Step 6: 통과 확인**

Run: `python3 evals/score.py`
Expected: 종료 코드 0. `airflow-sql` 줄은 `0 0 0`이고 `MISMATCH`가 없다.

Run: `python3 -m pytest -q`
Expected: 전체 통과

- [ ] **Step 7: 커밋**

```bash
git add evals/score.py evals/fixtures/airflow-sql tests/evals/test_fixtures.py
git commit -m "Score file references against a synthetic Airflow fixture"
```

---

### Task 6: 종류를 가려 읽는 곳 고치기 (리비전 비교, 지도, 폴더 요약)

**Files:**
- Modify: `skills/arche/scripts/arche_lib/changes.py:41-49`
- Modify: `skills/arche/scripts/arche_lib/atlas.py:293-300`
- Modify: `skills/arche/assets/map-template.html:277`
- Modify: `skills/arche/scripts/brief.py:65-80`
- Test: `tests/unit/test_changes_cli.py`, `tests/unit/test_atlas.py`

**Interfaces:**
- Consumes: `REFERENCES` edge (Task 4)
- Produces:
  - `changes.json`: SQL 파일이 바뀌면 그 파일을 참조하는 Python 파일이 `impact`에 들어간다.
  - `atlas.json`의 `links`: 부품 사이 참조는 `"kind": "reference"`다.
  - `brief.txt`: `FILE REFERENCES BETWEEN FOLDERS` 구역.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_changes_cli.py` 끝에 추가한다.

```python
def test_changing_a_sql_file_reaches_the_python_file_that_names_it(make_repo, git_cmd, run_script, tmp_path):
    repo = make_repo({"dag.py": 'QUERY = "q/load.sql"\n', "q/load.sql": "select 1\n"})
    base = git_cmd(repo, "rev-parse", "HEAD").strip()
    out = tmp_path / "run"
    _snapshot(repo, out, run_script)
    (repo / "q" / "load.sql").write_text("select 2\n")
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", "edit")
    head = git_cmd(repo, "rev-parse", "HEAD").strip()

    result = run_script("changes.py", "--out", out, "--base", base, "--head", head)

    assert result.returncode == 0, result.stderr
    files = {item["path"]: item for item in json.loads((out / "changes.json").read_text())["files"]}
    assert files["q/load.sql"]["impact"] == ["dag.py"]
```

`tests/unit/test_atlas.py` 끝에 추가한다.

```python
def test_a_file_reference_between_parts_is_a_reference_link():
    graph = _graph({"dags/dag.py": 'Q = "queries/load.sql"\n', "queries/load.sql": "select 1\n"})
    inventory = {"files": [{"path": n["id"][5:], "status": "analyzed", "lines": n["lines"], "sha256": n["sha256"]}
                           for n in graph["nodes"] if n["type"] == "file"]}
    document = {"version": 1, "project": {"name": "demo", "summary": "s"}, "parts": [
        {"id": "dags", "label": "DAGs", "kind": "script", "summary": "pipelines", "paths": ["dags"]},
        {"id": "queries", "label": "Queries", "kind": "data", "summary": "sql", "paths": ["queries"]}],
        "edges": [], "flows": []}

    atlas = build_atlas(inventory, graph, document, {})

    assert [(l["from"], l["to"], l["status"], l["kind"], l["count"]) for l in atlas["links"]] == [
        ("dags", "queries", "verified", "reference", 1)]
    assert atlas["file_links"] == [[0, 1, "verified", 1]]


def test_brief_lists_file_references_between_folders_apart_from_imports(make_repo, run_script, tmp_path):
    repo = make_repo({"dags/a/dag.py": 'Q = "dags/b/sql/x.sql"\n', "dags/b/sql/x.sql": "select 1\n"})
    out = tmp_path / "out"
    assert run_script("arche.py", repo, "--out", out).returncode == 0
    assert run_script("brief.py", "--out", out).returncode == 0
    text = (out / "brief.txt").read_text()

    def section(title):
        return text.split(title)[1].split("\n\n")[0]

    assert "  dags/a -> dags/b  1" in section("FILE REFERENCES BETWEEN FOLDERS")
    assert "dags/a -> dags/b" not in section("IMPORTS BETWEEN FOLDERS")
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_changes_cli.py tests/unit/test_atlas.py -q`
Expected: 새 테스트 세 개가 FAIL. `impact`가 `[]`이고, 연결 종류가 `import`이며, `FILE REFERENCES BETWEEN FOLDERS`가 없어 `IndexError`가 난다.

`test_a_file_reference_between_parts_is_a_reference_link`가 `build_atlas`의 문서 검증에서 실패하면(`IntegrityError`) 같은 파일의 `_document()`와 비교해 빠진 필수 필드를 채운다. 연결 종류에 대한 단언은 바꾸지 않는다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/changes.py`. `_reverse_imports` 위에 상수를 두고 조건을 바꾼다.

```python
_DEPENDENCY_KINDS = ("IMPORTS", "REFERENCES")


def _reverse_imports(graph: dict) -> dict:
    reverse = defaultdict(set)
    for edge in graph["edges"]:
        source, target = edge.get("from"), edge.get("to")
        if (edge.get("kind") in _DEPENDENCY_KINDS and edge.get("status") in ("verified", "inferred")
                and isinstance(source, str) and source.startswith("file:")
                and isinstance(target, str) and target.startswith("file:")):
            reverse[target[5:]].add(source[5:])
    return reverse
```

`skills/arche/scripts/arche_lib/atlas.py`. `for edge in graph["edges"]:` 루프의 마지막 두 줄을 바꾼다.

```python
        if a and b and a != b:
            kind = "reference" if edge.get("kind") == "REFERENCES" else "import"
            counts[a, b, edge["status"], kind, None, None] += 1
```

`skills/arche/assets/map-template.html` 277번째 줄. 지도는 파일 사이의 선을 `D.file_links`로 이미 그린다(275번째 줄). `reference` 연결을 `D.links`에서 한 번 더 그리지 않도록 조건을 바꾼다.

```javascript
  for (const item of D.links) if (item.kind !== 'import' && item.kind !== 'reference') link(end(item, 'from'), end(item, 'to'), item.status, item.count, item.label);
```

`skills/arche/scripts/brief.py`. `fan_in, fan_out, between = Counter(), Counter(), Counter()`부터 그 `for` 루프 끝까지를 다음으로 바꾼다.

```python
    fan_in, fan_out, between, referenced = Counter(), Counter(), Counter(), Counter()
    resolved = {(e["from"][5:], e["to"][5:], e.get("kind", "IMPORTS")) for e in graph["edges"] if e["to"] and e["to"][5:] in files}
    for source, target, kind in sorted(resolved):
        if source == target:
            continue
        fan_out[source] += 1
        fan_in[target] += 1
        a, b = ("/".join(p.split("/")[:-1][:2]) or "." for p in (source, target))
        if a != b:
            (referenced if kind == "REFERENCES" else between)[a, b] += 1
```

`IMPORTS BETWEEN FOLDERS` 구역을 만드는 두 줄 바로 아래에 추가한다.

```python
    lines += ["", "FILE REFERENCES BETWEEN FOLDERS (a SQL file path written as text; two levels deep)"]
    lines += [f"  {a} -> {b}  {count}" for (a, b), count in sorted(referenced.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP * 2]] or ["  none"]
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과

- [ ] **Step 5: 지도를 눈으로 확인**

```bash
out=$(mktemp -d) && python3 -B skills/arche/scripts/arche.py evals/fixtures/airflow-sql/repo --out "$out" && python3 -B skills/arche/scripts/map.py --out "$out" && python3 -B skills/arche/scripts/render.py --out "$out" && echo "$out/map.html"
```

출력된 `map.html`을 브라우저로 열어 `billing` 쪽 카드에서 SQL 파일로 가는 선이 한 번만 그려지는지 확인한다. 선이 겹쳐 두 번 그려지면 277번째 줄의 조건을 다시 본다. 명령의 인자가 맞지 않으면 `skills/arche/SKILL.md`의 실행 절차를 따른다.

- [ ] **Step 6: 커밋**

```bash
git add skills/arche/scripts/arche_lib/changes.py skills/arche/scripts/arche_lib/atlas.py skills/arche/assets/map-template.html skills/arche/scripts/brief.py tests/unit/test_changes_cli.py tests/unit/test_atlas.py
git commit -m "Carry file references into change impact, the map and the brief"
```

---

### Task 7: 리포트의 "SQL file references" 구역

**Files:**
- Modify: `skills/arche/scripts/arche_lib/report.py`
- Modify: `skills/arche/scripts/arche_lib/i18n.py`
- Test: `tests/unit/test_report_file_references.py`

**Interfaces:**
- Consumes: `REFERENCES` edge (Task 4), 언어 `sql` 노드 (Task 1)
- Produces: 리포트 HTML의 `<section id="file-references">`. SQL 파일도 `REFERENCES` edge도 없으면 구역과 목차 항목이 없다.

SQL 파일마다 "누가 참조하는가"는 이 구역에서 만들지 않는다. 기존 **File dependencies**의 파일 상세가 edge 종류를 가리지 않고 들어오는 edge를 보여 주므로 SQL 파일을 펼치면 참조하는 Python 파일과 근거 줄이 이미 나온다. 이 Task는 그 목록에 없는 것, 곧 참조가 없는 SQL과 해석하지 못한 경로를 더한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_report_file_references.py`:

```python
"""The report section for SQL files and the paths that name them."""
from pathlib import Path

from arche_lib.report import render

TEMPLATE = (Path(__file__).resolve().parents[2] / "skills/arche/assets/report-template.html").read_text()


def _edge(line, to=None, spec=None, reason=None):
    edge = {"kind": "REFERENCES", "from": "file:dags/dag.py", "to": to, "origin": "script",
            "status": "verified" if to else "unresolved",
            "evidence": {"path": "dags/dag.py", "line": line, "quote": "q"}}
    return edge if to else {**edge, "spec": spec, "reason": reason}


def _graph(edges, sql=("dags/sql/load.sql", "dags/sql/unused.sql")):
    return {
        "repo": {"name": "sample", "commit": None, "dirty": None},
        "nodes": [{"type": "file", "id": "file:dags/dag.py", "lang": "python", "lines": 3}]
                 + [{"type": "file", "id": "file:" + path, "lang": "sql", "lines": 1} for path in sql],
        "edges": edges, "externals": [], "skipped": [],
    }


def _section(html):
    return html.split('<section id="file-references">')[1].split("</section>")[0]


def test_sql_files_no_parsed_file_names_are_listed():
    section = _section(render(TEMPLATE, _graph([_edge(1, to="file:dags/sql/load.sql")]), [], "match"))

    assert "SQL files with no static reference" in section
    assert "<code>dags/sql/unused.sql</code>" in section
    assert "<code>dags/sql/load.sql</code>" not in section
    assert "run time" not in section


def test_dynamic_paths_warn_that_a_listed_file_may_still_be_used():
    edges = [_edge(1, to="file:dags/sql/load.sql"), _edge(2, spec="sql/{}.sql", reason="dynamic")]
    section = _section(render(TEMPLATE, _graph(edges), [], "match"))

    assert 'data-tone="warn"' in section and "1 path" in section
    assert "<code>sql/{}.sql</code> · <code>dags/dag.py:2</code>" in section


def test_unresolved_paths_are_folded_by_reason():
    edges = [_edge(3, spec="sql/gone.sql", reason="not_found"), _edge(4, spec="x.sql", reason="ambiguous")]
    section = _section(render(TEMPLATE, _graph(edges), [], "match"))

    assert section.count('<details class="group">') == 2
    assert "<code>not_found</code>" in section and "<code>ambiguous</code>" in section
    assert "<code>sql/gone.sql</code> · <code>dags/dag.py:3</code>" in section


def test_a_repository_without_sql_has_no_section_or_navigation_entry():
    html = render(TEMPLATE, _graph([], sql=()), [], "match")

    assert 'id="file-references"' not in html and 'href="#file-references"' not in html


def test_the_section_is_translated():
    html = render(TEMPLATE, _graph([_edge(1, to="file:dags/sql/load.sql")]), [], "match", lang="ko")

    assert "정적 참조가 없는 SQL 파일" in _section(html)
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_report_file_references.py -q`
Expected: FAIL. 구역이 없어 `_section`에서 `IndexError`가 난다. 마지막에서 두 번째 테스트만 통과한다.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/report.py`. `_graph_data` 함수 위에 추가한다.

```python
_UNRESOLVED_REFERENCES = (
    ("dynamic", "Paths built at run time"),
    ("ambiguous", "Paths matching several files"),
    ("not_found", "Paths matching no scanned file"),
)


def _file_references(edges, files, anchors):
    """SQL files nothing names, and written paths that were not followed. Empty without SQL or references."""
    sql = [file["id"] for file in files if file["lang"] == "sql"]
    references = [edge for edge in edges if edge.get("kind") == "REFERENCES"]
    if not sql and not references:
        return ""
    named = {edge["to"] for edge in references if edge["to"]}
    unnamed = [node_id for node_id in sql if node_id not in named]
    by_reason = defaultdict(list)
    for edge in references:
        if not edge["to"]:
            by_reason[edge["reason"]].append(f'{_code(edge["spec"])} · {_evidence(edge)}')
    dynamic = len(by_reason["dynamic"])
    warning = ""
    if dynamic:
        counted = T("1 path is built at run time.") if dynamic == 1 else f'{dynamic:,} {T("paths are built at run time.")}'
        warning = f'<p class="callout" data-tone="warn">{counted} {T("A file listed here may still be used through one of them.")}</p>'
    body = (f'<h3>{T("SQL files with no static reference")} <span class="count">{len(unnamed)}</span></h3>' + warning
            + _items((_file_link(node_id, anchors) for node_id in unnamed), T("Every SQL file is named by a parsed file."), "columns"))
    for reason, title in _UNRESOLVED_REFERENCES:
        if by_reason[reason]:
            body += (f'<details class="group"><summary>{T(title)} <code>{reason}</code> <span class="count">{len(by_reason[reason])}</span></summary>'
                     + _items(by_reason[reason], "", "columns") + '</details>')
    return _section("file-references", T("SQL file references"), body,
                    T("A reference is a SQL file path written as text in a parsed file. Open a SQL file under File dependencies to see what names it. Which operator or task runs it is not read."))
```

`_render`의 `sections` 목록에서 `("http", ...)` 줄 바로 아래에 한 항목을 넣는다.

```python
        ("file-references", T("SQL file references"),
         sum(1 for edge in edges if edge.get("kind") == "REFERENCES" and edge["to"]) or None,
         _file_references(edges, files, anchors)),
```

`skills/arche/scripts/arche_lib/i18n.py`의 `_KO`에서 `"HTTP connections": "HTTP 연결",` 줄 아래에 추가한다.

```python
    "SQL file references": "SQL 파일 참조",
    "A reference is a SQL file path written as text in a parsed file. Open a SQL file under File dependencies to see what names it. Which operator or task runs it is not read.":
        "참조는 분석한 파일 안에 문자열로 적힌 SQL 파일 경로입니다. 파일 의존성에서 SQL 파일을 열면 그 파일을 가리키는 곳을 볼 수 있습니다. 어느 operator나 task가 실행하는지는 읽지 않습니다.",
    "SQL files with no static reference": "정적 참조가 없는 SQL 파일",
    "Every SQL file is named by a parsed file.": "모든 SQL 파일을 분석한 파일이 가리킵니다.",
    "1 path is built at run time.": "경로 1개가 실행 시점에 만들어집니다.",
    "paths are built at run time.": "개의 경로가 실행 시점에 만들어집니다.",
    "A file listed here may still be used through one of them.": "여기 나온 파일도 그런 경로로 쓰일 수 있습니다.",
    "Paths built at run time": "실행 시점에 만들어지는 경로",
    "Paths matching several files": "여러 파일과 일치하는 경로",
    "Paths matching no scanned file": "스캔한 파일과 일치하지 않는 경로",
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과. `i18n`의 모든 영어 문구에 한국어가 있는지 검사하는 테스트가 실패하면 빠진 문구를 `_KO`에 더한다.

- [ ] **Step 5: 리포트를 눈으로 확인**

```bash
out=$(mktemp -d) && python3 -B skills/arche/scripts/arche.py evals/fixtures/airflow-sql/repo --out "$out" --lang ko && echo "$out/report.html"
```

브라우저에서 다음을 확인한다.

1. 목차에 **SQL 파일 참조 5**가 있다(해석된 참조 5건).
2. **정적 참조가 없는 SQL 파일**에 `unused.sql`과 두 `report.sql`이 있고, 그 위에 "경로 1개가 실행 시점에 만들어집니다" 경고가 있다.
3. 접힌 구역 세 개(`dynamic` 1, `ambiguous` 1, `not_found` 1)가 있다.
4. **파일 의존성**에서 `dags/billing/sql/settle.hql`을 펼치면 `daily_dag.py`와 `ledger_dag.py`가 근거 줄과 함께 나온다.

- [ ] **Step 6: 커밋**

```bash
git add skills/arche/scripts/arche_lib/report.py skills/arche/scripts/arche_lib/i18n.py tests/unit/test_report_file_references.py
git commit -m "List unreferenced SQL files and unfollowed paths in the report"
```

---

### Task 8: 문서와 최종 확인

**Files:**
- Modify: `README.md:68`
- Modify: `docs/artifact-compatibility.md:5`
- Modify: `docs/release-notes.md`
- Modify: `skills/arche/SKILL.md:55,61`

**Interfaces:**
- Consumes: Task 1~7의 동작
- Produces: 없음

- [ ] **Step 1: `README.md`**

68번째 줄에서 `합성 레포 9개`를 `합성 레포 10개`로 바꾸고, 그 문단 끝에 문장을 더한다.

```markdown
SQL·HQL 파일은 내용을 해석하지 않고 노드로만 둡니다. Python 파일에 문자열로 적힌 SQL 파일 경로는 **SQL 파일 참조**로 연결하고, f-string처럼 실행 시점에 만들어지는 경로는 따라가지 않고 건수만 보여 줍니다.
```

- [ ] **Step 2: `docs/artifact-compatibility.md`**

첫 행(`inventory.json, graph.json, changes.json`)의 Reader contract 칸 끝에 문장을 더한다.

```markdown
`graph.json` edges may carry `kind: "REFERENCES"` (a SQL file path written as text in a parsed file; `rule` is `relative`, `root` or `suffix`, `reason` is `dynamic`, `ambiguous` or `not_found`) and file nodes may carry `lang: "sql"`. Both are additive; readers that only follow `IMPORTS` skip them.
```

- [ ] **Step 3: `docs/release-notes.md`**

`## 1.0 출시 후보` 아래 첫 항목으로 넣는다.

```markdown
- SQL 참조: `.sql`·`.hql` 파일을 노드로 스캔하고, Python 파일에 문자열로 적힌 SQL 경로를 `REFERENCES` 연결로 기록합니다. `os.path.join`과 `Path /`로 이은 경로도 읽습니다. 리포트의 **SQL 파일 참조**에서 정적 참조가 없는 SQL 파일과 따라가지 못한 경로를 볼 수 있습니다. SQL 내용, `task >> task`, `template_searchpath`는 읽지 않습니다.
```

- [ ] **Step 4: `skills/arche/SKILL.md`**

55번째 줄의 `Python, Go, Dart, TypeScript, JavaScript, Java and Kotlin files`를 `Python, Go, Dart, TypeScript, JavaScript, Java and Kotlin files, plus SQL and HQL files as unparsed nodes`로 바꾼다.

61번째 줄 끝에 문장을 더한다.

```markdown
A `REFERENCES` edge is a SQL file path written as text in a Python file; it shows that the file names the SQL file, not which operator or task runs it, and paths built at run time are listed as `dynamic` and not followed.
```

- [ ] **Step 5: 전체 확인**

Run: `python3 -m pytest -q`
Expected: 전체 통과. 통과 수는 478에 이 계획에서 더한 테스트 수를 더한 값이다.

Run: `python3 evals/score.py`
Expected: 종료 코드 0. `airflow-sql`을 포함한 fixture 10개가 나오고 `MISMATCH`가 없다.

문서의 숫자나 문구를 검사하는 테스트(`tests/evals/test_evaluation_report.py`, `tests/evals/test_release_package.py`)가 실패하면 그 테스트가 요구하는 문서 위치를 함께 고친다.

- [ ] **Step 6: 커밋**

```bash
git add README.md docs/artifact-compatibility.md docs/release-notes.md skills/arche/SKILL.md
git commit -m "Document SQL file references"
```

---

## 계획 밖에 남는 일

- 사내 팀 폴더 재실행과 설계 문서의 성공 기준 확인은 사용자가 한다. 해석된 edge 30개를 손으로 확인해 오탐이 3개를 넘으면, 수집 규칙을 좁히기 전에 오탐의 형태를 먼저 분류한다.
- 지도에서 참조 선을 import 선과 다른 모양으로 그리는 일은 하지 않는다. 지금은 같은 선으로 그린다.
- 리포트의 파일 상세는 SQL 참조도 "Imports"·"Imported by" 제목 아래에 보여 준다. 제목을 바꾸는 일은 하지 않는다.
