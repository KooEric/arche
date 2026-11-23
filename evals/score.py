#!/usr/bin/env python3
"""Score the deterministic layer against the hand-labelled fixtures.

Precision and recall count `verified` IMPORTS edges only. A true edge the script
can only infer counts as not verified, so it lowers recall. `implicit` edges are
true dependencies with no import statement behind them (Java and Kotlin types in
the same package). They must be reported as inferred and stay out of recall.
REFERENCES edges (a SQL file path written as text) are compared exactly through the
`references` list and never count toward precision or recall.
READS edges (a SQL file reading a table another SQL file writes) and external tables are
compared exactly through the `reads` and `external_tables` lists, also outside precision and recall.

Usage: python3 evals/score.py [--min-precision 0.98] [--min-recall 0.95]
Exits 1 when a gate fails or a fixture does not match its expected sets.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(EVALS_DIR.parent / "skills" / "arche" / "scripts"))

from arche_lib.graph import build_graph  # noqa: E402
from arche_lib.inventory import build_inventory  # noqa: E402
from arche_lib.langs import language_for  # noqa: E402
from arche_lib.sources import make_reader  # noqa: E402

FIXTURES_DIR = EVALS_DIR / "fixtures"
EXPECTED_FILE = "expected_graph.json"
DEFAULT_MIN_PRECISION = 0.98
DEFAULT_MIN_RECALL = 0.95
EXACT_SETS = ("analyzed", "skipped", "inferred", "unresolved", "externals", "symbols", "http", "references",
              "reads", "external_tables")
OUTCOMES = ("true_positive", "false_positive", "false_negative")


def run_pipeline(repo_dir):
    """Copy the fixture outside any git work tree, then scan it and build its graph."""
    with tempfile.TemporaryDirectory() as temp:
        work = Path(os.path.realpath(temp)) / "repo"
        shutil.copytree(repo_dir, work, symlinks=True)
        inventory = build_inventory(work)
        if inventory["repo"]["commit"] is not None:
            raise RuntimeError("fixtures must be scanned outside a git work tree")
        return build_graph(inventory, make_reader(inventory))


def _path(node_id):
    return node_id.split(":", 1)[1]


def _reference_key(edge):
    """(from, resolved path or written spec, status, rule or reason)."""
    target = _path(edge["to"]) if edge["to"] else edge["spec"]
    return (_path(edge["from"]), target, edge["status"], edge.get("rule") or edge["reason"])


def _read_key(edge):
    """(from, writing file or written name, table, status, rule or reason)."""
    target = _path(edge["to"]) if edge["to"] else edge["spec"]
    return (_path(edge["from"]), target, edge["table"], edge["status"], edge.get("rule") or edge["reason"])


def observed_sets(graph):
    edges = [e for e in graph["edges"] if e["kind"] == "IMPORTS"]
    return {
        "analyzed": {_path(n["id"]) for n in graph["nodes"] if n["type"] == "file"},
        "skipped": {(s["path"], s["reason"]) for s in graph["skipped"]},
        "imports": {(_path(e["from"]), _path(e["to"])) for e in edges if e["status"] == "verified"},
        "inferred": {(_path(e["from"]), _path(e["to"])) for e in edges if e["status"] == "inferred"},
        "unresolved": {(_path(e["from"]), e["spec"]) for e in edges if e["status"] == "unresolved"},
        "externals": {(_path(x["from"]), x["package"]) for x in graph["externals"]},
        "symbols": {n["id"] for n in graph["nodes"] if n["type"] == "symbol"},
        "http": {_http_key(c) for c in graph.get("http_connections", [])},
        "references": {_reference_key(e) for e in graph["edges"] if e["kind"] == "REFERENCES"},
        "reads": {_read_key(e) for e in graph["edges"] if e["kind"] == "READS"},
        "external_tables": {(_path(x["from"]), x["table"]) for x in graph.get("external_tables", [])},
    }


def _http_key(connection):
    """(request file, line, method, path, reason, sorted route file:line targets)."""
    request = connection["request"]
    targets = tuple(sorted("%s:%d" % (r["file"], r["line"]) for r in connection["candidates"]))
    return (request["file"], request["line"], request["method"], request["path"],
            connection["reason"], targets)


def expected_sets(expected):
    """`imports` are edges the script must verify; `inferred` are true edges it may only infer."""
    return {
        "analyzed": set(expected["analyzed"]),
        "skipped": {(s["path"], s["reason"]) for s in expected["skipped"]},
        "imports": {(e["from"], e["to"]) for e in expected["imports"]},
        "inferred": {(e["from"], e["to"]) for e in expected["inferred"]},
        "implicit": {(e["from"], e["to"]) for e in expected.get("implicit", [])},
        "unresolved": {(u["from"], u["spec"]) for u in expected["unresolved"]},
        "externals": {(x["from"], x["package"]) for x in expected["externals"]},
        "symbols": set(expected["symbols"]),
        "http": {(h["file"], h["line"], h["method"], h["path"], h["reason"], tuple(sorted(h["routes"])))
                 for h in expected.get("http", [])},
        "references": {(r["from"], r["target"], r["status"], r["how"]) for r in expected.get("references", [])},
        "reads": {(r["from"], r["target"], r["table"], r["status"], r["how"]) for r in expected.get("reads", [])},
        "external_tables": {(x["from"], x["table"]) for x in expected.get("external_tables", [])},
    }


def score_fixture(fixture_dir):
    raw = json.loads((fixture_dir / EXPECTED_FILE).read_text(encoding="utf-8"))
    expected = expected_sets(raw)
    observed = observed_sets(run_pipeline(fixture_dir / "repo"))
    implicit = expected.pop("implicit")
    expected["inferred"] |= implicit
    mismatches = {
        name: {
            "missing": sorted(expected[name] - observed[name]),
            "extra": sorted(observed[name] - expected[name]),
        }
        for name in EXACT_SETS
        if expected[name] != observed[name]
    }
    known_edges = expected["imports"] | expected["inferred"]
    true_edges = known_edges - implicit
    return {
        "fixture": fixture_dir.name,
        "true_positive": sorted(observed["imports"] & true_edges),
        "false_positive": sorted(observed["imports"] - known_edges),
        "false_negative": sorted(true_edges - observed["imports"]),
        "inferred_correct": sorted(observed["inferred"] & known_edges),
        "inferred_wrong": sorted(observed["inferred"] - known_edges),
        "mismatches": mismatches,
    }


def score_all():
    fixtures = sorted(d for d in FIXTURES_DIR.iterdir() if (d / EXPECTED_FILE).is_file())
    return [score_fixture(fixture) for fixture in fixtures]


def _ratio(numerator, denominator):
    return 1.0 if denominator == 0 else numerator / denominator


def _metrics(tp, fp, fn):
    return {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn)}


def totals(results):
    counts = [sum(len(result[outcome]) for result in results) for outcome in OUTCOMES]
    return _metrics(*counts)


def by_language(results):
    """Group edges by the importing file's language."""
    langs = sorted(
        {language_for(edge[0]) or "other" for r in results for outcome in OUTCOMES for edge in r[outcome]}
    )
    return {
        lang: _metrics(
            *[
                sum(1 for r in results for edge in r[outcome] if (language_for(edge[0]) or "other") == lang)
                for outcome in OUTCOMES
            ]
        )
        for lang in langs
    }


