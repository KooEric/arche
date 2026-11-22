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


def test_a_url_literal_followed_by_concatenation_is_not_read_as_the_whole_url():
    source = '''fetch("/items/" + id);
axios.get("/users/" + id + "/posts");
fetch("/ok", { method: "POST" });
axios.get("/fine", config);
'''
    assert [(r["method"], r["path"]) for r in requests("client.ts", source)] == [
        ("POST", "/ok"), ("GET", "/fine")]


def test_a_fetch_whose_method_cannot_be_read_is_dropped():
    source = '''fetch("/a", opts);
fetch("/b", { method });
fetch("/c", { method: verb });
fetch("/d", { method: post ? "POST" : "GET" });
fetch("/e", { ...base });
fetch("/f", { headers: { method: "X" } });
fetch("/g", { body, method: "put", });
fetch("/h", {});
fetch("/i",);
'''
    assert [(r["method"], r["path"]) for r in requests("client.ts", source)] == [
        ("GET", "/f"), ("PUT", "/g"), ("GET", "/h"), ("GET", "/i")]


def test_many_unclosed_fetch_calls_are_read_in_linear_time():
    import time
    started = time.perf_counter()
    assert requests("client.ts", "fetch(" * 8000) == []
    assert requests("client.ts", 'fetch("/x", {' * 8000) == []
    assert time.perf_counter() - started < 2
