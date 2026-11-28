import base64
import hashlib
import json
import re


def test_report_is_offline_deterministic_and_escapes_paths(make_repo, run_script, tmp_path, git_cmd):
    unsafe = 'x"><script>alert(1)</script>.py'.replace("/", "_")
    repo = make_repo({"a.py": "import b\n", "b.py": "import a\n", unsafe: "pass\n"})
    commit = git_cmd(repo, "rev-parse", "HEAD").strip()
    before = git_cmd(repo, "status", "--porcelain")
    out = tmp_path / "out"
    for script, args in (
        ("scan.py", (repo, "--out", out)),
        ("imports.py", ("--out", out)),
        ("render.py", ("--out", out)),
    ):
        done = run_script(script, *args)
        assert done.returncode == 0, done.stderr

    path = out / "report.html"
    html = path.read_text(encoding="utf-8")
    assert unsafe not in html
    assert "x&quot;&gt;&lt;script&gt;" in html
    assert "One observed static import cycle" in html
    assert "a.py:1" in html
    assert "b.py:1" in html
    assert commit in html
    assert "Git status comparison <code>match</code>" in html
    assert "import b" not in html
    assert git_cmd(repo, "status", "--porcelain") == before

    for tag in ("script", "style"):
        blocks = re.findall(f"<{tag}>(.*?)</{tag}>", html, re.S)
        assert len(blocks) == 1
        digest = base64.b64encode(hashlib.sha256(blocks[0].encode()).digest()).decode()
        assert f"{tag}-src 'sha256-{digest}'" in html

    assert run_script("render.py", "--out", out).returncode == 0
    assert path.read_text(encoding="utf-8") == html

    (repo / "new.py").write_text("pass\n", encoding="utf-8")
    done = run_script("render.py", "--out", out)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["readonly_check"] == "changed"


def test_modules_and_optional_validated_tour(make_repo, run_script, tmp_path):
    repo = make_repo({"root.py": "pass\n", "app/main.py": "pass\n", "app/readme.md": "docs\n", "assets/image.svg": "image\n"})
    out = tmp_path / "out"
    for script, args in (("scan.py", (repo, "--out", out)), ("imports.py", ("--out", out))):
        done = run_script(script, *args)
        assert done.returncode == 0, done.stderr

    # The original CLI remains valid and includes unsupported-only modules.
    done = run_script("render.py", "--out", out)
    assert done.returncode == 0, done.stderr
    html = (out / "report.html").read_text()
    for name, counts in ((".", "1 parsed · 0 excluded"), ("app", "1 parsed · 1 excluded"), ("assets", "0 parsed · 1 excluded")):
        assert f'<strong>{name}</strong><span>{counts}</span>' in html
    assert "No tour available" in html

    inventory = json.loads((out / "inventory.json").read_text())
    file = next(entry for entry in inventory["files"] if entry["path"] == "app/main.py")
    tour = {"version": 1, "steps": [{"id": "entry", "path": file["path"], "start_line": 1, "end_line": 1, "sha256": file["sha256"]}], "edges": []}
    tour_path = tmp_path / "tour.json"
    tour_path.write_text(json.dumps(tour))
    done = run_script("render.py", "--out", out, "--tour", tour_path)
    assert done.returncode == 0, done.stderr
    assert "<code>entry</code>" in (out / "report.html").read_text()

    tour["steps"][0]["sha256"] = "0" * 64
    tour_path.write_text(json.dumps(tour))
    done = run_script("render.py", "--out", out, "--tour", tour_path)
    assert done.returncode != 0
    assert "stale SHA256" in done.stderr


def test_one_command_runs_every_step(make_repo, run_script, tmp_path, git_cmd):
    repo = make_repo({"a.py": "import b\n", "b.py": "pass\n"})
    out = tmp_path / "out"
    done = run_script("arche.py", repo, "--out", out, "--lang", "ko")
    assert done.returncode == 0, done.stderr
    summary = json.loads(done.stdout)
    assert summary["step"] == "arche" and summary["analyzed"] == 2 and summary["edges"] == {"verified": 1}
    html = (out / "report.html").read_text(encoding="utf-8")
    assert summary["report"] == str((out / "report.html").resolve())
    assert '<html lang="ko">' in html
    subject = git_cmd(repo, "log", "-1", "--format=%s").strip()
    assert subject in html
    assert run_script("arche.py", repo / "missing").returncode != 0


