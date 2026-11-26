SELECT region, SUM(net_amount) AS net FROM mart.revenue WHERE size_class = 'big' GROUP BY region;
SELECT e.kind, COUNT(*) FROM stage.events e GROUP BY 1;
SELECT * FROM mart.all_amounts;
SELECT * FROM src PIVOT (SUM(x) FOR y IN (1, 2));
SELECT id, amount FROM mart.all_amounts ORDER BY amount;
