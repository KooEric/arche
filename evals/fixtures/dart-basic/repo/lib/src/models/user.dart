class User {
  const User(this.name);

  final String name;
}

mixin Named {
  String get displayName => 'user';
}

extension UserX on User {
  String get initial => name.isEmpty ? '' : name[0];
}
