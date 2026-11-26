select p.order_id, sum(p.amount) as paid, count(distinct p.payment_id) filter (where p.currency = 'usd') as n
from {{ ref('stg_pay') }} p
group by 1
