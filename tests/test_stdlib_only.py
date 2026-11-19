"""The installed skill folder may import nothing outside the standard library."""
import ast
import importlib.util
import os
import sys
import sysconfig
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "skills" / "arche" / "scripts"
OWN_PACKAGE = "arche_lib"


def _top_level_imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.module.split(".")[0]


def _is_stdlib(name):
    if name in sys.builtin_module_names:
        return True
    spec = importlib.util.find_spec(name)
    if spec is None or spec.origin is None:
        return False
    if spec.origin in ("built-in", "frozen"):
        return True
    origin = os.path.realpath(spec.origin)
    stdlib_dirs = {os.path.realpath(sysconfig.get_paths()[key]) for key in ("stdlib", "platstdlib")}
    return "site-packages" not in origin and any(origin.startswith(d + os.sep) for d in stdlib_dirs)


def test_skill_scripts_import_only_the_standard_library():
    offenders = sorted(
        (str(path.relative_to(SCRIPTS_DIR)), name)
        for path in SCRIPTS_DIR.rglob("*.py")
        for name in set(_top_level_imports(path))
        if name != OWN_PACKAGE and not _is_stdlib(name)
    )

    assert offenders == []
