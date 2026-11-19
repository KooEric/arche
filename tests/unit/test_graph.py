import hashlib

from arche_lib import jsonio
from arche_lib.graph import build_graph
from arche_lib.inventory import count_lines
from arche_lib.langs import LANG_BY_EXT
from arche_lib.langs.registry import ADAPTERS

SOURCES = {
    "app/__init__.py": "",
    "app/main.py": (
        "import os\n"
        "import os.path\n"
        "from app import util\n"
        "from app.util import helper\n"
        "def load(name):\n"
        "    import importlib\n"
        "    return importlib.import_module(name)\n"
    ),
    "app/util.py": "def helper():\n    return 1\n",
    "app/broken.py": "def (:\n",
}
SKIPPED = [{"path": "README.md", "status": "skipped", "reason": "unsupported_lang"}]


def _inventory(sources=SOURCES, skipped=SKIPPED):
    analyzed = [
        {"path": path, "status": "analyzed", "lang": "python", "bytes": len(text),
         "lines": count_lines(text), "sha256": hashlib.sha256(text.encode()).hexdigest()}
        for path, text in sources.items()
    ]
    return {
        "schema_version": "0.1",
        "repo": {"name": "demo", "root": "/abs/demo", "commit": None, "dirty": None},
        "files": analyzed + list(skipped),
    }


def _graph(sources=SOURCES):
    return build_graph(_inventory(sources), sources.get)


def test_every_detected_language_has_an_adapter():
    assert set(LANG_BY_EXT.values()) <= set(ADAPTERS)


def test_repo_block_omits_the_absolute_root():
    assert _graph()["repo"] == {"name": "demo", "commit": None, "dirty": None}


def test_nodes_are_parsed_files_and_their_symbols_sorted_by_id():
    ids = [node["id"] for node in _graph()["nodes"]]

    assert ids == [
        "file:app/__init__.py",
        "file:app/main.py",
        "file:app/util.py",
        "sym:app/main.py#load",
        "sym:app/util.py#helper",
    ]


def test_file_node_carries_language_lines_and_hash():
    node = next(n for n in _graph()["nodes"] if n["id"] == "file:app/util.py")

    assert node == {
        "id": "file:app/util.py", "type": "file", "lang": "python", "lines": 2,
        "sha256": hashlib.sha256(SOURCES["app/util.py"].encode()).hexdigest(),
    }


def test_verified_edge_has_names_and_line_evidence():
    edges = [e for e in _graph()["edges"] if e["status"] == "verified"]

    assert edges == [
        {"kind": "IMPORTS", "from": "file:app/main.py", "to": "file:app/util.py", "names": [],
         "status": "verified", "origin": "script",
         "evidence": {"path": "app/main.py", "line": 3, "quote": "from app import util"}},
        {"kind": "IMPORTS", "from": "file:app/main.py", "to": "file:app/util.py", "names": ["helper"],
         "status": "verified", "origin": "script",
         "evidence": {"path": "app/main.py", "line": 4, "quote": "from app.util import helper"}},
    ]


def test_unresolved_edge_has_spec_reason_and_true_flags_only():
    edges = [e for e in _graph()["edges"] if e["status"] == "unresolved"]

    assert edges == [
        {"kind": "IMPORTS", "from": "file:app/main.py", "to": None, "spec": "<dynamic>",
         "reason": "dynamic", "status": "unresolved", "origin": "script",
         "conditional": True, "dynamic": True,
         "evidence": {"path": "app/main.py", "line": 7,
                      "quote": "return importlib.import_module(name)"}},
    ]


def test_externals_are_deduplicated_per_file_and_package():
    assert _graph()["externals"] == [
        {"from": "file:app/main.py", "package": "os", "line": 1},
        {"from": "file:app/main.py", "package": "importlib", "line": 6},
    ]


def test_unparseable_file_moves_to_skipped_and_the_rest_still_builds():
    assert _graph()["skipped"] == [
        {"path": "README.md", "reason": "unsupported_lang"},
        {"path": "app/broken.py", "reason": "parse_error"},
    ]


def test_output_does_not_depend_on_inventory_order():
    forward = _graph()
    backward = build_graph(_inventory(dict(reversed(list(SOURCES.items())))), SOURCES.get)

    assert jsonio.dumps(forward) == jsonio.dumps(backward)


def test_inferred_edge_keeps_its_target_and_names_the_rule():
    sources = {"pipeline/run.py": "import card_crawl\n", "crawling/card_crawl.py": "X = 1\n"}

    edges = build_graph(_inventory(sources, skipped=[]), sources.get)["edges"]

    assert edges == [
        {"kind": "IMPORTS", "from": "file:pipeline/run.py", "to": "file:crawling/card_crawl.py",
         "names": [], "status": "inferred", "rule": "unique-module-name", "origin": "script",
         "evidence": {"path": "pipeline/run.py", "line": 1, "quote": "import card_crawl"}},
    ]


def test_ambiguous_edge_lists_its_candidates():
    sources = {"main/run.py": "import util\n", "a/util.py": "", "b/util.py": ""}

    edge = build_graph(_inventory(sources, skipped=[]), sources.get)["edges"][0]

    assert (edge["status"], edge["reason"], edge["candidates"]) == (
        "unresolved", "ambiguous_module", ["a/util.py", "b/util.py"]
    )
