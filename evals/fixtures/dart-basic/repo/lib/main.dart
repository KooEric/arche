import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shop_app/src/app.dart';

import 'src/models/models.dart' as models show Order;
import 'src/platform/storage_stub.dart'
    if (dart.library.io) 'src/platform/storage_io.dart'
    if (dart.library.html) 'src/platform/storage_web.dart';

void main() {
  final models.Order? first = null;
  scheduleMicrotask(() => runApp(ShopApp(label: '${storageKind()} $first')));
}
