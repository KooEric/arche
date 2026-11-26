#!/usr/bin/env python3
"""Score column lineage against the hand-labelled column-lineage fixture.

Gates the precision and recall of `verified` column edges (an edge the answer marks
inferred and the script calls verified counts as a false positive), and compares the
inferred edges, unresolved references, statement states, disagreements and the column
impact of a two-commit change exactly. Exits 1 on any failure.

Usage: python3 evals/score_columns.py [--min-precision 0.98] [--min-recall 0.90]
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent / "skills" / "arche" / "scripts"))

import changes  # noqa: E402
import imports  # noqa: E402
import scan  # noqa: E402

FIXTURE = EVALS_DIR / "fixtures" / "column-lineage"
_GIT_ENV = {"GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
            "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _key(edge: dict) -> tuple:
    return (edge["from"], edge["to"], edge["kind"]) + ((edge["role"],) if edge.get("role") else ())


def _rows(items) -> list:
    return sorted((list(item) for item in items), key=lambda row: [str(part) for part in row])


def observed(columns: dict) -> dict:
    """The answer file's view of a columns.json document."""
    return {
        "verified": _rows(_key(e) for e in columns["edges"] if e["status"] == "verified"),
        "inferred": _rows(_key(e) + (e["rule"],) for e in columns["edges"] if e["status"] == "inferred"),
        "unresolved": _rows([u["to"], u["reason"], u["spec"]] + ([u["candidates"]] if u.get("candidates") else [])
                            for u in columns["unresolved"]),
        "statements": [[s["path"], s["index"], s["status"], s["reason"]] for s in columns["statements"]],
        "column_sources_disagree": columns["column_sources_disagree"],
    }


def gate(got: list, expected: list, min_precision: float, min_recall: float) -> tuple:
    """(precision, recall, passed) of the verified edges."""
    found, wanted = {tuple(row) for row in got}, {tuple(row) for row in expected}
    hits = len(found & wanted)
    precision = hits / len(found) if found else 1.0
    recall = hits / len(wanted) if wanted else 1.0
    return precision, recall, precision >= min_precision and recall >= min_recall


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True,
                            env={**os.environ, **_GIT_ENV})
    return result.stdout.strip()


def _snapshot(repo: Path, out: Path) -> None:
    scan.main([str(repo), "--out", str(out)])
    imports.main(["--out", str(out)])


def run_fixture(work: Path) -> dict:
    """columns.json of the fixture repository, scanned outside any git work tree."""
    shutil.copytree(FIXTURE / "repo", work / "plain")
    _snapshot(work / "plain", work / "plain-out")
    return json.loads((work / "plain-out" / "columns.json").read_text(encoding="utf-8"))


def run_change(work: Path) -> dict:
    """column_impact for the fixture's base -> head change (head/ overlays repo/)."""
    repo = work / "git"
    shutil.copytree(FIXTURE / "repo", repo)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _snapshot(repo, work / "base-out")
    shutil.copytree(FIXTURE / "head", repo, dirs_exist_ok=True)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "head")
    head = _git(repo, "rev-parse", "HEAD")
    _snapshot(repo, work / "head-out")
    changes.main(["--out", str(work / "head-out"), "--base", base, "--head", head,
                  "--base-out", str(work / "base-out")])
    return json.loads((work / "head-out" / "changes.json").read_text(encoding="utf-8"))["column_impact"]


def impact_view(impact: dict) -> dict:
    return {
        "seeds": _rows([s["column"], s["change"]] for s in impact["seeds"]),
        "affected": _rows([a["column"], a["depth"], a["status"]] for a in impact["affected"]),
        "used_by": _rows([u["table"], u["status"]] for u in impact["used_by"]),
        "affected_files": impact["affected_files"],
        "affected_tables": impact["affected_tables"],
        "truncated": impact["truncated"],
    }


def differences(got: dict, expected: dict) -> list:
    found = []
    for key, value in got.items():
        if key == "verified" or value == expected.get(key):
            continue
        if not isinstance(value, list) or not isinstance(expected.get(key), list):
            found.append(f"{key}: expected {expected.get(key)!r}, got {value!r}")
        else:
            missing = [row for row in expected.get(key, []) if row not in value]
            extra = [row for row in value if row not in expected.get(key, [])]
            found.append(f"{key}: missing {missing}, unexpected {extra}")
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-precision", type=float, default=0.98)
    parser.add_argument("--min-recall", type=float, default=0.90)
    args = parser.parse_args(argv)
    expected = json.loads((FIXTURE / "expected_columns.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp:
        work = Path(os.path.realpath(temp))
        got = observed(run_fixture(work))
        impact = impact_view(run_change(work))
    precision, recall, passed = gate(got["verified"], expected["verified"], args.min_precision, args.min_recall)
    found = differences(got, expected) + [f"column_impact: {line}" for line in
                                          differences(impact, expected["column_impact"])]
    if not passed:
        found.append(f"verified edges below the gate: precision {precision:.3f}, recall {recall:.3f}")
    for line in found:
        print(f"column-lineage: {line}")
    print(f"column-lineage: {'FAIL' if found else 'ok'} (verified precision {precision:.3f}, recall {recall:.3f}, "
          f"{len(got['verified'])} verified, {len(got['inferred'])} inferred)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
