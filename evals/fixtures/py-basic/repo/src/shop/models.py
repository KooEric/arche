from dataclasses import dataclass


@dataclass
class Order:
    order_id: int
    total: float
