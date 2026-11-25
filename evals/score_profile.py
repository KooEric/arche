#!/usr/bin/env python3
"""Score the data profile against the hand-labelled data fixture.

Compares table matches, loose ends, column types, candidate keys and sensitive columns
exactly, and checks that planted personal values never reach profile.json, even with
--show-values. Exits 1 on any difference.

Usage: python3 evals/score_profile.py
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent / "skills" / "arche" / "scripts"))

import data_profile  # noqa: E402
import imports  # noqa: E402
import scan  # noqa: E402

FIXTURE = EVALS_DIR / "fixtures" / "data-profile"


def run_fixture(fixture: Path) -> tuple:
    """(profile.json text, profile) for the fixture, scanned outside any git work tree."""
    with tempfile.TemporaryDirectory() as temp:
        work = Path(os.path.realpath(temp))
        shutil.copytree(fixture / "repo", work / "repo")
        shutil.copytree(fixture / "data", work / "data")
        out = work / "out"
        scan.main([str(work / "repo"), "--out", str(out)])
        imports.main(["--out", str(out)])
        data_profile.main(["--out", str(out), "--data", str(work / "data"),
                           "--map", str(fixture / "data-map.json"), "--show-values"])
        text = (out / "profile.json").read_text(encoding="utf-8")
    return text, json.loads(text)


def differences(text: str, profile: dict, expected: dict) -> list:
    found = []
    observed = {
        "matches": [[m["table"], m["dataset"], m["status"], m["rule"]] for m in profile["matches"]],
        "unresolved": [[u["dataset"], u["reason"]] for u in profile["unresolved"]],
        "orphan_datasets": profile["orphan_datasets"],
        "tables_without_data": profile["tables_without_data"],
    }
    for key, value in observed.items():
        if value != expected[key]:
            found.append(f"{key}: expected {expected[key]}, got {value}")
    datasets = {item["id"]: item for item in profile["datasets"]}
    for dataset, columns in expected["columns"].items():
        got = {column["name"]: column["type"] for column in datasets.get(dataset, {}).get("columns", [])}
        if got != columns:
            found.append(f"{dataset} column types: expected {columns}, got {got}")
    for label, field in (("keys", "candidate_key"), ("sensitive", "sensitive")):
        for dataset, names in expected[label].items():
            got = [column["name"] for column in datasets[dataset]["columns"] if column.get(field)]
            if got != names:
                found.append(f"{dataset} {label}: expected {names}, got {got}")
    found.extend(f"planted value reached profile.json: {value}" for value in expected["planted_values"] if value in text)
    return found


def main() -> int:
    expected = json.loads((FIXTURE / "expected_profile.json").read_text(encoding="utf-8"))
    text, profile = run_fixture(FIXTURE)
    found = differences(text, profile, expected)
    for line in found:
        print(f"data-profile: {line}")
    print(f"data-profile: {'FAIL' if found else 'ok'} ({len(profile['datasets'])} datasets, {len(profile['matches'])} matches)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
