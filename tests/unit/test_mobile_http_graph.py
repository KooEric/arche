from arche_lib.graph import build_graph
from arche_lib.inventory import build_inventory
from arche_lib.sources import make_reader


def test_connects_registered_handlers_by_method_and_literal_path(make_repo):
    mobile = """
Future<void> load() async {
  final uri = Uri.parse('${ApiConstants.baseUrl}/items/version');
  final response = await _client.get(uri);
}
"""
    worker = """
const versionRoute = createRoute({
  method: "get",
  path: "/items/version",
});
app.openapi(versionRoute, async (c) => c.json({}));
"""
    files = {"mobile/client.dart": mobile, "worker/api.ts": worker}
    for label, routes, status, reason, count in (
        ("one", files, "inferred", "method_and_path", 1),
        ("wrong", {**files, "worker/api.ts": worker.replace('"get"', '"post"')},
         "unresolved", "no_literal_handler", 0),
        ("two", {**files, "worker/duplicate.ts": worker}, "unresolved", "multiple_handlers", 2),
    ):
        inventory = build_inventory(make_repo(routes, name=label, use_git=False))
        graph = build_graph(inventory, make_reader(inventory))
        connection, = graph["http_connections"]
        assert (connection["status"], connection["reason"]) == (status, reason)
        assert len(connection["candidates"]) == count
        assert connection["request"]["line"] == 4
        for route in connection["candidates"]:
            assert route["handler_line"] == 6
