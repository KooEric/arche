import 'package:flutter_test/flutter_test.dart';
import 'package:shop_app/src/models/order.dart';

import '../lib/src/models/user.dart';

void main() {
  test('order keeps its owner', () {
    expect(Order(1, const User('a')).owner?.name, 'a');
  });
}
