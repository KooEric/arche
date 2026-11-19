import copy
import hashlib
import json
from pathlib import Path

import pytest

import score_tour
from arche_lib.errors import IntegrityError
from arche_lib.tour import validate_tour

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "evals/fixtures/go-translator/repo"
TOURS = ROOT / "evals/tours/go-translator"


def load(name):
    return json.loads((TOURS / name).read_text())


def inventory(tour):
    paths = {step["path"] for step in tour["steps"]}
    return {"files": [{"path": p, "sha256": hashlib.sha256((REPO / p).read_bytes()).hexdigest()} for p in paths]}


@pytest.mark.parametrize("branch", ["present", "absent"])
def test_branch_evidence_and_golden(branch):
    tour = load(f"{branch}.json")
    validate_tour(tour, inventory(tour), lambda p: (REPO / p).read_bytes())
    result = score_tour.score_tour(load(f"{branch}-case.json"), tour)
    assert result["required_step_recall"] == result["expected_edge_recall"] == 1
    assert not result["missing_steps"] and not result["missing_edges"] and not result["extra_edges"]


def test_missing_step_and_cross_branch_call_are_reported():
    tour = load("present.json")
    case = load("present-case.json")
    tour["steps"] = [s for s in tour["steps"] if s["id"] != "uppercase"]
    tour["edges"] = [e for e in tour["edges"] if e["to"] != "uppercase"]
    result = score_tour.score_tour(case, tour)
    assert result["missing_steps"] == ["uppercase"]
    assert result["missing_edges"] == [{"from": "dispatch", "to": "uppercase", "kind": "calls"}]
    absent = load("absent.json")
    absent["steps"].append(copy.deepcopy(load("present.json")["steps"][-1]))
    wrong = {"from": "fallback", "to": "uppercase", "kind": "calls"}
    absent["edges"].append(wrong)
    validate_tour(absent, inventory(absent), lambda p: (REPO / p).read_bytes())
    assert score_tour.score_tour(load("absent-case.json"), absent)["extra_edges"] == [wrong]


def test_stale_evidence_is_rejected():
    tour = load("absent.json")
    with pytest.raises(IntegrityError, match="stale SHA256"):
        validate_tour(tour, inventory(tour), lambda p: (REPO / p).read_bytes() + b"changed")
