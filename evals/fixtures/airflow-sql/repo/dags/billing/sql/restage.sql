CREATE TEMPORARY TABLE tmp_orders AS SELECT * FROM billing.orders;
INSERT INTO billing.orders SELECT * FROM tmp_orders;
INSERT INTO ledger.daily_{{ ds_nodash }} SELECT * FROM tmp_orders;
SELECT COUNT(*) FROM ledger.daily_{{ ds_nodash }};
