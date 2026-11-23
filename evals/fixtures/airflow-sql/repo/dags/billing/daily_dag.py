"""Daily billing load."""
import os
from pathlib import Path

from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

from common.readers import read_sql

BASE = os.path.dirname(__file__)
TABLES = ["orders", "refunds"]

with DAG("billing_daily") as dag:
    load = SQLExecuteQueryOperator(task_id="load", sql="sql/load_orders.sql")
    settle = SQLExecuteQueryOperator(task_id="settle", sql=open(os.path.join(BASE, "sql", "settle.hql")).read())
    close = SQLExecuteQueryOperator(task_id="close", sql=read_sql("dags/ledger/sql/close.sql"))
    audit = SQLExecuteQueryOperator(task_id="audit", sql=(Path(BASE) / "sql" / "audit.sql").read_text())
    for table in TABLES:
        SQLExecuteQueryOperator(task_id=f"stage_{table}", sql=f"sql/stage_{table}.sql")
    load >> settle >> close >> audit
