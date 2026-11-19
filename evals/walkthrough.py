"""Run the one-line skill invocation on synthetic repositories or rescore a saved run."""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path

FIXTURES = Path(__file__).with_name("walkthrough-fixtures")
CASES = ("cli", "ui")
PROMPT = "$arche 레포 분석"
REPORT = re.compile(r"\]\(<?(/[^)<>\n]+/report\.html)>?\)")
CITATION = re.compile(r"([\w.-]+\.(?:js|py|html)):(\d+)")

class Report(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sections = set()
        self.section = None
        self.screen_text = []
        self.files = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "section":
            self.section = attrs.get("id")
            self.sections.add(self.section)
        if tag == "details" and "data-file" in attrs:
            self.files.add(attrs["data-file"])

    def handle_endtag(self, tag):
        if tag == "section":
            self.section = None

    def handle_data(self, data):
        if self.section == "screens":
            self.screen_text.append(data)


def digest_files(repo):
    return {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(repo.rglob("*")) if ".git" not in p.relative_to(repo).parts and p.is_file()}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def snapshot(repo):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    return {"head": git("rev-parse", "HEAD"), "status": git("status", "--porcelain"),
            "files": digest_files(repo)}


def capture(name, directory, binary, model):
    directory.mkdir()
    with tempfile.TemporaryDirectory(prefix=f"arche-{name}-") as temp:
        repo = Path(temp).resolve()
        shutil.copytree(FIXTURES / name, repo, dirs_exist_ok=True)
        for args in (["init", "-q"], ["add", "."], ["-c", "user.name=Fixture", "-c",
                     "user.email=fixture@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"]):
            subprocess.run(["git", "-c", "core.hooksPath=/dev/null", *args], cwd=repo, check=True)
        before = snapshot(repo)
        answer = directory / "answer.md"
        with (directory / "trace.jsonl").open("w") as trace, (directory / "stderr.txt").open("w") as stderr:
            try:
                result = subprocess.run([binary, "exec", "--json", "--ephemeral", "--ignore-user-config",
                                         "--sandbox", "workspace-write", "--model", model, "-C", str(repo),
                                         "-o", str(answer), PROMPT], cwd=repo, stdout=trace, stderr=stderr, timeout=300)
                status = result.returncode
            except subprocess.TimeoutExpired:
                status = "timeout"
        match = REPORT.search(answer.read_text()) if answer.exists() else None
        report = Path(match[1]) if match else None
        metadata = {"before": before, "after": snapshot(repo), "exit": status, "model": model,
                    "prompt": PROMPT, "report": None}
        # Only archive a report generated for this isolated fixture.
        if report and report.is_file() and not report.resolve().is_relative_to(repo):
            inv = report.with_name("inventory.json")
            if inv.is_file() and Path(read_json(inv)["repo"]["root"]).resolve() == repo:
                metadata["report"] = str(report)
                for filename in ("report.html", "run.json", "inventory.json"):
                    shutil.copyfile(report.with_name(filename), directory / filename)
        (directory / "capture.json").write_text(json.dumps(metadata, indent=2))


def score(name, directory):
    meta = read_json(directory / "capture.json")
    answer_file = directory / "answer.md"
    answer = answer_file.read_text() if answer_file.exists() else ""
    html = Report()
    if (directory / "report.html").is_file():
        html.feed((directory / "report.html").read_text())
    fixture = FIXTURES / name
    citations = CITATION.findall(answer)
    files = {p.name: len(p.read_text().splitlines()) for p in fixture.iterdir() if p.is_file()}
    required = {"app.js"} if name == "ui" else {"count_tags.py", "tally.py"}
    runfile = directory / "run.json"
    run = read_json(runfile) if runfile.exists() else {}
    checks = {
        "execution": meta["exit"] == 0,
        "input_matches_fixture": meta["before"]["files"] == digest_files(fixture),
        "repository_preserved": meta["before"] == meta["after"] and run.get("readonly_check") == "match",
        "linked_report": meta["report"] in REPORT.findall(answer) and required <= html.files,
        "source_citations": required <= {file for file, _ in citations}
            and all(1 <= int(line) <= files.get(file, 0) for file, line in citations),
    }
    if name == "ui":
        checks["screen_controls"] = all(label in " ".join(html.screen_text) for label in ("수령 처리", "처리 취소"))
        checks["answer_states"] = all(label in answer for label in ("대기", "완료", "취소"))
    else:
        checks["example_io"] = all(fragment in answer for fragment in ("red red blue", "blue 1", "red 2"))
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["run", "score"])
    parser.add_argument("output", type=Path)
    parser.add_argument("--codex", default="codex")
    parser.add_argument("--model", default="gpt-6.1-terra")
    args = parser.parse_args()
    out = args.output.resolve()
    if args.mode == "run":
        out.mkdir(parents=True, exist_ok=False)
        for case in CASES:
            capture(case, out / case, args.codex, args.model)
    scores = {case: score(case, out / case) for case in CASES}
    print(json.dumps(scores, indent=2))
    return int(not all(all(checks.values()) for checks in scores.values()))


if __name__ == "__main__":
    raise SystemExit(main())
