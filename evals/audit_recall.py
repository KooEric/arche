#!/usr/bin/env python3
"""Measure how much planted dead code the Java and Kotlin audit rules find in a real source tree.

Usage: python3 evals/audit_recall.py SOURCE_DIR [SOURCE_DIR ...]

Each tree is copied to a temporary directory. Every eligible file gets declarations
that nothing uses: a private field and a private method inside each top-level Java
class, a private top-level function in each Kotlin file, and one non-public type per
file. The audit then runs on the copy. Planted names are unique, so a miss means the
rule did not recognise the declaration in that file's surroundings, not that the name
was used. This measures recognition on real code, not how much dead code a project has.
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/arche/scripts"))
from arche_lib.audit_jvm import _protected, build_jvm_audit  # noqa: E402
from arche_lib.graph import build_graph  # noqa: E402
from arche_lib.inventory import build_inventory  # noqa: E402
from arche_lib.sources import make_reader  # noqa: E402

_JAVA_CLASS = re.compile(r"(?m)^(?:public\s+|final\s+|abstract\s+)*class\s+\w+")


def plant(root):
    planted = {"unused-private-member": set(), "unused-nonpublic-type": set()}
    number = 0
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.suffix not in (".java", ".kt") or not path.is_file() or _protected(rel):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        number += 1
        if path.suffix == ".java":
            end = text.rfind("}")
            if not _JAVA_CLASS.search(text) or end < 0:
                continue
            text = (text[:end] + f"\n  private int archeDead{number}Field;\n  private void archeDead{number}Method() {{}}\n}}\n"
                    + f"class ArcheDead{number}Type {{}}\n")
            planted["unused-private-member"] |= {(rel, f"archeDead{number}Field"), (rel, f"archeDead{number}Method")}
        else:
            text += f"\nprivate fun archeDead{number}Top() = 0\ninternal class ArcheDead{number}Type\n"
            planted["unused-private-member"].add((rel, f"archeDead{number}Top"))
        planted["unused-nonpublic-type"].add((rel, f"ArcheDead{number}Type"))
        path.write_text(text, encoding="utf-8")
    return planted


def measure(source):
    with tempfile.TemporaryDirectory() as temp:
        root = Path(os.path.realpath(temp)) / "repo"
        shutil.copytree(source, root, symlinks=True)
        planted = plant(root)
        inventory = build_inventory(root)
        reader = make_reader(inventory)
        findings = build_jvm_audit(build_graph(inventory, reader), inventory, reader)
    result = {"source": Path(source).name}
    for rule, expected in planted.items():
        found = {(f["path"], f["name"]) for f in findings if f["rule"] == rule}
        result[rule] = {"planted": len(expected), "found": len(expected & found),
                        "recall": round(len(expected & found) / len(expected), 3) if expected else None,
                        "missed_examples": sorted(expected - found)[:5], "other_candidates": len(found - expected)}
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    print(json.dumps([measure(source) for source in sys.argv[1:]], indent=2))
