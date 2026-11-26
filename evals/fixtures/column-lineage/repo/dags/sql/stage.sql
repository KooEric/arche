-- A: CTAS from an external table, with a rename and a filter
CREATE TABLE stage.orders AS
SELECT o.id AS order_id, o.cust_id, o.amt AS amount, o.status, o.ts
FROM raw.orders o
WHERE o.deleted = false;

CREATE TABLE stage.customers (id BIGINT, region STRING, name STRING);

CREATE TABLE stage.events AS SELECT * FROM raw.events;
