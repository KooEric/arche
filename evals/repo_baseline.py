"""Run the installed skill helpers on local checkouts and record coverage."""
import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "arche" / "scripts"


def scan(repo, out):
    for script, args in (("scan", [str(repo)]), ("imports", []), ("render", [])):
        subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / (script + ".py")), *args, "--out", str(out)],
            check=True,
        )
    inventory = json.loads((out / "inventory.json").read_text(encoding="utf-8"))
    graph = json.loads((out / "graph.json").read_text(encoding="utf-8"))
    run = json.loads((out / "run.json").read_text(encoding="utf-8"))
    unsupported = Counter(
        Path(entry["path"]).suffix or "(none)"
        for entry in graph["skipped"] if entry["reason"] == "unsupported_lang"
    )
    return {
        "repo": repo.name,
        "revision": graph["repo"]["commit"],
        "dirty": graph["repo"]["dirty"],
        "inventory_entries": len(inventory["files"]),
        "parsed_files": sum(n["type"] == "file" for n in graph["nodes"]),
        "import_statuses": dict(Counter(e["status"] for e in graph["edges"])),
        "unsupported_by_suffix": dict(unsupported),
        "readonly_check": run["readonly_check"],
        "report": str((out / "report.html").relative_to(out.parent)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repos", nargs="+", type=Path)
    parser.add_argument("--output-parent", type=Path, default=Path(tempfile.gettempdir()))
    args = parser.parse_args()
    repos = [p.resolve() for p in args.repos]
    parent = args.output_parent.resolve()
    if any(not p.is_dir() for p in repos):
        parser.error("each repository path must be an existing directory")
    if any(parent.is_relative_to(p) for p in repos):
        parser.error("output parent must be outside all scanned directories")
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="arche-baseline-", dir=parent))
    rows = [scan(repo, root / f"{i}-{repo.name}") for i, repo in enumerate(repos, 1)]
    summary = root / "baseline.json"
    summary.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(summary)
    return int(any(row["readonly_check"] == "changed" for row in rows))


if __name__ == "__main__":
    sys.exit(main())
