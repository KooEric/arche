#!/usr/bin/env python3
"""Score the data profile against the hand-labelled data fixture.

Compares table matches, loose ends, column types, candidate keys and sensitive columns
exactly, and checks that planted personal values never reach profile.json, even with
--show-values. Exits 1 on any difference.

Usage: python3 evals/score_profile.py
"""
import csv
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


REQUIRED = ("matches", "unresolved", "orphan_datasets", "tables_without_data", "datasets", "columns", "keys", "sensitive")
PLANTED_SOURCE = "exports/customers_extract.csv"


def planted_values(fixture: Path, sensitive: list) -> list:
    """Every cell of the sensitive columns in the fixture's extract: none may reach profile.json."""
    with open(fixture / "data" / PLANTED_SOURCE, newline="", encoding="utf-8") as handle:
        return [row[name] for row in csv.DictReader(handle) for name in sensitive if row.get(name)]


def differences(text: str, profile: dict, expected: dict) -> list:
    missing = [key for key in REQUIRED if key not in expected]
    if missing:
        return [f"expected_profile.json is missing keys: {missing}"]
    found = []
    observed = {
        "matches": [[m["table"], m["dataset"], m["status"], m["rule"]] for m in profile["matches"]],
        "unresolved": [[u["dataset"], u["reason"]] for u in profile["unresolved"]],
        "orphan_datasets": profile["orphan_datasets"],
        "tables_without_data": profile["tables_without_data"],
        "datasets": sorted(item["id"] for item in profile["datasets"]),
    }
    for key, value in observed.items():
        if value != expected[key]:
            found.append(f"{key}: expected {expected[key]}, got {value}")
    for item in sorted(profile["datasets"], key=lambda d: d["id"]):
        dataset = item["id"]
        got = {column["name"]: column["type"] for column in item["columns"]}
        if got != expected["columns"].get(dataset):
            found.append(f"{dataset} column types: expected {expected['columns'].get(dataset)}, got {got}")
        for label, field in (("keys", "candidate_key"), ("sensitive", "sensitive")):
            names = [column["name"] for column in item["columns"] if column.get(field)]
            if names != expected[label].get(dataset, []):
                found.append(f"{dataset} {label}: expected {expected[label].get(dataset, [])}, got {names}")
    sensitive = expected["sensitive"].get(f"d0:{PLANTED_SOURCE}", [])
    found.extend(f"planted value reached profile.json: {value}" for value in planted_values(FIXTURE, sensitive) if value in text)
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
