import json
import subprocess
import sys
from pathlib import Path

import pytest

import score_tour


def edge(source, target, kind="calls"):
    return {"from": source, "to": target, "kind": kind}


# Synthetic labels only: these are not claims about any field repository.
CASE = {
    "required_steps": ["cli", "read_project", "select_stage", "run_stage"],
    "expected_edges": [edge("cli", "read_project"), edge("cli", "select_stage"),
                       edge("select_stage", "run_stage")],
}


def tour(ids, edges):
    return {"steps": [{"id": name} for name in ids], "edges": edges}


def test_complete_and_optional_step_omitted():
    result = score_tour.score_tour(CASE, tour(CASE["required_steps"], CASE["expected_edges"]))
    assert result == {"required_step_recall": 1, "expected_edge_recall": 1,
                      "missing_steps": [], "missing_edges": [], "extra_edges": []}


def test_missing_and_wrong_kind_or_direction_are_reported():
    result = score_tour.score_tour(CASE, tour(["cli", "read_project", "select_stage"], [
        edge("cli", "read_project"), edge("select_stage", "cli"),
        edge("select_stage", "run_stage", "reads"),
    ]))
    assert result["required_step_recall"] == 0.75
    assert result["expected_edge_recall"] == pytest.approx(1 / 3)
    assert result["missing_steps"] == ["run_stage"]
    assert result["missing_edges"] == [edge("cli", "select_stage"), edge("select_stage", "run_stage")]
    assert result["extra_edges"] == [edge("select_stage", "cli"), edge("select_stage", "run_stage", "reads")]


def test_duplicate_observations_do_not_inflate_coverage():
    result = score_tour.score_tour(CASE, tour(["cli"] * 10, [edge("cli", "read_project")] * 10))
    assert result["required_step_recall"] == 0.25
    assert result["expected_edge_recall"] == pytest.approx(1 / 3)
    assert len(result["missing_edges"]) == 2


def test_alternative_path_is_a_separate_case():
    alternative = {"required_steps": ["cli", "read_project", "default_stage", "run_stage"],
                   "expected_edges": [edge("cli", "read_project"), edge("cli", "default_stage"),
                                      edge("default_stage", "run_stage")]}
    observed = tour(alternative["required_steps"], alternative["expected_edges"])
    assert score_tour.score_tour(alternative, observed)["missing_steps"] == []
    assert score_tour.score_tour(CASE, observed)["missing_steps"] == ["select_stage"]
    assert len(score_tour.score_tour(CASE, observed)["extra_edges"]) == 2


def test_empty_and_malformed_inputs():
    assert score_tour.score_tour({"required_steps": [], "expected_edges": []}, tour([], []))["required_step_recall"] == 1
    with pytest.raises(ValueError):
        score_tour.score_tour(CASE, tour([None], []))
    with pytest.raises(ValueError):
        score_tour.score_tour(CASE, tour([], [{"from": "a", "to": "b"}]))


def test_cli_reports_exit_codes(tmp_path):
    case = tmp_path / "case.json"
    output = tmp_path / "tour.json"
    case.write_text(json.dumps(CASE))
    output.write_text(json.dumps(tour(CASE["required_steps"], CASE["expected_edges"])))
    script = Path(score_tour.__file__)
    run = lambda: subprocess.run([sys.executable, str(script), str(case), str(output)], capture_output=True, text=True)
    assert run().returncode == 0
    output.write_text(json.dumps(tour([], [])))
    failed = run()
    assert failed.returncode == 1
    assert json.loads(failed.stdout)["missing_steps"] == sorted(CASE["required_steps"])
    output.write_text("invalid")
    assert run().returncode == 2
