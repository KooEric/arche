from shop.models import Order
from shop import config
import shop.storage.db as db
from .storage import cache


def place_order(order_id, total):
    order = Order(order_id, total)
    db.connect(config.SETTINGS["db_path"])
    cache.remember(order)
    return order
