"""Conservative Java and Kotlin review candidates. A name mentioned anywhere else is never reported."""
import re
from collections import Counter

from .langs.jvm import _JAVA, _KOTLIN, _is_kotlin
from .lexer import line_of, mask

_WORD = re.compile(r"[A-Za-z_$][\w$]*")
_PLAIN_WORD = re.compile(r"[A-Za-z_]\w*")
_MODIFIERS = r"(?:(?:static|final|abstract|synchronized|native|transient|volatile|strictfp|open|inline|const|lateinit|suspend|operator|infix|tailrec|override|inner|data|sealed|enum|value)\s+)*"
_JAVA_MEMBER = re.compile(rf"(?m)^([ \t]*)private\s+{_MODIFIERS}(?:<[^>\n]+>\s+)?(?:class\s+|interface\s+|enum\s+|record\s+|[\w$.<>\[\],?]+(?:\s*<[^;={{\n]*>)?(?:\[\])*\s+)([A-Za-z_$][\w$]*)\s*(?=[(=;{{<]|extends\b|implements\b)")
_KOTLIN_MEMBER = re.compile(rf"(?m)^([ \t]*)private\s+{_MODIFIERS}(?:fun\s+(?:<[^>\n]+>\s*)?(?:[\w<>?,.\s]*\.)?|val\s+|var\s+|class\s+|object\s+|interface\s+|typealias\s+)([A-Za-z_$][\w$]*)")
# Serialization calls these by name, so no reference appears in source.
_HOOKS = frozenset({"serialVersionUID", "readObject", "writeObject", "readResolve", "writeReplace", "readObjectNoData", "serialPersistentFields"})
_TEXT_CONFIG = (".xml", ".properties", ".yml", ".yaml", ".json", ".gradle", ".kts", ".toml", ".pro", ".txt", ".factories", ".imports")
_TYPE_KINDS = frozenset({"class", "interface", "enum", "typedef"})
_ANNOTATION_LINE = re.compile(r"(?:@[\w.:]+(?:\([^\n]*\))?\s*)+")
MAX_CONFIG_BYTES = 200_000


def _protected(path):
    parts = path.lower().split("/")
    return any(part in {"test", "tests", "androidtest", "testfixtures", "generated", "fixtures", "examples", "samples"} for part in parts)


def _annotated(text, start):
    """True when an annotation sits on the declaration's line or on the lines directly above it."""
    line_start = text.rfind("\n", 0, start) + 1
    if "@" in text[line_start:text.find("\n", start) if "\n" in text[start:] else len(text)].split("private", 1)[0]:
        return True
    while line_start > 0:
        previous = text.rfind("\n", 0, line_start - 1) + 1
        above = text[previous:line_start].strip()
        if _ANNOTATION_LINE.fullmatch(above):
            return True
        if above:  # any other code ends the run of annotations
            return False
        line_start = previous
    return False


def build_jvm_audit(graph, inventory, read_path):
    files = sorted(node["id"][5:] for node in graph["nodes"] if node["type"] == "file" and node.get("lang") in ("java", "kotlin"))
    if not files:
        return []
    masked, everywhere, in_strings = {}, Counter(), set()
    for path in files:
        text = read_path(path)
        if text is None:
            continue
        item = mask(text, _KOTLIN if _is_kotlin(path) else _JAVA)
        masked[path] = (item, Counter(_WORD.findall(item.text)))
        everywhere.update(masked[path][1])
        for literal in item.strings.values():
            in_strings.update(_PLAIN_WORD.findall(literal))  # also reaches Kotlin "$name" templates
    # Manifests, DI and build files name classes as text.
    for entry in inventory["files"]:
        if entry["path"] in masked or not entry["path"].lower().endswith(_TEXT_CONFIG) or entry.get("bytes", 0) > MAX_CONFIG_BYTES:
            continue
        try:
            in_strings.update(_PLAIN_WORD.findall(read_path(entry["path"]) or ""))
        except Exception:  # an unreadable side file only makes the audit more cautious elsewhere
            continue

    findings = []
    for path, (item, words) in masked.items():
        if _protected(path):
            continue
        pattern = _KOTLIN_MEMBER if _is_kotlin(path) else _JAVA_MEMBER
        for match in pattern.finditer(item.text):
            name = match.group(2)
            if name in _HOOKS or words[name] != 1 or name in in_strings or _annotated(item.text, match.start()):
                continue
            findings.append({"rule": "unused-private-member", "path": path, "line": line_of(item, match.start(2)), "name": name})

    for node in graph["nodes"]:
        if node["type"] != "symbol" or node.get("exported") or node.get("kind") not in _TYPE_KINDS:
            continue
        path, name = node["id"][4:].rsplit("#", 1)
        if path not in masked or _protected(path) or everywhere[name] != 1 or name in in_strings:
            continue
        item = masked[path][0]
        offset = item.line_starts[node["line"] - 1]
        if _annotated(item.text, offset + (len(item.text[offset:]) - len(item.text[offset:].lstrip()))) or "private " in item.text[offset:item.text.find("\n", offset)]:
            continue  # annotated, or already covered by the private-member rule
        findings.append({"rule": "unused-nonpublic-type", "path": path, "line": node["line"], "name": name})
    return sorted(findings, key=lambda f: (f["rule"], f["path"], f["line"]))
