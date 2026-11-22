import json


def _document():
    return {"version": 1, "project": {"name": "demo", "summary": "s"}, "parts": [
        {"id": "app", "label": "App", "kind": "ui", "summary": "screens", "paths": ["app"],
         "evidence": {"path": "app/main.py", "start_line": 3, "end_line": 4}},
        {"id": "lib", "label": "Lib", "kind": "library", "summary": "helpers", "paths": ["lib"]}],
        "flows": [{"id": "f", "title": "Flow", "steps": [
            {"part": "lib", "note": "computes", "evidence": {"path": "lib/calc.py", "start_line": 1, "end_line": 2}},
            {"part": "lib", "note": "formats", "evidence": {"path": "lib/old_name.py", "start_line": 1}}]}]}


def test_map_and_descriptions_follow_the_code_to_a_new_scan(make_repo, run_script, tmp_path):
    body = "def start():\n    return 1\n"
    repo = make_repo({"app/main.py": "import lib.calc\n\n" + body, "lib/calc.py": "def add(a, b):\n    return a + b\n",
                      "lib/old_name.py": "def fmt(x):\n    return str(x)\n", "lib/gone.py": "pass\n"})
    first = tmp_path / "first"
    assert run_script("arche.py", repo, "--out", first).returncode == 0
    (first / "map.json").write_text(json.dumps(_document()))
    assert run_script("map.py", "--out", first).returncode == 0
    assert run_script("describe.py", "--out", first).returncode == 0
    for name, key, text in (("app.json", "app/main.py", "Starts the app."), ("lib.json", "lib/calc.py", "Adds numbers."),
                            ("lib.json", "lib/old_name.py", "Formats a value."), ("lib.json", "lib/gone.py", "Nothing.")):
        content = json.loads((first / "descriptions" / name).read_text())
        content[key] = {"name": "n", "description": text}
        (first / "descriptions" / name).write_text(json.dumps(content))
    assert (first / "map.lock.json").exists()

    # Lines shift in one file, another is edited where it is cited, one is renamed, one deleted, one added.
    (repo / "app/main.py").write_text("import lib.calc\nimport os\n\n\n" + body)
    (repo / "lib/calc.py").write_text("def add(a, b):\n    return b + a\n")
    (repo / "lib/old_name.py").rename(repo / "lib/new_name.py")
    (repo / "lib/gone.py").unlink()
    (repo / "lib/extra.py").write_text("x = 1\n")

    second = tmp_path / "second"
    done = run_script("arche.py", repo, "--out", second, "--from", first)
    assert done.returncode == 0, done.stderr
    report = json.loads((second / "carry.json").read_text())
    assert report["files_new"] == ["lib/extra.py"] and report["files_removed"] == ["lib/gone.py"]
    assert report["files_changed"] == ["app/main.py", "lib/calc.py"] and report["review_descriptions"] == ["app/main.py", "lib/calc.py"]
    assert report["files_moved"] == [{"from": "lib/old_name.py", "to": "lib/new_name.py"}]
    assert report["evidence_moved"] == [{"where": "parts[0]", "path": "app/main.py", "from_line": 3, "to_line": 5}]
    assert [item["where"] for item in report["evidence_stale"]] == ["flows[0].steps[0]"]

    carried = json.loads((second / "map.json").read_text())
    assert carried["parts"][0]["evidence"] == {"path": "app/main.py", "start_line": 5, "end_line": 6}
    assert "evidence" not in carried["flows"][0]["steps"][0]
    assert carried["flows"][0]["steps"][1]["evidence"]["path"] == "lib/new_name.py"
    lib = json.loads((second / "descriptions" / "lib.json").read_text())
    assert lib["lib/new_name.py"]["description"] == "Formats a value." and "lib/gone.py" not in lib and "lib/old_name.py" not in lib
    summary = json.loads(done.stdout)
    assert summary["map_source"] == "map.json" and summary["carried"]["files_new"] == 1
    html = (second / "map.html").read_text(encoding="utf-8")
    assert "Formats a value." in html and '"label":"App"' in html

    # Only the new file is left to describe.
    assert json.loads(run_script("describe.py", "--out", second).stdout)["first_empty"] == ["lib/extra.py"]

    shared = tmp_path / "shared"
    assert run_script("carry.py", "--out", second, "--save", shared).returncode == 0
    assert sorted(p.name for p in shared.iterdir()) == ["descriptions", "map.json", "map.lock.json"]
    third = tmp_path / "third"
    done = run_script("arche.py", repo, "--out", third, "--from", shared)
    assert done.returncode == 0 and json.loads(done.stdout)["carried"]["files_changed"] == 0
    assert run_script("carry.py", "--out", third, "--from", tmp_path / "missing").returncode != 0


def test_a_file_renamed_and_edited_keeps_its_description_for_review(make_repo, run_script, tmp_path):
    body = "".join(f"def step_{n}(x):\n    return x + {n}\n\n" for n in range(12))
    repo = make_repo({"lib/report.py": body, "lib/other.py": "".join(f"VALUE_{n} = {n}\n" for n in range(20)), "app/main.py": "pass\n"})
    first = tmp_path / "first"
    assert run_script("arche.py", repo, "--out", first).returncode == 0
    (first / "map.json").write_text(json.dumps({"version": 1, "project": {"name": "d", "summary": "s"}, "parts": [
        {"id": "lib", "label": "Lib", "kind": "library", "summary": "x", "paths": ["lib"],
         "evidence": {"path": "lib/report.py", "start_line": 4, "end_line": 5}},
        {"id": "app", "label": "App", "kind": "ui", "summary": "y", "paths": ["app"]}]}))
    assert run_script("map.py", "--out", first).returncode == 0 and run_script("describe.py", "--out", first).returncode == 0
    content = json.loads((first / "descriptions" / "lib.json").read_text())
    content["lib/report.py"] = {"name": "Report", "description": "Builds the report."}
    (first / "descriptions" / "lib.json").write_text(json.dumps(content))
    assert run_script("map.py", "--out", first).returncode == 0
    assert "lib/report.py" in json.loads((first / "map.lock.json").read_text())["sketches"]

    (repo / "lib/report.py").unlink()
    (repo / "lib/summary.py").write_text("import os\n\n" + body.replace("return x + 11", "return x + 110"))
    (repo / "lib/unrelated.py").write_text("".join(f"def other_{n}():\n    return {n}\n" for n in range(12)))
    second = tmp_path / "second"
    done = run_script("arche.py", repo, "--out", second, "--from", first)
    assert done.returncode == 0, done.stderr
    report = json.loads((second / "carry.json").read_text())
    assert report["files_moved"] == [{"from": "lib/report.py", "to": "lib/summary.py", "edited": True}]
    assert report["files_new"] == ["lib/unrelated.py"] and report["review_descriptions"] == ["lib/summary.py"]
    assert json.loads((second / "descriptions" / "lib.json").read_text())["lib/summary.py"]["description"] == "Builds the report."
    assert json.loads((second / "map.json").read_text())["parts"][0]["evidence"] == {"path": "lib/summary.py", "start_line": 6, "end_line": 7}
