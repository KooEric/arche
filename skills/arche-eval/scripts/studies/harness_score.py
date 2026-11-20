#!/usr/bin/env python3
"""Deterministically rescore recorded runs against labelled cases.

Usage: python3 evals/harness_score.py GOLDEN RUNS [--harness]

The optional Harness export is a JSONL-friendly list of per-run records, not a
network submission. Citation accuracy is unknown unless separately verified;
step and edge labels alone cannot establish that a citation is correct.
"""
import argparse
import json
from pathlib import Path

from score_tour import score_tour


def _load_cases(document):
    if not isinstance(document, dict) or not isinstance(document.get("cases"), list):
        raise ValueError("golden must contain a cases list")
    cases = {}
    for case in document["cases"]:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("each case requires a non-empty id")
        if case["id"] in cases:
            raise ValueError(f"duplicate case id: {case['id']}")
        # Validate even cases for which there are no runs.
        score_tour(case, {"steps": [], "edges": []})
        cases[case["id"]] = case
    return cases


def score_runs(golden, runs):
    cases = _load_cases(golden)
    results = []
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("each run must be an object")
        case_id = run.get("case_id")
        if case_id not in cases:
            raise ValueError(f"unknown case id: {case_id!r}")
        # Preserve metadata and trace for audit and subsequent rescoring. Missing
        # measurements remain unknown rather than becoming zero.
        result = dict(run)
        for key in ("arm", "model", "trial", "latency_seconds", "tokens", "cost"):
            result.setdefault(key, None)
        failed = run.get("status") in ("error", "failed", "timeout", "api_error") or bool(run.get("error"))
        result["score"] = None if failed else score_tour(cases[case_id], run)
        result["scoring_status"] = "execution_error" if failed else "scored"
        labels = cases[case_id].get("citation_lines")
        result["citation_accuracy"] = None
        if not failed and labels:
            matched = total = 0
            for step in run["steps"]:
                sources = labels.get(step["id"], {})
                for citation in step.get("citations", []):
                    total += 1
                    if isinstance(citation, dict) and type(citation.get("line")) is int:
                        matched += citation["line"] in sources.get(citation.get("path"), [])
            result["citation_matches"] = {"matched": matched, "total": total}
            result["citation_accuracy"] = matched / total if total else None
        if not failed and "expected_conditions" in cases[case_id]:
            def conditions(values):
                if not isinstance(values, list) or any(not isinstance(v, dict) or not isinstance(v.get("id"), str) or not isinstance(v.get("expression"), str) for v in values):
                    raise ValueError("conditions require id and expression strings")
                return {(v["id"], v["expression"]) for v in values}
            expected = conditions(cases[case_id]["expected_conditions"])
            observed = conditions(run.get("conditions", []))
            # The original prompt allowed conditions beside the cited step.
            for step in run["steps"]:
                observed.update(conditions(step.get("conditions", [])))
            result["score"]["condition_recall"] = len(expected & observed) / len(expected) if expected else 1.0
            result["score"]["missing_conditions"] = [dict(zip(("id", "expression"), x)) for x in sorted(expected - observed)]
            result["score"]["extra_conditions"] = [dict(zip(("id", "expression"), x)) for x in sorted(observed - expected)]
        if not failed and "forbidden_edges" in cases[case_id]:
            forbidden = {(e["from"], e["to"], e["kind"]) for e in cases[case_id]["forbidden_edges"]}
            observed = {(e["from"], e["to"], e["kind"]) for e in run["edges"]}
            result["score"]["forbidden_edges"] = [dict(zip(("from", "to", "kind"), x)) for x in sorted(forbidden & observed)]
        results.append(result)
    return {"results": results}


def to_harness(scored):
    """Convert results to portable input/output/metadata evaluation records."""
    records = []
    for result in scored["results"]:
        score = result["score"]
        records.append({
            "input": {"case_id": result["case_id"]},
            "output": {"steps": result.get("steps"), "edges": result.get("edges"), "conditions": result.get("conditions")},
            "metadata": {key: value for key, value in result.items()
                         if key not in ("steps", "edges", "score")},
            "scores": score,
        })
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("golden", type=Path)
    parser.add_argument("runs", type=Path)
    parser.add_argument("--harness", action="store_true", help="output portable Harness-style records")
    args = parser.parse_args()
    try:
        golden = json.loads(args.golden.read_text(encoding="utf-8"))
        runs = []
        for line_number, line in enumerate(args.runs.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    runs.append(json.loads(line, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f"invalid number {value}"))))
                except ValueError as exc:
                    raise ValueError(f"runs line {line_number}: {exc}") from exc
        result = score_runs(golden, runs)
        print(json.dumps(to_harness(result) if args.harness else result, ensure_ascii=False, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"invalid input: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
