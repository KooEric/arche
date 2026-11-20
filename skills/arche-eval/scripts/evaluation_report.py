"""Replay captured evidence into an offline, script-free comparison report."""
from collections import defaultdict
from difflib import unified_diff
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from statistics import mean
import sys

import repair

STUDIES = Path(__file__).resolve().parent / "studies"
sys.path.insert(0, str(STUDIES))
from harness_score import score_runs


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def study_cases():
    return {case["id"]: case for case in read(STUDIES / "manifest.json")["cases"]}


def replay_study(path):
    golden = read(path / "golden.json")
    manifest = read(path / "manifest.json")
    hashes = read(path / "input-hashes.json")
    digest = sha256((path / "golden.json").read_bytes()).hexdigest()
    if hashes.get("experiment/golden", digest) != digest:
        raise ValueError("archived golden hash differs from recorded input")
    cases = {case["id"]: case for case in golden["cases"]}
    expected = {(case["id"], arm, trial) for case in manifest["cases"] for arm in manifest["arms"]
                for trial in range(1, manifest["trials"] + 1)}
    seen = set()
    rows = []
    for line in (path / "runs.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        run = json.loads(line)
        case = run["case_id"]
        key = (case, run["arm"], run["trial"])
        if key in seen or key not in expected:
            raise ValueError(f"unexpected or duplicate recorded trial: {key}")
        seen.add(key)
        try:
            result = score_runs(golden, [run])["results"][0]
            score = result["score"]
            format_error = None
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            result, score, format_error = {}, None, str(exc)
        checks = {}
        if score is not None:
            checks = {k: not score.get(k) for k in ("missing_steps", "missing_edges", "extra_edges",
                                                      "missing_conditions", "extra_conditions", "forbidden_edges")}
            labels = cases[case].get("citation_lines", {})
            if labels:
                supported = {s["id"] for s in run["steps"] for c in s.get("citations", [])
                             if isinstance(c, dict) and c.get("line") in labels.get(s["id"], {}).get(c.get("path"), [])}
                checks["required_citations"] = set(labels) <= supported
        known = study_cases().get(case)
        case_prefix = (known or {}).get("repo", "") + "/"
        recorded_inputs = {k: v for k, v in hashes.items() if k.startswith("skills/arche/") or
                           k.startswith("studies/" + case_prefix) or k.startswith("evals/experiments/" + case_prefix)}
        fingerprint = sha256(json.dumps({"golden": digest, "recorded_inputs": recorded_inputs,
                                         "timeout_seconds": manifest.get("timeout_seconds"),
                                         "sandbox": manifest.get("sandbox"),
                                         "prompt_template": hashes.get("experiment/prompt-template")},
                                        sort_keys=True).encode()).hexdigest()
        if format_error:
            outcome = "format_error"
        elif score is None:
            outcome = "execution_error"
        elif not checks.get("required_citations", True):
            outcome = "support_error"
        else:
            outcome = "passed" if all(checks.values()) else "failed"
        row = {"case": case, "trial": run["trial"], "model": run.get("model"), "arm": run["arm"],
               "prompt": hashes.get("prompt/"+case), "input": fingerprint, "seconds": run.get("latency_seconds"),
               "tokens": run.get("tokens"), "cost": run.get("cost"), "checks": checks,
               "outcome": outcome,
               "details": score if not format_error else {"format_error": format_error},
               "citation_accuracy": result.get("citation_accuracy"), "evidence": {}, "references": []}
        # Read only bundled synthetic source files whose bytes match the capture.
        if known:
            folder = STUDIES / known["repo"]
            for f in sorted(folder.iterdir()):
                if not f.is_file() or f.is_symlink():
                    continue
                relative = f.relative_to(STUDIES).as_posix()
                recorded = hashes.get("studies/"+relative, hashes.get("evals/experiments/"+relative))
                if sha256(f.read_bytes()).hexdigest() == recorded:
                    row["evidence"][f.name] = f.read_text()
        # Preserve only the structured submitted answer for independent review.
        row["candidate"] = {}
        for kind, allowed in (("steps", {"id", "citations"}),
                              ("edges", {"from", "to", "kind", "citations"}),
                              ("conditions", {"id", "expression"})):
            value = run.get(kind)
            if isinstance(value, list):
                row["candidate"][kind] = [{k: v for k, v in item.items() if k in allowed}
                                          for item in value if isinstance(item, dict)]
        # Link cited and golden source lines to this trial's own verified files.
        labels = cases[case].get("citation_lines", {})
        for claim, sources in labels.items():
            for filename, lines in sources.items():
                if filename in row["evidence"]:
                    for number in lines:
                        if type(number) is int and 1 <= number <= len(row["evidence"][filename].splitlines()):
                            row["references"].append({"claim": claim, "file": filename, "line": number})
        for collection in (run.get("steps", []), run.get("edges", [])):
            if not isinstance(collection, list):
                continue
            for item in collection:
                if not isinstance(item, dict):
                    continue
                claim = item.get("id", str(item.get("from", "")) + " → " + str(item.get("to", "")))
                citations = item.get("citations", [])
                if not isinstance(citations, list):
                    continue
                for citation in citations:
                    if not isinstance(citation, dict):
                        continue
                    filename, number = citation.get("path"), citation.get("line")
                    if filename in row["evidence"] and type(number) is int and 1 <= number <= len(row["evidence"][filename].splitlines()):
                        ref = {"claim": claim, "file": filename, "line": number}
                        if ref not in row["references"]:
                            row["references"].append(ref)
        rows.append(row)
    if expected != seen:
        raise ValueError(f"incomplete study: {len(expected - seen)} planned trials missing")
    return rows


def replay(path, browser=None):
    if not (path / "record.json").is_file():
        return replay_study(path)
    score = repair.score_run(path, browser)
    try:
        if (path / "record.json").is_symlink():
            raise ValueError("symlinked repair record")
        record = read(path / "record.json")
        if not isinstance(record, dict) or not isinstance(record.get("execution"), dict) or record.get("task") not in repair.CASES or not isinstance(record.get("provenance"), dict):
            raise ValueError("invalid repair record structure")
    except (OSError, ValueError, TypeError) as exc:
        return [{"case": "unknown", "trial": path.name, "model": None, "arm": "unknown", "input": None,
                 "prompt": None, "seconds": None, "tokens": None, "cost": None, "checks": {},
                 "outcome": "execution_error", "details": {"errors": [str(exc)]}, "evidence": {}}]
    exe = record["execution"]
    usage = exe.get("usage", [])
    if not isinstance(usage, list):
        usage = []
    tokens = {key: sum(e.get(key, 0) for e in usage if isinstance(e, dict) and type(e.get(key)) is int) for key in
              ("input_tokens", "output_tokens")} if usage else None
    evidence = {}
    fixture = repair.case_fixture(record["task"])
    if not score["execution_errors"]:
        for name in score.get("changed_paths", []):
            # Render bounded candidate bytes as escaped text, never execute them.
            if name not in {p.name for p in fixture.iterdir() if p.is_file()}:
                continue
            before, after = fixture/name, path/"candidate"/name
            if not after.is_file() or after.is_symlink() or after.stat().st_size > repair.MAX_FILE_BYTES:
                continue
            try:
                evidence[name] = "".join(unified_diff(before.read_text().splitlines(True),
                                                      after.read_text().splitlines(True), "before/"+name, "after/"+name))
            except (OSError, UnicodeError):
                continue
    return [{"case": record["task"], "trial": path.name, "model": exe.get("model"),
             "arm": record.get("prompt_variant", "task-only"), "input": record["provenance"].get("baseline_digest"),
             "prompt": record["provenance"].get("prompt_digest"), "seconds": exe.get("duration_seconds"),
             "tokens": tokens, "cost": None, "checks": score["checks"],
             "outcome": "execution_error" if score["execution_errors"] else ("passed" if score["passed"] else "failed"),
             "details": {"changed_paths": score.get("changed_paths", []), "errors": score["execution_errors"]},
             "evidence": evidence}]


def write_report(out, rows):
    if not rows:
        raise ValueError("no trials to report")
    groups = defaultdict(list)
    for row in rows:
        key = (row["case"], row["input"], row["model"], row["arm"], row["prompt"])
        groups[key].append(row)
    summary = []
    for (case, inputs, model, arm, prompt), group in groups.items():
        times = [r["seconds"] for r in group if isinstance(r["seconds"], (int, float))]
        def numeric(values):
            return [x for x in values if isinstance(x, (int, float)) and not isinstance(x, bool)]
        inputs_used = numeric([r["tokens"].get("input_tokens") for r in group if isinstance(r.get("tokens"), dict)])
        outputs_used = numeric([r["tokens"].get("output_tokens") for r in group if isinstance(r.get("tokens"), dict)])
        costs = numeric([r.get("cost") for r in group])
        summary.append({"case": case, "snapshot_or_golden": inputs, "model": model, "arm": arm, "prompt": prompt,
                        "trials": len(group), "passed": sum(r["outcome"] == "passed" for r in group),
                        "errors": sum(r["outcome"] == "execution_error" for r in group),
                        "format_errors": sum(r["outcome"] == "format_error" for r in group),
                        "support_errors": sum(r["outcome"] == "support_error" for r in group),
                        "seconds_mean": round(mean(times), 3) if times else None,
                        "seconds_range": [min(times), max(times)] if times else None,
                        "mean_input_tokens": round(mean(inputs_used)) if inputs_used else None,
                        "mean_output_tokens": round(mean(outputs_used)) if outputs_used else None,
                        "mean_cost": mean(costs) if costs else None})
    def cell(value):
        return escape(str(value)) if value is not None else "—"
    body = []
    for s in summary:
        keys = ("case", "model", "arm", "passed", "trials", "errors", "seconds_mean", "seconds_range", "mean_input_tokens", "mean_output_tokens", "mean_cost", "format_errors", "support_errors")
        body.append("<tr>" + "".join(f"<td>{cell(s[k])}</td>" for k in keys) + "</tr>")
    trials = []
    for n, row in enumerate(rows, 1):
        links, sources = [], []
        for i, (filename, source) in enumerate(row["evidence"].items()):
            anchor = f"evidence-{n}-{i}"
            links.append(f'<a href="#{anchor}">{cell(filename)}</a>')
            numbered = "\n".join(f'<span id="{anchor}-line-{line_no}">{line_no:4}  {escape(line)}</span>'
                                   for line_no, line in enumerate(source.splitlines(), 1))
            sources.append(f'<details id="{anchor}" open><summary>{cell(filename)}</summary><pre>{numbered}</pre></details>')
            for ref in row.get("references", []):
                if ref.get("file") == filename:
                    links.append(f'<a href="#{anchor}-line-{ref["line"]}">{cell(ref["claim"])}:{ref["line"]}</a>')
        trial_data = json.dumps({k: v for k, v in row.items() if k != "evidence"}, indent=2, ensure_ascii=False)
        trials.append(f'<details><summary>{cell(row["case"])} · {cell(row["arm"])} · {cell(row["outcome"])}</summary>'
                      + " · ".join(links) + f"<pre>{escape(trial_data)}</pre>" + "".join(sources) + "</details>")
    html = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
<title>Arche evaluation</title><style>body{font:16px/1.5 system-ui;max-width:85rem;margin:3rem auto;padding:0 1rem}
table{border-collapse:collapse}td,th{text-align:left;padding:.5rem;border-bottom:1px solid #bbb}
details{padding:.7rem 0}summary{cursor:pointer}pre{overflow:auto;background:#f3f4f0;padding:1rem}</style>
<h1>Agent evaluation</h1><table><thead><tr>"""
    headers = ["Task", "Model", "Configuration", "Passed", "Trials", "Execution errors", "Mean seconds",
               "Seconds range", "Mean input tokens", "Mean output tokens", "Mean cost", "Format errors", "Support errors"]
    html += "".join(f"<th>{h}</th>" for h in headers) + "</tr></thead><tbody>" + "".join(body)
    html += "</tbody></table><h2>Trial evidence</h2>" + "".join(trials)
    out.mkdir(parents=True, exist_ok=False)
    (out/"report.html").write_text(html, encoding="utf-8")
    (out/"comparison.json").write_text(json.dumps({"schema_version": 1, "summary": summary, "trials": rows},
                                                   ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
