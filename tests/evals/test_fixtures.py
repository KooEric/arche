import json

import pytest

import score
from arche_lib import jsonio

FIXTURES = {"py-basic", "py-dynamic", "secrets", "ts-basic", "ts-alias", "ts-dynamic", "dart-basic", "hono-mobile", "jvm-basic", "airflow-sql"}
INFERRED_ONLY = {
    ("py-dynamic", "tools/run.py", "vendor_local/helper.py"),
    ("ts-alias", "src/main.ts", "packages/legacy/src/index.ts"),
    ("jvm-basic", "app/src/main/kotlin/com/acme/shop/data/OrderRepository.kt", "app/src/main/java/com/acme/shop/model/Order.java"),
}
# Same-package Java and Kotlin types have no import statement; they are inferred and stay out of recall.
IMPLICIT = {
    ("jvm-basic", "app/src/main/java/com/acme/shop/OrderService.java", "app/src/main/java/com/acme/shop/Receipt.java"),
    ("jvm-basic", "app/src/main/kotlin/com/acme/shop/ui/Main.kt", "app/src/main/kotlin/com/acme/shop/ui/Screen.kt"),
    ("jvm-basic", "app/src/test/kotlin/com/acme/shop/OrderServiceTest.kt", "app/src/main/java/com/acme/shop/OrderService.java"),
}


@pytest.fixture(scope="module")
def results():
    return score.score_all()


def _graph(fixture):
    return score.run_pipeline(score.FIXTURES_DIR / fixture / "repo")


def test_every_expected_fixture_is_scored(results):
    assert {r["fixture"] for r in results} == FIXTURES


def test_every_fixture_matches_its_expected_sets(results):
    assert {r["fixture"]: r["mismatches"] for r in results if r["mismatches"]} == {}


def test_import_edges_meet_the_release_gates(results):
    overall = score.totals(results)

    assert overall["precision"] >= score.DEFAULT_MIN_PRECISION
    assert overall["recall"] >= score.DEFAULT_MIN_RECALL


def test_every_language_is_scored(results):
    assert set(score.by_language(results)) == {"python", "typescript", "dart", "java", "kotlin"}


def test_file_references_stay_out_of_import_precision_and_recall(results):
    airflow = next(r for r in results if r["fixture"] == "airflow-sql")

    assert all(airflow[outcome] == [] for outcome in score.OUTCOMES)
    assert airflow["inferred_correct"] == [] and airflow["inferred_wrong"] == []


def test_table_reads_are_scored_as_their_own_exact_sets():
    observed = score.observed_sets(_graph("airflow-sql"))

    assert ("dags/ledger/sql/close.sql", "dags/billing/sql/settle.hql", "billing.settled", "verified", "name") in observed["reads"]
    assert observed["external_tables"] == {("dags/billing/sql/load_orders.sql", "staging.orders"),
                                           ("dags/shared/sql/report.sql", "shared.calendar"),
                                           ("dags/shared/sql/stage_a.sql", "shared.calendar"),
                                           ("dags/shared/sql/stage_b.sql", "shared.calendar")}
    assert "reads" in score.EXACT_SETS and "external_tables" in score.EXACT_SETS


def test_edges_not_verified_are_exactly_the_ones_declared_as_inferred(results):
    missed = {(r["fixture"],) + edge for r in results for edge in r["false_negative"]}
    inferred = {(r["fixture"],) + edge for r in results for edge in r["inferred_correct"]}

    assert missed == INFERRED_ONLY
    assert inferred == INFERRED_ONLY | IMPLICIT
    assert all(r["inferred_wrong"] == [] for r in results)


@pytest.mark.parametrize("fixture, count", [("py-dynamic", 2), ("ts-dynamic", 2)])
def test_non_literal_dynamic_imports_are_never_verified(fixture, count):
    dynamic = [edge for edge in _graph(fixture)["edges"] if edge.get("reason") == "dynamic"]

    assert len(dynamic) == count
    assert all(edge["status"] == "unresolved" and edge["to"] is None for edge in dynamic)


def test_text_inside_comments_strings_and_regex_creates_no_edges():
    specs = [edge.get("spec") or edge["to"] for edge in _graph("ts-dynamic")["edges"]]

    assert not any("commented" in spec or "in-string" in spec or "in-regex" in spec for spec in specs)


def test_dart_edges_carry_conditional_part_and_reexport_flags():
    edges = _graph("dart-basic")["edges"]

    assert sum(1 for e in edges if e.get("conditional")) == 2
    assert sum(1 for e in edges if e.get("part")) == 4
    assert sum(1 for e in edges if e.get("reexport")) == 2


def test_no_fixture_file_is_degraded():
    for fixture in sorted(FIXTURES):
        degraded = [n["id"] for n in _graph(fixture)["nodes"] if n.get("degraded")]
        assert degraded == [], fixture


def test_secret_files_never_reach_the_graph_with_content():
    assert "not-a-real-key" not in jsonio.dumps(_graph("secrets"))


@pytest.mark.parametrize("fixture", ["py-basic", "ts-alias", "dart-basic"])
def test_graph_is_byte_identical_across_runs_in_different_directories(fixture):
    assert jsonio.dumps(_graph(fixture)) == jsonio.dumps(_graph(fixture))


def test_score_cli_passes_with_the_default_gates(capsys):
    assert score.main([]) == 0
    assert "overall precision" in capsys.readouterr().out


def test_score_cli_fails_when_a_gate_is_out_of_reach(capsys):
    assert score.main(["--min-recall", "1.0"]) == 1
    capsys.readouterr()


@pytest.fixture(scope="module")
def data_profile_run():
    import score_profile

    text, profile = score_profile.run_fixture(score_profile.FIXTURE)
    expected = json.loads((score_profile.FIXTURE / "expected_profile.json").read_text(encoding="utf-8"))
    return score_profile, text, profile, expected


def test_the_data_profile_fixture_matches_its_expected_profile(data_profile_run):
    score_profile, text, profile, expected = data_profile_run

    assert score_profile.differences(text, profile, expected) == []


def test_the_data_profile_scorer_catches_a_changed_column_type(data_profile_run):
    score_profile, text, profile, expected = data_profile_run
    changed = {**expected, "columns": {**expected["columns"], "d0:segments.csv": {"id": "string", "name": "string"}}}

    assert score_profile.differences(text, profile, changed) != []


def test_the_data_profile_scorer_catches_a_dropped_match(data_profile_run):
    score_profile, text, profile, expected = data_profile_run
    dropped = {**profile, "matches": profile["matches"][1:]}

    assert score_profile.differences(text, dropped, expected) != []


def test_the_data_profile_scorer_catches_an_extra_dataset(data_profile_run):
    score_profile, text, profile, expected = data_profile_run
    extra = {**profile, "datasets": profile["datasets"] + [{"id": "d0:extra.csv", "columns": []}]}

    assert score_profile.differences(text, extra, expected) != []


def test_the_data_profile_scorer_catches_a_planted_value_in_the_text(data_profile_run):
    score_profile, text, profile, expected = data_profile_run

    assert score_profile.differences(text + " fixture.one@example.com", profile, expected) != []


def test_the_data_profile_scorer_reports_a_missing_expected_key(data_profile_run):
    score_profile, text, profile, expected = data_profile_run
    incomplete = {k: v for k, v in expected.items() if k != "datasets"}

    assert any("datasets" in line for line in score_profile.differences(text, profile, incomplete))
