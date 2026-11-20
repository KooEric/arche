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


def test_an_arrow_bodied_helper_is_not_paired_with_the_next_functions_block():
    source = r"""
class Api {
  Future<void> a() async {
    await _get('/a');
    await _post('/b');
  }

  Future<void> _get(String path) => _client.get(Uri.parse('${base}$path'));

  Future<void> _post(String path) async {
    final uri = Uri.parse('${base}$path');
    await _client.post(uri);
  }
}
"""
    assert [(r["method"], r["path"]) for r in requests("client.dart", source)] == [
        ("GET", "/a"),
        ("POST", "/b"),
    ]


def test_an_abstract_helper_declaration_is_not_paired_with_the_next_block():
    source = r"""
abstract class Api {
  Future<void> a() async {
    await _get('/a');
  }

  Future<void> _get(String path);

  Future<void> _post(String path) async {
    final uri = Uri.parse('${base}$path');
    await _client.post(uri);
  }
}
"""
    assert requests("client.dart", source) == []


def test_a_helper_call_with_a_concatenated_path_is_not_read():
    source = r"""
class Api {
  Future<void> a(String id) async {
    await _get('/items/' + id);
    await _get('/items', {});
  }

  Future<void> _get(String path, [Map<String, String>? query]) async {
    final uri = Uri.parse('${base}$path');
    await _client.get(uri);
  }
}
"""
    assert [(r["method"], r["path"], r["line"]) for r in requests("client.dart", source)] == [
        ("GET", "/items", 5)]


def test_a_helper_name_declared_twice_is_not_used():
    source = r"""
class A {
  Future<void> run() async {
    await _send('/items');
  }

  Future<void> _send(String path) async {
    await _client.get(Uri.parse('${base}$path'));
  }
}

class B {
  Future<void> _send(String path) async {
    await _client.post(Uri.parse('${base}$path'));
  }
}
"""
    assert requests("client.dart", source) == []


def test_many_helpers_are_read_in_linear_time():
    import time
    helper = "  Future<void> _h{0}(String path) async {{\n    await _client.get(Uri.parse('${{base}}$path'));\n  }}\n"
    source = "class Api {\n" + "".join(helper.format(i) for i in range(2000)) + "}\n"
    started = time.perf_counter()
    requests("client.dart", source)
    assert time.perf_counter() - started < 2
