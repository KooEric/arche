"""data_profile.py end to end, and arche.py --data."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from arche_lib.data.parquet_rows import find_reader
from parquet_footer import BINARY, I32, parquet_file


def _repo(make_repo):
    return make_repo({
        "sql/load.sql": "INSERT INTO acc.ledger SELECT * FROM raw.events;\n",
        "sql/mart.sql": "CREATE TABLE mart.revenue AS SELECT * FROM acc.ledger;\n",
    })


def _data(tmp_path):
    root = tmp_path / "landing"
    (root / "acc" / "ledger" / "dt=1").mkdir(parents=True)
    (root / "acc" / "ledger" / "dt=1" / "part-0.csv").write_text("id,email,tier\n1,a@x.com,gold\n2,b@y.org,silver\n", encoding="utf-8")
    (root / "raw").mkdir()
    (root / "raw" / "events.jsonl").write_text('{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    (root / "tmp.csv").write_text("x\n1\n", encoding="utf-8")
    (root / "broken.json").write_text("{", encoding="utf-8")
    (root / "cols.parquet").write_bytes(parquet_file(
        [[(4, BINARY, "schema"), (5, I32, 1)], [(1, I32, 2), (4, BINARY, "id")]], 3))
    return root


def _imports(run_script, repo, out):
    for script, args in (("scan.py", (repo, "--out", out)), ("imports.py", ("--out", out))):
        done = run_script(script, *args)
        assert done.returncode == 0, done.stderr


def test_profile_writes_matches_and_stays_deterministic(make_repo, run_script, tmp_path):
    repo, out, data = _repo(make_repo), tmp_path / "out", _data(tmp_path)
    _imports(run_script, repo, out)

    done = run_script("data_profile.py", "--out", out, "--data", data, "--show-values")

    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert (summary["datasets"], summary["matched_tables"], summary["orphan_datasets"]) == (5, 2, 3)
    first = (out / "profile.json").read_text(encoding="utf-8")
    profile = json.loads(first)
    assert [m["table"] for m in profile["matches"]] == ["acc.ledger", "raw.events"]
    assert profile["tables_without_data"] == ["mart.revenue"]
    by_id = {d["id"]: d for d in profile["datasets"]}
    assert by_id["d0:broken.json"]["status"] == "error" and by_id["d0:broken.json"]["reason"] == "json_error"
    assert "a@x.com" not in first and "b@y.org" not in first
    assert run_script("data_profile.py", "--out", out, "--data", data, "--show-values").returncode == 0
    assert (out / "profile.json").read_text(encoding="utf-8") == first


def test_parquet_without_a_reader_is_schema_only(make_repo, run_script, tmp_path):
    if find_reader() is not None:
        pytest.skip("a Parquet library is installed here")
    repo, out, data = _repo(make_repo), tmp_path / "out", _data(tmp_path)
    _imports(run_script, repo, out)

    assert run_script("data_profile.py", "--out", out, "--data", data,
                      env={"PYTHONPATH": str(tmp_path / "nothing")}).returncode == 0

    parquet = {d["id"]: d for d in json.loads((out / "profile.json").read_text())["datasets"]}["d0:cols.parquet"]
    assert parquet["status"] == "schema_only" and parquet["reason"] == "no_reader"


def test_data_inside_the_repository_or_output_is_refused(make_repo, run_script, tmp_path):
    repo, out = _repo(make_repo), tmp_path / "out"
    _imports(run_script, repo, out)

    inside = run_script("data_profile.py", "--out", out, "--data", repo / "sql")
    (out / "inner").mkdir()
    overlap = run_script("data_profile.py", "--out", out, "--data", out / "inner")

    assert inside.returncode == 2 and "outside the repository" in inside.stderr
    assert overlap.returncode == 2 and "overlap the output" in overlap.stderr


def test_profile_needs_imports_first(make_repo, run_script, tmp_path):
    repo, out = _repo(make_repo), tmp_path / "out"
    assert run_script("scan.py", repo, "--out", out).returncode == 0

    done = run_script("data_profile.py", "--out", out, "--data", _data(tmp_path))

    assert done.returncode == 2 and "imports.py" in done.stderr
