CREATE TABLE mart.revenue AS
SELECT event_date, SUM(amount) AS revenue
FROM accounting.ledger_daily
GROUP BY event_date;
