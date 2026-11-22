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


def test_request_path_reads_a_protocol_relative_url_as_an_outside_host():
    assert request_path("//cdn.example.com/x") == ("/x", False, "cdn.example.com")


def test_request_path_reads_an_interpolated_host_as_unknown():
    assert request_path("https://$host/x") == ("/x", False, None)
    assert request_path("https://${host}/items/${id}") == ("/items/${id}", True, None)
    assert request_path("https://api.${domain}/x") == ("/x", False, None)


def test_connect_never_matches_a_dynamic_route():
    route = {**_route("GET", "/items/${kind}"), "dynamic": True}
    connection, = connect([_request("GET", "/items/books")], [route])
    assert (connection["status"], connection["reason"], connection["candidates"]) == (
        "unresolved", "no_literal_handler", [])


def test_connect_prefers_routes_whose_prefix_is_known():
    unknown = {**_route("GET", "/items/:id", 1), "prefix_unknown": True}
    connection, = connect([_request("GET", "/items/7")], [unknown, _route("GET", "/items/:id", 2)])
    assert (connection["status"], connection["reason"]) == ("inferred", "method_and_path_pattern")
    assert [route["line"] for route in connection["candidates"]] == [2]


def test_connect_does_not_infer_from_routes_whose_prefix_is_unknown():
    unknown = {**_route("GET", "/health", 1), "prefix_unknown": True}
    connection, = connect([_request("GET", "/health")], [unknown])
    assert (connection["status"], connection["reason"], connection["candidates"]) == (
        "unresolved", "route_prefix_unknown", [unknown])


def test_connect_does_not_infer_from_a_route_made_only_of_variables():
    for path in ("/:id", "/{x}", "/*", "/:a/<b>"):
        route = _route("GET", path)
        connection, = connect([_request("GET", "/" + "/".join(["v"] * (path.count("/"))))], [route])
        assert (connection["status"], connection["reason"], connection["candidates"]) == (
            "unresolved", "variable_only_route", [route])


def test_connect_counts_all_routes_once_for_an_all_request():
    connection, = connect([_request("ALL", "/health")], [_route("ALL", "/health")])
    assert (connection["status"], len(connection["candidates"])) == ("inferred", 1)
