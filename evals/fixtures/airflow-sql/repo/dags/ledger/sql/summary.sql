-- FROM ledger.ignored is only a comment
WITH recent AS (
    SELECT account, EXTRACT(DAY FROM closed_at) AS d FROM ledger.closed
)
INSERT INTO ledger.summary
SELECT r.account, o.amount FROM recent r JOIN {{ params.schema }}.orders o ON r.account = o.account
WHERE o.note <> 'FROM text.inside';
