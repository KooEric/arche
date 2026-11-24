"""Tables a SQL file reads and writes, found by keyword. Not a parser."""
from __future__ import annotations

import re
from bisect import bisect_left
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
_IDENTIFIER_BODY = re.compile(r"[\w$.]*")
# dbt's `{{ ref('model') }}` and `{{ source('src', 'table') }}`, found in masked text
# (their string arguments are blanked there) and read back from the original.
_DBT_CALL = re.compile(r"\{\{-?\s*(ref|source)\s*\(([^()]*)\)\s*-?\}\}")
_DBT_TOKEN = re.compile(r"^__dbt_call_(\d+)__$")
# Every `ref(` inside `{{ }}` or `{% %}` is a dbt dependency, wherever the SQL puts
# it (a bare line, even a `--` comment): Jinja renders first. `{# #}` hides it.
_JINJA_SPAN = re.compile(r"\{#.*?#\}|\{\{.*?\}\}|\{%.*?%\}", re.S)
_REF_OPEN = re.compile(r"\bref\s*\(")
_REF_CALL = re.compile(r"\bref\s*\(([^()]*)\)")
_VERSION_ARG = re.compile(r"\b(?:v|version)\s*=\s*['\"]?(\d+)['\"]?")
_LITERAL = re.compile(r"""\s*(?:'([^'\n]*)'|"([^"\n]*)")\s*""")
_CLOSERS = {"/*": "*/", "{%": "%}", "{#": "#}"}


def _span_end(text: str, start: int) -> int:
    """Index of the quote closing the backtick or double-quoted span at `start` on its line, or -1."""
    limit = text.find("\n", start)
    return text.find(text[start], start + 1, len(text) if limit < 0 else limit)


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
                end += 2 if text[end] in "\\'" else 1
            unterminated = unterminated or end >= size
            end = min(end + 1, size)
        elif text[i] in "`\"" and _span_end(text, i) > 0:
            close = _span_end(text, i)
            body = text[i + 1:close]
            keep = text[i] == "`" or _IDENTIFIER_BODY.fullmatch(body)
            out.append(text[i:close + 1] if keep else text[i] + re.sub(r"[^\n]", " ", body) + text[close])
            i = close + 1
            continue
        else:
            out.append(text[i])
            i += 1
            continue
        out.append("".join(char if char == "\n" else " " for char in text[i:end]))
        i = end
    return "".join(out), unterminated


def _literal_args(text: str) -> Optional[List[str]]:
    """Positional string arguments of a dbt call; keyword arguments (`v=2`) are ignored.
    None when a positional argument is not a string literal."""
    args: List[str] = []
    for part in (piece for piece in text.split(",") if piece.strip()):
        if "=" in part and not part.strip().startswith(("'", '"')):
            continue
        match = _LITERAL.fullmatch(part)
        if match is None:
            return None
        args.append(match.group(1) if match.group(1) is not None else match.group(2))
    return args


def _dbt_calls(text: str, masked: str) -> Tuple[str, List[Tuple[str, Optional[List[str]]]]]:
    """Replace each dbt call with a unique name the table reader can record. (masked, calls)."""
    calls: List[Tuple[str, Optional[List[str]]]] = []

    def replace(match: "re.Match[str]") -> str:
        calls.append((match.group(1), _literal_args(text[match.start(2):match.end(2)])))
        return f"__dbt_call_{len(calls) - 1}__" + "\n" * match.group().count("\n")

    return _DBT_CALL.sub(replace, masked), calls


def _jinja_refs(text: str) -> List[dict]:
    """[{"ref", "package", "line", "quote", "dynamic"}] for each ref() call in Jinja, first per line and name."""
    lines = text.split("\n")
    found: Dict[Tuple[int, str, Optional[str]], dict] = {}
    for span in _JINJA_SPAN.finditer(text):
        if span.group().startswith("{#"):
            continue
        body = span.group()
        for opening in _REF_OPEN.finditer(body):
            call = _REF_CALL.match(body, opening.start())
            args = _literal_args(call.group(1)) if call else None
            dynamic = args is None or not 1 <= len(args) <= 2
            line = text.count("\n", 0, span.start() + opening.start()) + 1
            name = "" if dynamic else args[-1]
            package = None if dynamic or len(args) == 1 else args[0]
            quote = lines[line - 1].strip()[:MAX_QUOTE_CHARS] if line <= len(lines) else ""
            version = _VERSION_ARG.search(call.group(1)) if call else None
            found.setdefault((line, name, package), {"ref": name, "package": package, "line": line, "quote": quote,
                                                     "dynamic": dynamic, **({"version": version.group(1)} if version else {})})
    return list(found.values())


