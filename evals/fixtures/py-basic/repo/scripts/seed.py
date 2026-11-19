import helpers
from helpers import make_order
import shop.service


def run():
    order = make_order(7)
    shop.service.place_order(order.order_id, order.total)
    return helpers
