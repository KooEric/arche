from arche_lib.langs.dart_http import requests


def test_local_uri_multiline_and_call_line():
    source = """class Api {
  Future<void> sync() async {
    final uri = Uri.parse(
      '${ApiConstants.baseUrl}/example/version',
    );
    await client.get(
      uri, headers: headers,
    );
  }
}
"""
    assert requests('lib/api.dart', source) == [
        {'file': 'lib/api.dart', 'line': 6, 'method': 'GET',
         'path': '/example/version', 'dynamic': False}]


def test_templates_and_direct_uri():
    source = '''void f() {
      client.post(Uri.parse('${baseUrl}/users/$id/items/${item.id}'));
      client.delete(Uri.parse('https://example.com/users?query=1'));
    }'''
    assert [(r['method'], r['path'], r['dynamic']) for r in requests('a', source)] == [
        ('POST', '/users/$id/items/${item.id}', True), ('DELETE', '/users', False)]


def test_comments_strings_and_scope_do_not_leak():
    source = '''class A {
      void one() {
        // client.get(Uri.parse('/comment'));
        final example = "client.get(Uri.parse('/fake'))";
        final uri = Uri.parse('/one');
        { final uri = Uri.parse('/inner'); client.get(uri); }
        client.get(uri);
      }
      void two() { client.get(uri); }
    }'''
    assert [r['path'] for r in requests('a', source)] == ['/inner', '/one']


def test_reassignment_invalidates_and_unknown_uri_is_ignored():
    source = '''void f() {
      var uri = Uri.parse('/known');
      client.get(uri);
      uri = makeUri();
      client.get(uri);
      client.get(Uri.parse(other));
    }'''
    assert [r['path'] for r in requests('a', source)] == ['/known']



def test_malformed_uri_does_not_hide_subsequent_requests():
    source = """void fetch() {
      client.get(Uri.parse('https://[invalid/path'));
      client
          .get(Uri.parse('/valid'));
    }"""
    assert [(r['line'], r['path']) for r in requests('api.dart', source)] == [(4, '/valid')]
