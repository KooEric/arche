select order_id, customer_id, status
from {{ ref('stg_orders') }}
