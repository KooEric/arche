"""report.py stays under the 800-line file limit; new report sections go in their own modules."""
from pathlib import Path

REPORT = Path(__file__).resolve().parents[2] / "skills/arche/scripts/arche_lib/report.py"
MAX_LINES = 800


def test_report_module_stays_under_the_line_limit():
    assert len(REPORT.read_text(encoding="utf-8").splitlines()) < MAX_LINES