def _report(results, overall, min_precision, min_recall):
    lines = ["%-16s %4s %4s %4s" % ("fixture", "tp", "fp", "fn")]
    for result in results:
        counts = tuple(len(result[outcome]) for outcome in OUTCOMES)
        lines.append("%-16s %4d %4d %4d" % ((result["fixture"],) + counts))
        for name, diff in sorted(result["mismatches"].items()):
            lines.append("  MISMATCH %s: missing=%s extra=%s" % (name, diff["missing"], diff["extra"]))
        for edge in result["false_positive"]:
            lines.append("  FALSE POSITIVE %s -> %s" % edge)
        for edge in result["false_negative"]:
            note = " (inferred)" if edge in result["inferred_correct"] else ""
            lines.append("  NOT VERIFIED %s -> %s%s" % (edge + (note,)))
        for edge in result["inferred_wrong"]:
            lines.append("  WRONG INFERENCE %s -> %s" % edge)
    for lang, metric in by_language(results).items():
        lines.append("language %-8s precision %.3f recall %.3f" % (lang, metric["precision"], metric["recall"]))
    inferred = [sum(len(r[key]) for r in results) for key in ("inferred_correct", "inferred_wrong")]
    lines.append("inferred edges: %d correct, %d wrong" % tuple(inferred))
    lines.append(
        "overall precision %.3f recall %.3f (gates %.2f / %.2f)"
        % (overall["precision"], overall["recall"], min_precision, min_recall)
    )
    return "\n".join(lines)


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-precision", type=float, default=DEFAULT_MIN_PRECISION)
    parser.add_argument("--min-recall", type=float, default=DEFAULT_MIN_RECALL)
    args = parser.parse_args(argv)
    results = score_all()
    overall = totals(results)
    print(_report(results, overall, args.min_precision, args.min_recall))
    failed = (
        overall["precision"] < args.min_precision
        or overall["recall"] < args.min_recall
        or any(result["mismatches"] for result in results)
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
