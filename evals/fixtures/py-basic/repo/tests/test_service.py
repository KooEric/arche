import pytest

from shop import service
from shop.service import place_order


def test_place_order():
    assert place_order(1, 2.0).order_id == 1
    assert service is not None
    assert pytest is not None
