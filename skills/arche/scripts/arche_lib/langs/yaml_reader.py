"""Read the parts of YAML that hold paths: block mappings, block sequences and scalars.

`scalars(text)` returns `(documents, degraded)`. Each document is a list of
`(key path, value, line)` for every scalar value. A key path is a tuple of keys
and sequence positions, e.g. ("services", "web", "build", "context") or
("dependencies", 0, "repository"). Not read, by design:
  - block scalar contents (`|`, `>`), which are text, not structure;
  - anchors and tags, which are dropped from values; aliases (`*a`), which are skipped;
  - flow collections that span lines, and nested flow collections;
  - template lines (`{% ... %}`, or `{{` before a key), which mark the file degraded.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

Path = Tuple[object, ...]
Scalar = Tuple[Path, str, int]


def _strip_comment(line: str) -> str:
    """The line without a trailing `# comment`; `#` inside quotes or a word is kept."""
    quote: Optional[str] = None
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if quote == '"' and char == "\\":
                index += 1  # an escaped character never closes the string
            elif quote == "'" and char == "'" and line[index + 1:index + 2] == "'":
                index += 1  # '' is a quote inside a single-quoted string
            elif char == quote:
                quote = None
        elif char in "'\"" and (index == 0 or line[index - 1] in " \t:-[{,"):
            quote = char
        elif char == "#" and (index == 0 or line[index - 1] in " \t"):
            return line[:index].rstrip()
        index += 1
    return line.rstrip()


def _strip_properties(text: str) -> str:
    """The value without leading anchors (`&a`) and tags (`!Ref`); "" when nothing follows them."""
    while text[:1] in ("&", "!"):
        space = text.find(" ")
        text = "" if space < 0 else text[space + 1:].lstrip()
    return text


def _split_key(text: str) -> Optional[Tuple[str, str]]:
    """(key, rest) for `key: rest` or `key:`; None when the text is not a mapping entry."""
    if text[:1] in "'\"":
        end = text.find(text[0], 1)
        if end < 0 or not text[end + 1:].startswith(":"):
            return None
        after = text[end + 2:]
        if after and after[0] not in " \t":
            return None
        return text[1:end], after.strip()
    if text[:1] in "[{":
        return None
    for index, char in enumerate(text):
        if char == ":" and (index + 1 == len(text) or text[index + 1] in " \t"):
            key = text[:index].strip()
            return (key, text[index + 1:].strip()) if key else None
    return None


def _flow_items(body: str) -> List[str]:
    """Items of a one-line flow collection body, split at top-level commas."""
    items, depth, quote, start = [], 0, None, 0
    for index, char in enumerate(body):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(body[start:index].strip())
            start = index + 1
    tail = body[start:].strip()
    if tail:
        items.append(tail)
    return items


def _scalar(text: str) -> Optional[str]:
    """The value of a one-line scalar; None for an alias or an empty value."""
    while text[:1] in "!&":
        space = text.find(" ")
        if space < 0:
            return None
        text = text[space + 1:].strip()
    if not text or text.startswith("*"):
        return None
    if text[0] in "'\"" and len(text) >= 2 and text[-1] == text[0]:
        inner = text[1:-1]
        return inner.replace("''", "'") if text[0] == "'" else inner.replace('\\"', '"').replace("\\\\", "\\")
    return text


def _closes(text: str, quote: str) -> bool:
    """True when `text` holds the quote that ends a quoted scalar continued from earlier lines."""
    i = 0
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                i += 2
                continue
            return True
        i += 1
    return False


def _unclosed_quote(rest: str) -> Optional[str]:
    """The quote character when `rest` opens a quoted scalar that continues on the next line."""
    if rest[:1] not in ("'", '"'):
        return None
    return None if _closes(rest[1:], rest[0]) else rest[0]


def _emit_value(path: Path, rest: str, line: int, out: List[Scalar]) -> bool:
    """Emit a scalar or a one-line flow collection; False when `rest` starts a block scalar
    or an unfinished flow collection."""
    if rest[:1] in "|>":
        return False
    if rest[:1] == "[":
        if not rest.endswith("]"):
            return False
        for index, item in enumerate(_flow_items(rest[1:-1])):
            value = _scalar(item)
            if value is not None and item[:1] not in "[{":
                out.append((path + (index,), value, line))
        return True
    if rest[:1] == "{":
        if not rest.endswith("}"):
            return False
        for item in _flow_items(rest[1:-1]):
            pair = _split_key(item)
            if pair and pair[1][:1] not in "[{":
                value = _scalar(pair[1])
                if value is not None:
                    out.append((path + (pair[0],), value, line))
        return True
    value = _scalar(rest)
    if value is not None:
        out.append((path, value, line))
    return True


def scalars(text: str) -> Tuple[List[List[Scalar]], bool]:
    documents: List[List[Scalar]] = [[]]
    degraded = False
    # Stack of [indent, path, is_item, next index] for open containers; the first
    # entry is the document itself, so top-level sequence items are counted too.
    stack: List[list] = [[-1, (), False, 0]]
    skip_deeper_than: Optional[int] = None
    open_quote: Optional[str] = None  # a quoted scalar continues on this line
    for number, raw in enumerate(text.splitlines(), 1):
        if open_quote is not None:
            if _closes(raw, open_quote):
                open_quote = None
            continue
        if raw.startswith(("---", "...")) and raw[3:4] in ("", " ", "\t"):
            if documents[-1]:
                documents.append([])
            stack, skip_deeper_than = [[-1, (), False, 0]], None
            continue
        line = _strip_comment(raw)
        stripped = line.lstrip(" ")
        if not stripped:
            continue
        indent = len(line) - len(stripped)
        if skip_deeper_than is not None:
            if indent > skip_deeper_than:
                continue
            skip_deeper_than = None
        if stripped.startswith(("{%", "{{")) or stripped.startswith("%"):
            degraded = degraded or not stripped.startswith("%")
            continue
        out = documents[-1]
        is_item = stripped == "-" or stripped.startswith("- ")
        if is_item:
            while stack and (stack[-1][0] > indent or (stack[-1][0] == indent and stack[-1][2])):
                stack.pop()
        else:
            while stack and stack[-1][0] >= indent:
                stack.pop()
        parent = stack[-1]
        base: Path = parent[1]
        while is_item:
            # A sequence item: its position comes from the container that owns it.
            index = parent[3]
            parent[3] += 1
            item_path = base + (index,)
            rest = _strip_properties(stripped[1:].lstrip(" "))
            item_indent = indent
            column = indent + (len(stripped) - len(rest))
            stack.append([item_indent, item_path, True, 0])
            parent, base, stripped, indent = stack[-1], item_path, rest, column
            is_item = stripped == "-" or stripped.startswith("- ")
        if not stripped:
            continue
        pair = _split_key(stripped)
        if pair is None:
            if parent[2]:
                open_quote = _unclosed_quote(stripped)
                if open_quote is None and not _emit_value(base, stripped, number, out):
                    skip_deeper_than = indent - 1
                    degraded = degraded or stripped[:1] in "[{"
            continue  # a continuation line of a plain multi-line scalar
        key, rest = pair
        rest = _strip_properties(rest)  # `web: &web` or `Script: !Sub |` opens a block like `web:`
        if "{{" in key:
            degraded = True
            continue
        path = base + (key,)
        if not rest:
            stack.append([indent, path, False, 0])
            continue
        open_quote = _unclosed_quote(rest)
        if open_quote is None and not _emit_value(path, rest, number, out):
            skip_deeper_than = indent
            degraded = degraded or rest[:1] in "[{"
    return [document for document in documents if document], degraded
