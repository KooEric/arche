library order_service;

import 'package:shop_app/src/models/models.dart';

part 'order_cache.dart';

final orderServiceProvider = OrderService();

const _limit = 10;

class OrderService {
  final _OrderCache _cache = _OrderCache();

  Order? find(int id) => _cache.orders[id];
}

String internalHelper() => 'limit: $_limit';

Future<Order> fetchOrder(int id) async {
  return Order(id);
}

int get orderCount => _limit;
