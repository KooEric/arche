{{ config(materialized='table') }}

select o.order_id, o.customer_id, o.status, o.amount
from {{ ref('stg_orders') }} o
join {{ ref('customers') }} c on c.customer_id = o.customer_id
