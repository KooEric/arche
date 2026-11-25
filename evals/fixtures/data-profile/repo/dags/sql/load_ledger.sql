INSERT OVERWRITE TABLE accounting.ledger_daily
SELECT c.customer_id, e.amount, e.event_date
FROM raw.events e
JOIN crm.customers c ON c.customer_id = e.customer_id;
