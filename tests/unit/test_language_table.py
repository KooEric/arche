import pytest

from arche_lib import inventory
from arche_lib.langs import language_for
from arche_lib.langs.registry import ADAPTERS, adapter_for


@pytest.mark.parametrize(
    "path, lang",
    [
        ("src/a.ts", "typescript"), ("src/a.tsx", "typescript"), ("src/a.mts", "typescript"),
        ("src/a.cts", "typescript"), ("types/a.d.ts", "typescript"),
        ("src/a.js", "javascript"), ("src/a.jsx", "javascript"), ("src/a.mjs", "javascript"),
        ("src/a.cjs", "javascript"), ("lib/a.dart", "dart"), ("a.py", "python"),
        ("src/A.java", "java"), ("src/a.kt", "kotlin"), ("build.gradle.kts", "kotlin"),
        ("q/load.sql", "sql"), ("q/settle.hql", "sql"), ("q/UPPER.SQL", "sql"),
        ("README.md", None), ("style.css", None),
    ],
)
def test_language_is_chosen_by_extension(path, lang):
    assert language_for(path) == lang


def test_typescript_and_javascript_share_one_adapter():
    assert adapter_for("typescript") is adapter_for("javascript")
    assert set(ADAPTERS) == {"python", "typescript", "javascript", "dart", "go", "java", "kotlin", "sql"}


@pytest.mark.parametrize("directory", [".wrangler", ".turbo", ".vercel", ".cache", "coverage"])
def test_tool_cache_directories_are_vendored(make_repo, directory):
    path = directory + "/tmp/entry.ts"
    repo = make_repo({path: "export const a = 1;\n"}, use_git=False)

    assert inventory.classify(repo, path)["reason"] == "vendored"
    assert inventory.walk_files(repo) == [directory]


def test_generated_dart_files_stay_skipped(make_repo):
    repo = make_repo({"lib/model.g.dart": "part of 'model.dart';\n", "lib/model.dart": "class A {}\n"},
                     use_git=False)

    assert inventory.classify(repo, "lib/model.g.dart")["reason"] == "generated"
    assert inventory.classify(repo, "lib/model.dart")["lang"] == "dart"
