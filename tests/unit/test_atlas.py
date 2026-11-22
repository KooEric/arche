import json

import pytest

from arche_lib.atlas import build_atlas
from arche_lib.errors import IntegrityError
from arche_lib.mapview import render_map
from test_graph_multilang import _graph

SOURCES = {
    "app/main.py": "import api.server\n",
    "app/view.py": "pass\n",
    "api/server.py": "import api.db.store\nimport requests\n",
    "api/db/store.py": "pass\n",
    "tools/run.py": "pass\n",
}


def _inputs():
    graph = _graph(SOURCES)
    inventory = {"files": [{"path": n["id"][5:], "status": "analyzed", "lines": n["lines"], "sha256": n["sha256"]}
                           for n in graph["nodes"] if n["type"] == "file"]}
    return inventory, graph


def _document():
    return {"version": 1, "project": {"name": "demo", "summary": "s"}, "parts": [
        {"id": "app", "label": "App", "kind": "ui", "summary": "screens", "paths": ["app"]},
        {"id": "api", "label": "API", "kind": "service", "summary": "server", "paths": ["api"]},
        {"id": "store", "label": "Store", "kind": "data", "summary": "storage", "parent": "api", "paths": ["api/db/*.py"]},
        {"id": "http", "label": "Requests", "kind": "external", "summary": "client", "packages": ["requests"]}],
        "edges": [{"from": "store", "to": "http", "label": "claims", "evidence": {"path": "api/db/store.py", "start_line": 1}}],
        "flows": [{"id": "f", "title": "Flow", "steps": [{"part": "app", "note": "start"}, {"part": "api", "note": "serve"}]}]}


def test_files_go_to_the_deepest_part_and_links_are_counted():
    inventory, graph = _inputs()
    atlas = build_atlas(inventory, graph, _document(), {"app/main.py": {"name": "Entry", "description": "Starts <it>"}})
    owner = {item["path"]: item["part"] for item in atlas["files"]}
    assert owner == {"api/db/store.py": "store", "api/server.py": "api", "app/main.py": "app", "app/view.py": "app", "tools/run.py": None}
    links = {(l["from"], l["to"], l["status"], l["kind"]) for l in atlas["links"]}
    assert links == {("app", "api", "verified", "import"), ("api", "store", "verified", "import"),
                     ("api", "http", "verified", "package"), ("store", "http", "cited", "cited")}
    cited = next(l for l in atlas["links"] if l["status"] == "cited")
    assert cited["evidence"]["sha256"] and cited["from_file"] == 0
    assert atlas["stats"]["assigned"] == 4 and atlas["stats"]["described_files"] == 1
    assert any("belong to no part" in w for w in atlas["warnings"])
    html = render_map("<style></style><script></script>$csp $lang $title $noscript $data", atlas, "ko")
    assert "Starts \\u003cit>" in html and "<it>" not in html and "지도" in html


@pytest.mark.parametrize("change, message", [
    (lambda d: d["parts"][0].update(kind="screen"), "parts[0].kind"),
    (lambda d: d["parts"][2].update(parent="nope"), "parts[2].parent"),
    (lambda d: d["edges"][0]["evidence"].update(path="api/db/missing.py"), "edges[0].evidence.path"),
    (lambda d: d["edges"][0]["evidence"].update(start_line=99), "past the end"),
    (lambda d: d["flows"][0]["steps"][0].update(part="apps"), "flows[0].steps[0].part"),
    (lambda d: d.update(version=2), "version"),
])
def test_map_errors_name_the_field(change, message):
    inventory, graph = _inputs()
    document = _document()
    change(document)
    with pytest.raises(IntegrityError) as error:
        build_atlas(inventory, graph, document)
    assert message in str(error.value)


def test_folder_map_without_map_json_splits_until_readable():
    inventory, graph = _inputs()
    atlas = build_atlas(inventory, graph)
    assert atlas["generated_from"] == "folders" and 3 <= len(atlas["parts"]) <= 9
    assert all(part["unnamed"] for part in atlas["parts"]) and atlas["stats"]["assigned"] == 5


