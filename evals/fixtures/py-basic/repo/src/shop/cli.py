import argparse

from shop.service import place_order
from shop.storage import db, cache

import requests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("total", type=float)
    args = parser.parse_args()
    order = place_order(1, args.total)
    requests.post("https://example.invalid/orders", json={"id": order.order_id})
    return db, cache
