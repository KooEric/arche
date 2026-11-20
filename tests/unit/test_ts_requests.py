from arche_lib.langs.ts_requests import requests


def test_reads_fetch_calls_with_the_method_from_their_options():
    source = '''await fetch(`${BASE}/items/${id}`, {
  method: "DELETE",
  headers,
});
fetch("/health");
fetch(url);
// fetch("/commented");
'''
    assert requests("client.ts", source) == [
        {"file": "client.ts", "line": 1, "method": "DELETE", "path": "/items/${id}", "dynamic": True},
        {"file": "client.ts", "line": 5, "method": "GET", "path": "/health", "dynamic": False},
    ]


def test_a_method_in_a_later_statement_is_not_taken_for_this_fetch():
    source = 'fetch("/first");\nconst options = { method: "POST" };\n'
    assert requests("client.ts", source)[0]["method"] == "GET"


def test_reads_axios_and_clients_made_by_axios_create():
    source = '''const api = axios.create({ baseURL });
axios.get("/users");
api.post<Item>("/items", body);
router.get("/not-a-request", handler);
'''
    assert [(r["method"], r["path"], r["line"]) for r in requests("client.ts", source)] == [
        ("GET", "/users", 2), ("POST", "/items", 3)]


def test_records_the_host_of_an_absolute_url():
    found = requests("auth.ts", 'await fetch("https://kapi.example.com/v2/user/me");\n')
    assert found == [{"file": "auth.ts", "line": 1, "method": "GET", "path": "/v2/user/me",
                      "dynamic": False, "host": "kapi.example.com"}]
