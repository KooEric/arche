import score_columns


def test_the_column_fixture_meets_its_answer_file():
    assert score_columns.main([]) == 0


def test_an_inferred_answer_reported_as_verified_lowers_precision():
    expected = [["col:a.x", "col:b.x", "direct"]]
    got = [["col:a.x", "col:b.x", "direct"], ["col:a.y", "col:b.y", "direct"]]
    precision, recall, passed = score_columns.gate(got, expected, 0.98, 0.90)
    assert (precision, recall, passed) == (0.5, 1.0, False)


def test_a_missing_verified_edge_lowers_recall():
    expected = [["col:a.x", "col:b.x", "direct"], ["col:a.y", "col:b.y", "rename"]]
    precision, recall, passed = score_columns.gate(expected[:1], expected, 0.98, 0.90)
    assert (precision, recall, passed) == (1.0, 0.5, False)


def test_differences_name_missing_and_unexpected_rows():
    found = score_columns.differences({"inferred": [["b"]], "truncated": True},
                                      {"inferred": [["a"]], "truncated": False})
    assert found == ["inferred: missing [['a']], unexpected [['b']]", "truncated: expected False, got True"]


def test_observed_rows_are_sorted_and_carry_the_role():
    columns = {"edges": [
        {"from": "col:t.b", "to": "col:u.c", "kind": "transform", "role": "condition", "status": "verified"},
        {"from": None, "to": "col:u.a", "kind": "constant", "status": "verified"},
        {"from": "col:t.a", "to": "col:u.a", "kind": "direct", "status": "inferred", "rule": "star"}],
        "unresolved": [], "statements": [], "column_sources_disagree": []}
    view = score_columns.observed(columns)
    assert view["verified"] == [[None, "col:u.a", "constant"], ["col:t.b", "col:u.c", "transform", "condition"]]
    assert view["inferred"] == [["col:t.a", "col:u.a", "direct", "star"]]
