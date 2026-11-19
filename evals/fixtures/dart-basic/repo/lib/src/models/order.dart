import 'user.dart';

part 'order.g.dart';

class Order {
  Order(this.id, [this.owner]);

  final int id;
  final User? owner;
}

enum OrderStatus { open, closed }

typedef OrderId = int;
