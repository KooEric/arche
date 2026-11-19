import hashlib

import pytest

from arche_lib.errors import IntegrityError
from arche_lib.tour import validate_tour


def _fixture(text="one\ntwo\n"):
    digest = hashlib.sha256(text.encode()).hexdigest()
    inventory = {"files": [{"path": "src/main.py", "sha256": digest}]}
    tour = {
        "version": 1,
        "steps": [{"id": "main", "path": "src/main.py", "start_line": 1,
                    "end_line": 2, "sha256": digest}],
        "edges": [],
    }
    return tour, inventory, {"src/main.py": text}


def test_validates_steps_and_edges():
    tour, inventory, source = _fixture()
    tour["steps"].append({**tour["steps"][0], "id": "other"})
    tour["edges"] = [{"from": "main", "to": "other", "kind": "reads"}]
    validate_tour(tour, inventory, source.get)


@pytest.mark.parametrize("change", [
    lambda t: t.update(version=2),
    lambda t: t.update(version=1.0),
    lambda t: t["steps"][0].update(path="missing.py"),
    lambda t: t["steps"][0].update(start_line=0),
    lambda t: t["steps"][0].update(end_line=3),
    lambda t: t["edges"].append({"from": "main", "to": "missing", "kind": "calls"}),
    lambda t: t["edges"].append({"from": "main", "to": "main", "kind": "imports"}),
])
def test_rejects_invalid_tours(change):
    tour, inventory, source = _fixture()
    change(tour)
    with pytest.raises(IntegrityError):
        validate_tour(tour, inventory, source.get)


def test_rejects_stale_source_hash():
    tour, inventory, _ = _fixture()
    with pytest.raises(IntegrityError, match="stale SHA256"):
        validate_tour(tour, inventory, lambda _: "changed\n")


def test_rejects_skipped_inventory_entry_before_reading():
    tour, inventory, source = _fixture()
    inventory["files"][0]["status"] = "skipped"
    inventory["files"][0]["reason"] = "secret"

    def sentinel(_):
        raise AssertionError("read_path must not be called for skipped files")

    with pytest.raises(IntegrityError, match="explicitly skipped"):
        validate_tour(tour, inventory, sentinel)


def test_screen_items_reference_validated_evidence():
    from arche_lib.report import _screens
    tour, inventory, sources = _fixture()
    item = {"label": "<Retry>", "behavior": "Reload", "step": "main"}
    tour["screens"] = [{"title": "Home", "items": [item]}]
    validate_tour(tour, inventory, sources.get)
    html = _screens(tour, {"file:src/main.py": "file-0"})
    assert "&lt;Retry&gt;" in html and 'href="#file-0"' in html
    item["step"] = "missing"
    with pytest.raises(IntegrityError, match="unknown step"):
        validate_tour(tour, inventory, sources.get)
