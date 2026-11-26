select event_id, customer_id, occurred_at
from {{ source('raw', 'events') }}
