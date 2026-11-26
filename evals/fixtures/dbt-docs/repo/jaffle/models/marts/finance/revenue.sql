select customer_id, sum(amount) as revenue
from {{ ref('orders', v=2) }}
group by 1
