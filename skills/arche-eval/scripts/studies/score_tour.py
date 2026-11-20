#!/usr/bin/env python3
"""Compare a labelled case with a tour; evidence validity is a separate check.

Usage: python3 evals/score_tour.py CASE.json TOUR.json
Cases contain required_steps (IDs) and expected_edges ({from,to,kind}).
Unlisted steps are optional; unlisted edges are reported as extra. Represent
alternative paths as separate cases and score against the applicable one.
"""
import argparse
import json
from pathlib import Path


def _ids(values, label):
    if not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values):
        raise ValueError(f"{label} must be a list of non-empty strings")
    return set(values)


def _edges(values, label):
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a list")
    result = set()
    for edge in values:
        if not isinstance(edge, dict) or any(
            not isinstance(edge.get(key), str) or not edge[key]
            for key in ("from", "to", "kind")
        ):
            raise ValueError(f"{label} entries require non-empty from, to and kind strings")
        result.add((edge["from"], edge["to"], edge["kind"]))
    return result


def score_tour(case, tour):
    """Return deterministic set differences and coverage; duplicates count once."""
    required = _ids(case["required_steps"], "required_steps")
    expected = _edges(case["expected_edges"], "expected_edges")
    steps = tour["steps"]
    if not isinstance(steps, list) or any(not isinstance(s, dict) for s in steps):
        raise ValueError("steps must be a list of objects")
    observed = _ids([step.get("id") for step in steps], "step IDs")
    edges = _edges(tour["edges"], "edges")
    missing_steps = sorted(required - observed)
    missing_edges = sorted(expected - edges)
    extra_edges = sorted(edges - expected)
    as_records = lambda items: [dict(zip(("from", "to", "kind"), edge)) for edge in items]
    return {
        "required_step_recall": len(required & observed) / len(required) if required else 1.0,
        "expected_edge_recall": len(expected & edges) / len(expected) if expected else 1.0,
        "missing_steps": missing_steps,
        "missing_edges": as_records(missing_edges),
        "extra_edges": as_records(extra_edges),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", type=Path)
    parser.add_argument("tour", type=Path)
    args = parser.parse_args()
    try:
        case = json.loads(args.case.read_text(encoding="utf-8"))
        tour = json.loads(args.tour.read_text(encoding="utf-8"))
        result = score_tour(case, tour)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"invalid input: {exc}\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(bool(result["missing_steps"] or result["missing_edges"] or result["extra_edges"]))


if __name__ == "__main__":
    raise SystemExit(main())
