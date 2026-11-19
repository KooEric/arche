from shop.models import Order


def make_order(order_id):
    return Order(order_id, 9.5)