def test_brief_map_describe_and_render_write_the_map(make_repo, run_script, tmp_path):
    repo = make_repo(SOURCES)
    out = tmp_path / "out"
    assert run_script("arche.py", repo, "--out", out).returncode == 0
    assert "ENTRY POINT CANDIDATES" in (out / "brief.txt").read_text() if run_script("brief.py", "--out", out).returncode == 0 else False
    (out / "map.json").write_text(json.dumps(_document()))
    done = run_script("map.py", "--out", out)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)["files_assigned"] == "4/5"
    done = run_script("describe.py", "--out", out)
    assert json.loads(done.stdout)["empty"] == 5
    filled = json.loads((out / "descriptions" / "app.json").read_text())
    filled["app/main.py"] = {"name": "Entry", "description": "Starts the app."}
    (out / "descriptions" / "app.json").write_text(json.dumps(filled))
    assert json.loads(run_script("describe.py", "--out", out).stdout)["empty"] == 4
    assert "files 1/5" in json.loads(run_script("map.py", "--out", out).stdout)["descriptions"]
    done = run_script("render.py", "--out", out)
    assert done.returncode == 0, done.stderr
    html = (out / "map.html").read_text(encoding="utf-8")
    assert "Starts the app." in html and '"label":"App"' in html and "import api.server" not in html

    bad = _document()
    bad["parts"][0]["kind"] = "screen"
    (out / "map.json").write_text(json.dumps(bad))
    done = run_script("map.py", "--out", out)
    assert done.returncode != 0 and "parts[0].kind" in done.stderr


def test_screens_review_lists_and_editor_links():
    from arche_lib.atlas import attach_review
    inventory, graph = _inputs()
    document = _document()
    document["screens"] = [{"title": "Home", "part": "app", "items": [
        {"label": "Open", "behavior": "Shows the list.", "evidence": {"path": "app/view.py", "start_line": 1}}]}]
    atlas = build_atlas(inventory, graph, document)
    assert atlas["screens"][0]["items"][0]["evidence"]["sha256"]
    atlas = attach_review(atlas, {"files": [{"path": "api/server.py", "status": "modified"}]},
                          [{"rule": "unused-private-symbol", "path": "app/main.py", "line": 3, "name": "_x"}])
    counts = {part["id"]: (part["changed"], part["findings"]) for part in atlas["parts"]}
    assert counts["api"] == (1, 0) and counts["app"] == (0, 1)
    assert atlas["findings"][0]["part"] == "app" and atlas["changes"][0]["part"] == "api"
    template = "<style></style><script></script>$csp $lang $title $noscript $data"
    assert "vscode://" not in render_map(template, atlas) and "/abs/repo" not in render_map(template, atlas)
    html = render_map(template, atlas, editor={"name": "vscode", "root": "/abs/repo/"})
    assert '"editor":{"url":"vscode://file/{path}:{line}","root":"/abs/repo"}' in html

    document["screens"][0]["part"] = "nope"
    with pytest.raises(IntegrityError) as error:
        build_atlas(inventory, graph, document)
    assert "screens[0].part" in str(error.value)


def test_brief_counts_route_like_lines_that_were_not_read(make_repo, run_script, tmp_path):
    repo = make_repo({"app/server.ts": 'import { Hono } from "hono";\nconst app = new Hono();\napp.get("/ok", c => c.text("ok"));\n'
                                       'const other = make();\nother.post("/missed", handler);\nexport default app;\n'})
    out = tmp_path / "out"
    assert run_script("arche.py", repo, "--out", out).returncode == 0
    assert run_script("brief.py", "--out", out).returncode == 0
    brief = (out / "brief.txt").read_text()
    assert "ROUTE-LIKE LINES NOT READ (1;" in brief and "app/server.ts:5" in brief
    done = run_script("render.py", "--out", out, "--editor", "zed")
    assert done.returncode == 0, done.stderr
    assert "zed://file/" in (out / "map.html").read_text(encoding="utf-8")
    assert str(repo) not in (out / "report.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("paths, expected", [
    (["lib/a.py", "lib/b.py", "app/main.py"], {"lib", "app"}),                       # nothing to split: files only
    (["src/a.py", "src/x/b.py", "src/y/c.py"], {"src", "src/x", "src/y"}),           # own files stay under the folder
    (["src/x/b.py", "src/x/deep/c.py", "src/x/deep/d.py"], {"src/x", "src/x/deep"}),  # stops when no folder is left
    (["only.py"], {"."}),
])
def test_folder_split_always_terminates(paths, expected):
    from arche_lib.atlas import _auto_parts
    assert {part["label"] for part in _auto_parts(paths)} == expected
