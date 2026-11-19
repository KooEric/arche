import json
import subprocess
import sys
from pathlib import Path

import pytest

import harness_score


EDGE = {"from": "a", "to": "b", "kind": "calls"}
GOLDEN = {"cases": [{"id": "one", "required_steps": ["a", "b"], "expected_edges": [EDGE]}]}


def test_rescore_preserves_trace_and_unknown_measurements():
    run = {"case_id": "one", "arm": "on", "steps": [{"id": "a"}, {"id": "a"}],
           "edges": [EDGE, EDGE, {"from": "b", "to": "a", "kind": "calls"}],
           "trace": {"source": "recorded"}}
    result = harness_score.score_runs(GOLDEN, [run])["results"][0]
    assert result["score"]["required_step_recall"] == .5
    assert result["score"]["missing_steps"] == ["b"]
    assert result["score"]["extra_edges"] == [{"from": "b", "to": "a", "kind": "calls"}]
    assert result["trace"] == run["trace"]
    assert result["cost"] is None and result["citation_accuracy"] is None
    assert harness_score.score_runs(GOLDEN, [run]) == harness_score.score_runs(GOLDEN, [run])


def test_failed_execution_is_not_a_quality_failure():
    result = harness_score.score_runs(GOLDEN, [{"case_id": "one", "status": "api_error"}])["results"][0]
    assert result["scoring_status"] == "execution_error" and result["score"] is None
    converted = harness_score.to_harness({"results": [result]})[0]
    assert converted["scores"] is None and converted["metadata"]["status"] == "api_error"


def test_bad_cases_and_runs_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        harness_score.score_runs({"cases": GOLDEN["cases"] * 2}, [])
    with pytest.raises(ValueError, match="unknown"):
        harness_score.score_runs(GOLDEN, [{"case_id": "missing"}])
    with pytest.raises((ValueError, KeyError)):
        harness_score.score_runs(GOLDEN, [{"case_id": "one", "steps": [], "edges": [{}]}])


def test_cli_rescores_jsonl_and_converts(tmp_path):
    golden = tmp_path / "golden.json"
    runs = tmp_path / "runs.jsonl"
    golden.write_text(json.dumps(GOLDEN))
    runs.write_text(json.dumps({"case_id": "one", "steps": [{"id": "a"}, {"id": "b"}], "edges": [EDGE]}) + "\n")
    cmd = [sys.executable, str(Path(harness_score.__file__)), str(golden), str(runs)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["results"][0]["score"]["expected_edge_recall"] == 1
    result = subprocess.run(cmd + ["--harness"], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[0]["input"]["case_id"] == "one"
    runs.write_text('{bad}\n')
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 2 and "line 1" in result.stderr
