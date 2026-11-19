import 'package:flutter/material.dart';

import 'models/order.dart';
import '../src/services/order_service.dart' hide internalHelper;

class ShopApp extends StatelessWidget {
  const ShopApp({super.key, required this.label});

  final String label;

  @override
  Widget build(BuildContext context) {
    final Order order = Order(orderCount);
    return Text('$label ${order.id}');
  }
}