def test_cited_step_survives_edits_elsewhere_and_fails_when_its_lines_change(make_repo, run_script, tmp_path):
    body = "def keep():\n    return 1\n"
    repo = make_repo({"a.py": "import os\n\n" + body})
    out = tmp_path / "out"
    assert run_script("arche.py", repo, "--out", out).returncode == 0
    done = run_script("cite.py", "--out", out, "--path", "a.py", "--start", "3", "--end", "4", "--id", "keep")
    assert done.returncode == 0, done.stderr
    step = json.loads(done.stdout)
    assert step["id"] == "keep" and len(step["range_sha256"]) == 64
    tour = tmp_path / "tour.json"
    tour.write_text(json.dumps({"version": 1, "steps": [step], "edges": []}))

    # Lines added above move the citation; the tour follows them.
    (repo / "a.py").write_text("import os\nimport sys\n\n\n" + body)
    moved = tmp_path / "moved"
    done = run_script("arche.py", repo, "--out", moved, "--tour", tour)
    assert done.returncode == 0, done.stderr
    assert ":5–6" in (moved / "report.html").read_text(encoding="utf-8")
    fresh = tmp_path / "fresh.json"
    done = run_script("cite.py", "--out", moved, "--refresh", tour, "--output", fresh)
    assert json.loads(done.stdout)["reanchored"] == ["keep"]
    assert json.loads(fresh.read_text())["steps"][0]["start_line"] == 5

    # Editing the cited lines themselves makes the citation stale.
    (repo / "a.py").write_text("import os\n\ndef keep():\n    return 2\n")
    done = run_script("arche.py", repo, "--out", tmp_path / "stale", "--tour", tour)
    assert done.returncode != 0 and "stale SHA256" in done.stderr

    assert run_script("cite.py", "--out", out, "--path", "missing.py", "--start", "1").returncode != 0


SQL_FILES = {"q/stage.sql": "CREATE TABLE stage.orders AS SELECT o.amt AS amount FROM raw.orders o;\n",
             "q/mart.sql": "INSERT INTO mart.revenue SELECT SUM(s.amount) AS total FROM stage.orders s;\n"}


def _scan_and_import(make_repo, run_script, tmp_path, files):
    repo, out = make_repo(files), tmp_path / "out"
    for script, args in (("scan.py", (repo, "--out", out)), ("imports.py", ("--out", out))):
        done = run_script(script, *args)
        assert done.returncode == 0, done.stderr
    return out


def test_report_draws_column_lineage_from_columns_json(make_repo, run_script, tmp_path):
    out = _scan_and_import(make_repo, run_script, tmp_path, SQL_FILES)
    assert (out / "columns.json").exists()

    done = run_script("render.py", "--out", out)

    assert done.returncode == 0, done.stderr
    html = (out / "report.html").read_text(encoding="utf-8")
    assert 'class="group column-lineage"' in html and "If this column is removed" in html
    assert "<code>raw.orders.amt</code>" in html and "<code>mart.revenue.total</code>" in html


def test_render_refuses_columns_json_of_another_snapshot(make_repo, run_script, tmp_path):
    out = _scan_and_import(make_repo, run_script, tmp_path, SQL_FILES)
    columns = json.loads((out / "columns.json").read_text())
    (out / "columns.json").write_text(json.dumps({**columns, "inventory_sha256": "x"}), encoding="utf-8")

    done = run_script("render.py", "--out", out)

    assert done.returncode == 3
    assert "columns.json describes a different snapshot; run imports.py again" in done.stderr
    for broken in ([], {**columns, "columns_version": 3}, {**columns, "edges": {}}):
        (out / "columns.json").write_text(json.dumps(broken), encoding="utf-8")
        done = run_script("render.py", "--out", out)
        assert done.returncode == 3 and "columns.json" in done.stderr


def test_render_refuses_columns_json_that_is_too_deep_or_not_utf8(make_repo, run_script, tmp_path):
    out = _scan_and_import(make_repo, run_script, tmp_path, SQL_FILES)

    for broken in (b"[" * 200_000, b'{"columns": "\xff\xfe"}'):
        (out / "columns.json").write_bytes(broken)
        done = run_script("render.py", "--out", out)
        assert done.returncode == 3, done.stderr
        assert "Traceback" not in done.stderr and "columns.json" in done.stderr


def test_report_without_columns_json_has_no_column_lineage(make_repo, run_script, tmp_path):
    out = _scan_and_import(make_repo, run_script, tmp_path, SQL_FILES)
    assert run_script("render.py", "--out", out).returncode == 0
    (out / "columns.json").unlink()

    done = run_script("render.py", "--out", out)

    assert done.returncode == 0, done.stderr
    html = (out / "report.html").read_text(encoding="utf-8")
    assert "column-lineage" not in html and "Column lineage" not in html and "Tables" in html
