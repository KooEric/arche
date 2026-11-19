import json

from walkthrough import FIXTURES, digest_files, score


def test_rescore_detects_wrong_lines_missing_controls_and_repository_changes(tmp_path):
    snapshot = {"files": digest_files(FIXTURES / "ui"), "status": "", "head": "fixture"}
    report_path = "/tmp/fixture/report.html"
    capture = {"exit": 0, "before": snapshot, "after": dict(snapshot), "report": report_path}
    capture_file = tmp_path / "capture.json"
    capture_file.write_text(json.dumps(capture))
    (tmp_path / "run.json").write_text('{"readonly_check":"match"}')
    answer = tmp_path / "answer.md"
    answer.write_text(f"대기, 완료, 취소 [app.js:9](app.js:9) [HTML]({report_path})")
    html = tmp_path / "report.html"
    html.write_text('<section id="screens">수령 처리 · 처리 취소</section><details data-file="app.js"></details>')
    assert all(score("ui", tmp_path).values())

    answer.write_text(answer.read_text().replace("app.js:9", "app.js:999"))
    capture["after"]["status"] = "modified"
    capture_file.write_text(json.dumps(capture))
    html.write_text('<details data-file="app.js"></details>')
    failed = score("ui", tmp_path)
    assert not failed["source_citations"]
    assert not failed["repository_preserved"]
    assert not failed["screen_controls"]
