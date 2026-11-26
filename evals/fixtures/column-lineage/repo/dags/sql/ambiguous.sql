CREATE TABLE mart.mixed AS
SELECT region, extra, o.amount, id
FROM stage.customers c JOIN raw.people p ON p.id = c.id JOIN stage.orders o ON o.cust_id = c.id;
