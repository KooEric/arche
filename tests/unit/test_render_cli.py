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
