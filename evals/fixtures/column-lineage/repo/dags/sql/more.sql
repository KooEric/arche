-- variable schema: the table lineage matches it by table name only
INSERT INTO {{ params.schema }}.ledger (entry_id, amount)
SELECT o.order_id, o.amount FROM stage.orders o;

SELECT l.entry_id FROM {{ params.schema }}.ledger l;

MERGE INTO mart.balances b USING stage.orders o ON b.cust_id = o.cust_id
WHEN MATCHED AND o.status = 'paid' THEN UPDATE SET balance = b.balance + o.amount
WHEN NOT MATCHED THEN INSERT (cust_id, balance) VALUES (o.cust_id, o.amount);

FROM stage.orders o
INSERT OVERWRITE TABLE mart.paid_ids SELECT o.order_id WHERE o.status = 'paid'
INSERT OVERWRITE TABLE mart.open_ids SELECT o.order_id WHERE o.status = 'open';

CREATE VIEW mart.orders_slim AS SELECT * EXCEPT (ts, status) FROM stage.orders;

SELECT c.id, (SELECT MAX(o.amount) FROM stage.orders o WHERE o.cust_id = c.id) AS top_amount
FROM stage.customers c;

WITH RECURSIVE days (d) AS (SELECT 1 UNION ALL SELECT d + 1 FROM days WHERE d < 7)
SELECT d FROM days;
