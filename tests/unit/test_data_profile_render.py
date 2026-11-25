"""arche.py --data and render.py with a profile.json."""
import json

from test_data_profile_cli import _data, _imports, _repo


def test_arche_with_data_renders_table_data(make_repo, run_script, tmp_path):
    repo, out, data = _repo(make_repo), tmp_path / "out", _data(tmp_path)

    done = run_script("arche.py", repo, "--out", out, "--data", data)

    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["profile"]["matched_tables"] == 2
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "Table data" in html and "acc.ledger" in html and "a@x.com" not in html


def test_render_refuses_a_profile_of_another_snapshot(make_repo, run_script, tmp_path):
    repo, out, data = _repo(make_repo), tmp_path / "out", _data(tmp_path)
    _imports(run_script, repo, out)
    assert run_script("data_profile.py", "--out", out, "--data", data).returncode == 0
    profile = json.loads((out / "profile.json").read_text())
    (out / "profile.json").write_text(json.dumps({**profile, "inventory_sha256": "x"}), encoding="utf-8")

    done = run_script("render.py", "--out", out)

    assert done.returncode == 3 and "different snapshot" in done.stderr
