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
