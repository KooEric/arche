from . import db
from .. import models
from ..config import SETTINGS

_orders = {}


def remember(order):
    _orders[order.order_id] = order
    return models.Order, db.connect, SETTINGS