def _placeholders(masked: str) -> str:
    """Each run-time value becomes `{}`; newlines inside it are kept after it."""
    return _PLACEHOLDER.sub(lambda match: "{}" + "\n" * match.group().count("\n"), masked)


def _name(token: str) -> Optional[str]:
    parts = [part.strip().strip('`"').lower() for part in token.split(".")]
    if not 1 <= len(parts) <= MAX_NAME_PARTS or not all(parts) or parts[0][0].isdigit():
        return None
    if any(char in part for part in parts for char in "/:"):  # a path or URI in backticks, not a table
        return None
    return ".".join(parts)


def tables(path: str, text: str) -> dict:
    """{"tables": [{"table", "access", "line", "quote", "dynamic", "temp"}],
    "dbt_refs": [{"ref", "package", "line", "quote", "dynamic"}], "degraded": bool}.

    A table read through `{{ source('src', 'table') }}` is the table `src.table`."""
    masked, unterminated = mask(text)
    masked, calls = _dbt_calls(text, masked)
    source = _placeholders(masked)
    lines = text.split("\n")  # line numbers count "\n" only, so the quote must come from the same split
    newlines = [match.start() for match in re.finditer("\n", source)]
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
        if name is None or (access == "read" and name in ctes):
            return
        line = bisect_left(newlines, tokens[index][1]) + 1
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
            if index >= 2 and words[index - 1] == "AS":
                declared = index - 2
                if words[declared] == ")":  # WITH name (col, ...) AS (
                    while declared >= 0 and words[declared] != "(":
                        declared -= 1
                    declared -= 1
                if declared >= 1 and words[declared - 1] in ("WITH", "RECURSIVE", ","):
                    ctes.add(_name(tokens[declared][0]) or "")
        elif word == ")":
            if len(selects) > 1:
                selects.pop()
        elif word == ";":
            merging = False
            ctes.clear()
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
    tables_found, written_refs = _apply_dbt_calls(found, calls)
    refs = [ref for ref in (_jinja_refs(text) if "ref" in text else [])
            if (ref["line"], ref["ref"]) not in written_refs]
    return {"tables": sorted(tables_found, key=lambda item: (item["line"], item["access"], item["table"])),
            "dbt_refs": sorted(refs, key=lambda item: (item["line"], item["ref"])), "degraded": unterminated}


def _apply_dbt_calls(found: Dict[Tuple[str, str], dict], calls) -> Tuple[List[dict], Set[Tuple[int, str]]]:
    """Drop reads of `ref` calls (dbt_refs covers them) and turn `source` calls into table reads.

    A dbt call written to (`insert into {{ ref('t') }}`) stays a run-time write, as any
    other template in a write position does; the (line, name) of such a ref is
    returned so it is not also counted as a read."""
    tables_found: Dict[Tuple[str, str], dict] = {}
    written_refs: Set[Tuple[int, str]] = set()
    for item in found.values():
        match = _DBT_TOKEN.match(item["table"])
        if match is None:
            tables_found.setdefault((item["table"], item["access"]), item)
            continue
        kind, args = calls[int(match.group(1))]
        if item["access"] != "read":
            if kind == "ref" and args:
                written_refs.add((item["line"], args[-1]))
            tables_found.setdefault(("{}", item["access"]), {**item, "table": "{}", "dynamic": True})
            continue
        if kind == "ref":
            continue
        if args is not None and len(args) == 2 and all(args):
            name = f"{args[0]}.{args[1]}".lower()
            tables_found.setdefault((name, "read"), {**item, "table": name, "dynamic": False})
        else:
            tables_found.setdefault(("{}", "read"), {**item, "table": "{}", "dynamic": True})
    return list(tables_found.values()), written_refs
