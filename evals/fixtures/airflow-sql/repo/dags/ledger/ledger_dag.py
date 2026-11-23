from airflow import DAG
from airflow.providers.apache.hive.operators.hive import HiveOperator

with DAG("ledger") as dag:
    close = HiveOperator(task_id="close", hql="close.sql")
    report = HiveOperator(task_id="report", hql="report.sql")
    settle = HiveOperator(task_id="settle", hql="/opt/airflow/dags/billing/sql/settle.hql")
    close >> report >> settle
