select customer_id, name
from {{ ref('stg_customers') }}
