CREATE VIEW mart.all_amounts AS
SELECT order_id AS id, amount FROM stage.orders
UNION ALL
SELECT r.id, r.amount FROM refunds.daily r
UNION ALL
SELECT 0, 0;
