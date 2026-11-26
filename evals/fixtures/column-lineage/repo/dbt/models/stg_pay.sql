{{ config(materialized='view') }}
select
  id as payment_id,
  order_id,
  {% if target.name == 'prod' %} amount / 100 as amount, {% else %} amount, {% endif %}
  {{ var('currency') }} as currency,
from {{ source('stripe', 'payments') }}
