"""graph.json lists every table a SQL file writes, read or not."""
from test_graph_multilang import _graph


def test_written_tables_list_each_file_and_table_once_at_its_first_line():
    graph = _graph({
        "q/load.sql": "INSERT INTO a.orders SELECT 1;\nINSERT INTO a.orders SELECT 2;\n",
        "q/mart.sql": "CREATE TABLE mart.revenue AS SELECT * FROM a.orders;\n",
        "q/tmp.sql": "CREATE TEMP TABLE t AS SELECT 1;\nINSERT INTO a.daily_{{ ds }} SELECT 1;\n",
    })

    assert graph["written_tables"] == [
        {"table": "a.orders", "from": "file:q/load.sql", "line": 1},
        {"table": "mart.revenue", "from": "file:q/mart.sql", "line": 1},
    ]


def test_a_repository_without_sql_has_an_empty_list():
    assert _graph({"a.py": "import os\n"})["written_tables"] == []
