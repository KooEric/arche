WITH orders AS (            -- shadows the real table name
  SELECT order_id, cust_id, amount, status FROM stage.orders
),
paid AS (
  SELECT o.cust_id, o.amount AS paid_amount FROM orders o WHERE o.status = 'paid'
),
by_customer AS (
  SELECT cust_id, SUM(paid_amount) AS total FROM paid GROUP BY cust_id
)
INSERT OVERWRITE TABLE mart.revenue PARTITION (dt = '{{ ds }}')
SELECT b.cust_id AS customer_key,
       b.total - COALESCE(r.refunded, 0) AS net_amount,
       c.region,
       CASE WHEN b.total > 100 THEN 'big' ELSE NULL END AS size_class,
       ROW_NUMBER() OVER (PARTITION BY c.region ORDER BY b.total DESC) AS rank_in_region
FROM by_customer b
JOIN stage.customers c ON c.id = b.cust_id
LEFT JOIN (SELECT cust_id, SUM(amount) AS refunded FROM refunds.daily GROUP BY cust_id) r
  ON r.cust_id = b.cust_id;
