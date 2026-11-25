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


def test_values_shown_only_on_request_and_never_for_sensitive_columns(make_repo, run_script, tmp_path):
    repo, data = _repo(make_repo), _data(tmp_path)

    shown, hidden = tmp_path / "shown", tmp_path / "hidden"
    assert run_script("arche.py", repo, "--out", shown, "--data", data, "--show-values").returncode == 0
    assert run_script("arche.py", repo, "--out", hidden, "--data", data).returncode == 0

    html = (shown / "report.html").read_text(encoding="utf-8")
    assert "gold" in html
    for text in (html, (shown / "profile.json").read_text(encoding="utf-8")):
        assert "a@x.com" not in text and "b@y.org" not in text
    assert "gold" not in (hidden / "report.html").read_text(encoding="utf-8")


def _profiled(make_repo, run_script, tmp_path):
    repo, out, data = _repo(make_repo), tmp_path / "out", _data(tmp_path)
    _imports(run_script, repo, out)
    assert run_script("data_profile.py", "--out", out, "--data", data).returncode == 0
    return out


def test_render_refuses_an_unsupported_profile_version(make_repo, run_script, tmp_path):
    out = _profiled(make_repo, run_script, tmp_path)
    profile = json.loads((out / "profile.json").read_text())
    (out / "profile.json").write_text(json.dumps({**profile, "profile_version": 2}), encoding="utf-8")

    done = run_script("render.py", "--out", out)

    assert done.returncode == 3 and "unsupported profile_version" in done.stderr


def test_render_refuses_a_profile_that_is_not_an_object(make_repo, run_script, tmp_path):
    out = _profiled(make_repo, run_script, tmp_path)
    (out / "profile.json").write_text("[1]", encoding="utf-8")

    done = run_script("render.py", "--out", out)

    assert done.returncode == 3 and "profile.json" in done.stderr and "Traceback" not in done.stderr


def test_render_refuses_a_profile_missing_sections(make_repo, run_script, tmp_path):
    out = _profiled(make_repo, run_script, tmp_path)
    profile = json.loads((out / "profile.json").read_text())
    del profile["matches"]
    (out / "profile.json").write_text(json.dumps(profile), encoding="utf-8")

    done = run_script("render.py", "--out", out)

    assert done.returncode == 3 and "matches" in done.stderr and "Traceback" not in done.stderr
