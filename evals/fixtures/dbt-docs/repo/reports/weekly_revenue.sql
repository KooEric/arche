-- Read by the BI team outside dbt.
SELECT r.customer_id, r.revenue, o.status
FROM analytics_finance.revenue r
JOIN analytics_marts.orders_v2 o ON o.customer_id = r.customer_id
JOIN lake.raw_data.ev_v2 e ON e.customer_id = r.customer_id;
