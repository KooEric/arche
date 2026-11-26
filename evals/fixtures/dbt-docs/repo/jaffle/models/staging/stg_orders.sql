select id as order_id, customer_id, status, amount
from {{ source('shop', 'orders') }}
