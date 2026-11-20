#!/usr/bin/env python3
"""Discover, run, replay and compare bundled Arche evaluation tasks."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import repair
import evaluation_report as reports

HERE = Path(__file__).resolve().parent


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    run = commands.add_parser("run")
    run.add_argument("--case", required=True, help="case ID or 'understanding' for the entire read-only suite")
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--model", default="gpt-6.1-terra")
    run.add_argument("--codex", default="codex")
    run.add_argument("--prompt-variant", choices=("task-only", "verify"), default="task-only")
    run.add_argument("--prompt-file", type=Path, help="understanding study prompt template")
    for name in ["score", "report"]:
        action = commands.add_parser(name)
        if name == "report":
            action.add_argument("--runs", nargs="+", type=Path, required=True)
            action.add_argument("--output", type=Path, required=True, help="new directory for offline report")
        else:
            action.add_argument("--run", type=Path, required=True)
        action.add_argument("--browser-executable", default=os.environ.get("ARCHE_EVAL_BROWSER_EXECUTABLE"))
    args = parser.parse_args(argv)
    cases = reports.study_cases()
    repairs = ("pickup-undo", "translation-fallback")
    if args.command == "list":
        print(json.dumps({"schema_version": 1, "understanding": list(cases), "repair": repairs}, indent=2))
        return 0
    if args.command == "run":
        if args.case in repairs:
            if args.prompt_file:
                parser.error("--prompt-file is for understanding studies")
            script = HERE / "repair.py"
            options = ["run", "--case", args.case, "--prompt-variant", args.prompt_variant]
        elif args.case in cases or args.case == "understanding":
            if args.prompt_variant != "task-only":
                parser.error("use --prompt-file for an understanding study")
            script = HERE / "studies" / "run.py"
            options = ["--cases", args.case] if args.case in cases else []
            if args.prompt_file:
                options += ["--prompt-file", str(args.prompt_file)]
        else:
            parser.error("unknown case; see list")
        return subprocess.call([sys.executable, "-B", str(script), *options, "--model", args.model,
                                "--codex", args.codex, "--output", str(args.output)])
    if args.command == "score":
        rows = reports.replay(args.run, args.browser_executable)
        print(json.dumps({"schema_version": 1, "trials": rows}, indent=2, ensure_ascii=False))
        return 0 if rows and all(row["outcome"] == "passed" for row in rows) else 1
    rows = []
    seen = set()
    for directory in args.runs:
        if directory.resolve() in seen:
            parser.error("duplicate run directory")
        seen.add(directory.resolve())
        rows.extend(reports.replay(directory, args.browser_executable))
    reports.write_report(args.output, rows)
    print(args.output.resolve() / "report.html")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, OSError, KeyError, TypeError) as exc:
        sys.exit(f"arche-eval: {exc}")
