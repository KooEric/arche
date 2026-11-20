from arche_lib.langs.dart_http import requests

CLIENT = r"""
class ApiClient {
  Future<List<Item>> items() async {
    final data = await _getJson('/items', {}, authenticated: true);
    return parse(data);
  }

  Future<Item> item(String id) async {
    final data = await _getJson(
      '/items/$id/detail',
      {},
      authenticated: true,
    );
    return Item.fromJson(data);
  }

  Future<void> save(Map<String, dynamic> body) => _postJson('/items', body);

  Future<Map<String, dynamic>> _getJson(
    String path,
    Map<String, String> params, {
    required bool authenticated,
  }) async {
    final uri = Uri.parse(
      '${ApiConstants.baseUrl}$path',
    ).replace(queryParameters: params);
    return _send(() => _client.get(uri, headers: _headers));
  }

  Future<Map<String, dynamic>> _postJson(String path, Map<String, dynamic> body) async {
    final uri = Uri.parse('${ApiConstants.baseUrl}$path');
    return _send(() => _client.post(uri, body: jsonEncode(body)));
  }

  String _label(String path) {
    return 'path: $path';
  }
}
"""


def test_reads_calls_to_helpers_that_forward_their_path_argument():
    assert [(r["method"], r["path"], r["line"], r["dynamic"]) for r in requests("client.dart", CLIENT)] == [
        ("GET", "/items", 4, False),
        ("GET", "/items/$id/detail", 9, True),
        ("POST", "/items", 17, False),
    ]


def test_a_helper_that_makes_two_kinds_of_request_is_not_guessed():
    source = r"""
class Api {
  Future<void> run() async {
    await _send('/items');
  }

  Future<void> _send(String path) async {
    final uri = Uri.parse('${base}$path');
    if (cached) {
      await _client.get(uri);
    } else {
      await _client.post(uri);
    }
  }
}
"""
    assert requests("client.dart", source) == []
