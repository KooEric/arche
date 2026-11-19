from arche_lib.langs.ts_http import routes


def test_registered_multiline_route_has_declaration_and_registration_evidence():
    source = '''const route = createRoute({
  method: "get",
  request: { query: schema({ method: "post", path: "/wrong" }) },
  path: "/example/version",
});
app.openapi(
  route,
  handler,
);
'''
    assert routes("worker.ts", source) == [{
        "file": "worker.ts", "line": 1, "handler_line": 6,
        "method": "GET", "path": "/example/version", "dynamic": False,
    }]


def test_unregistered_and_examples_in_comments_or_strings_are_ignored():
    source = '''// const fake = createRoute({method: "get", path: "/fake"});
// app.openapi(fake, handler);
const example = 'app.openapi(real, handler)';
const unregistered = createRoute({method: 'get', path: '/unused'});
const real = createRoute({method: 'post', path: '/real'});
const text = `app.openapi(real, handler)`;
'''
    assert routes("worker.ts", source) == []


def test_same_path_distinct_methods_and_multiple_candidates_are_preserved():
    source = '''const first = createRoute({method: 'get', path: '/items'});
const second = createRoute({method: 'get', path: '/items'});
const other = createRoute({method: 'post', path: '/items'});
app.openapi(first, firstHandler);
app.openapi(second, secondHandler);
app.openapi(other, otherHandler);
'''
    found = routes("worker.ts", source)
    assert [(r["method"], r["path"], r["line"], r["handler_line"]) for r in found] == [
        ("GET", "/items", 1, 4), ("GET", "/items", 2, 5),
        ("POST", "/items", 3, 6),
    ]
    # A GET request must never match the POST candidate.
    assert [r["line"] for r in found if (r["method"], r["path"]) == ("GET", "/items")] == [1, 2]


def test_dynamic_path_is_marked_and_missing_handler_is_not_registered():
    source = '''const route = createRoute({method: 'get', path: `/items/${id}`});
const missing = createRoute({method: 'get', path: '/missing'});
app.openapi(route, handler);
app.openapi(missing, );
'''
    found = routes("worker.ts", source)
    assert len(found) == 1
    assert found[0]["path"] == "/items/${id}"
    assert found[0]["dynamic"] is True


def test_ambiguous_redeclarations_and_nonliteral_fields_are_ignored():
    source = '''const dup = createRoute({method: 'get', path: '/one'});
const dup = createRoute({method: 'get', path: '/two'});
const computed = createRoute({method: VERB, path: '/three'});
const expression = createRoute({method: 'get', path: '/four' + suffix});
app.openapi(dup, handler);
app.openapi(computed, handler);
app.openapi(expression, handler);
'''
    assert routes("worker.ts", source) == []


def test_route_scope_must_be_visible_at_registration():
    source = '''function hidden() {
  const route = createRoute({method: 'get', path: '/hidden'});
}
app.openapi(route, handler);
function local() {
  const localRoute = createRoute({method: 'get', path: '/local'});
  app.openapi(localRoute, handler);
}
'''
    assert [r["path"] for r in routes("worker.ts", source)] == ["/local"]
