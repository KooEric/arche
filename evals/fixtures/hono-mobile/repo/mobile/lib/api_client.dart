import 'package:http/http.dart' as http;

import 'config.dart';

class ApiClient {
  ApiClient(this._client);

  final http.Client _client;

  Future<http.Response> version() async {
    final uri = Uri.parse('${Config.baseUrl}/catalog/version');
    return _client.get(uri);
  }

  Future<http.Response> orders() => _getJson('/orders');

  Future<http.Response> order(String id) => _getJson('/orders/$id');

  Future<http.Response> login(String provider) => _postJson('/auth/$provider', {});

  Future<http.Response> track() => _postJson('/events', {});

  Future<http.Response> _getJson(String path) async {
    final uri = Uri.parse('${Config.baseUrl}$path');
    return _client.get(uri);
  }

  Future<http.Response> _postJson(String path, Map<String, dynamic> body) async {
    final uri = Uri.parse('${Config.baseUrl}$path');
    return _client.post(uri, body: body.toString());
  }
}
