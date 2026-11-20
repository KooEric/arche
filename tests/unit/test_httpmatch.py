from arche_lib.httpmatch import compare, connect, join_path, request_path, request_record


def _request(method, path, dynamic=False, **extra):
    return {"file": "client.ts", "line": 1, "method": method, "path": path, "dynamic": dynamic, **extra}


def _route(method, path, line=1):
    return {"file": "server.ts", "line": line, "handler_line": line, "method": method,
            "path": path, "dynamic": False}


def test_request_path_reads_one_leading_interpolation_as_an_unknown_host():
    assert request_path("${Api.baseUrl}/items/$id?page=1") == ("/items/$id", True, None)
    assert request_path("$base/items") == ("/items", False, None)


def test_request_path_keeps_the_host_of_a_literal_absolute_url():
    assert request_path("https://api.example.com/v1/messages#top") == ("/v1/messages", False, "api.example.com")
    assert request_record("a.ts", 3, "POST", ("/v1/messages", False, "api.example.com")) == {
        "file": "a.ts", "line": 3, "method": "POST", "path": "/v1/messages",
        "dynamic": False, "host": "api.example.com"}


def test_request_path_rejects_text_without_a_path():
    assert request_path("$path") is None
    assert request_path("items") is None


def test_compare_treats_every_variable_spelling_as_one_segment():
    for route in ("/items/:id", "/items/{id}", "/items/<int:id>", "/items/*"):
        assert compare("/items/42", route) == "pattern"
        assert compare("/items/${id}", route) == "pattern"
    assert compare("/items/42/", "/items/42") == "exact"
    assert compare("/items", "/items/:id") is None


def test_a_request_variable_does_not_match_a_literal_route_segment():
    assert compare("/auth/$provider", "/auth/kakao") is None


def test_join_path_handles_root_routes_and_wildcard_mounts():
    assert join_path("/api", "/") == "/api"
    assert join_path("/api/", "/users") == "/api/users"
    assert join_path("/api/*", "/users") == "/api/users"
    assert join_path("", "/") == "/"


def test_connect_prefers_an_exact_route_over_a_pattern_route():
    connection, = connect([_request("GET", "/items/version")],
                          [_route("GET", "/items/:id", 1), _route("GET", "/items/version", 2)])
    assert (connection["status"], connection["reason"]) == ("inferred", "method_and_path")
    assert [route["line"] for route in connection["candidates"]] == [2]


def test_connect_reports_each_outcome_with_its_reason():
    routes = [_route("GET", "/items/:id"), _route("POST", "/twice", 1), _route("POST", "/twice", 2),
              _route("ALL", "/health")]
    found = connect([
        _request("GET", "/items/${id}", dynamic=True),
        _request("POST", "/twice"),
        _request("DELETE", "/health"),
        _request("GET", "/missing"),
        _request("GET", "/missing/${id}", dynamic=True),
        _request("POST", "/v1/messages", host="api.example.com"),
    ], routes)
    assert [(c["status"], c["reason"], len(c["candidates"])) for c in found] == [
        ("inferred", "method_and_path_pattern", 1),
        ("unresolved", "multiple_handlers", 2),
        ("inferred", "method_and_path", 1),
        ("unresolved", "no_literal_handler", 0),
        ("unresolved", "dynamic_request_path", 0),
        ("unresolved", "external_host", 0),
    ]
