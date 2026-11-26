CREATE TEMPORARY TABLE tmp_paid AS SELECT cust_id, amount FROM stage.orders WHERE status = 'paid';
INSERT INTO mart.paid (customer, amount) SELECT cust_id, amount * 100 FROM tmp_paid;
INSERT INTO ledger.daily_{{ ds_nodash }} SELECT * FROM tmp_paid;
