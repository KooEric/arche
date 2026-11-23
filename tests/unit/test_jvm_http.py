from arche_lib.langs.jvm import extract
from arche_lib.langs.jvm_http import routes
from test_graph_multilang import _graph


def _found(path, text):
    return [(r["method"], r["path"], r["line"], r.get("prefix_unknown", False)) for r in routes(path, text)]


def test_class_prefix_joins_every_method_mapping_shortcut():
    text = '''package a;
@RestController
@RequestMapping("/api/")
public class Items {
    @GetMapping("/items") public List<Item> all() { return null; }
    @PostMapping("items") Item add() { return null; }
    @PutMapping(path = "/items/{id}") void put() {}
    @DeleteMapping(value = "/items/{id}") void delete() {}
    @PatchMapping({"/items/{id}", "/item/{id}"}) void patch() {}
    @GetMapping void root() {}
}
'''
    assert _found("Items.java", text) == [
        ("GET", "/api/items", 5, False), ("POST", "/api/items", 6, False), ("PUT", "/api/items/{id}", 7, False),
        ("DELETE", "/api/items/{id}", 8, False), ("PATCH", "/api/items/{id}", 9, False), ("PATCH", "/api/item/{id}", 9, False),
        ("GET", "/api", 10, False)]


def test_request_mapping_methods_and_paths_form_every_pair():
    text = '''@Controller
class Forms {
    @RequestMapping(value = {"/a", "/b"}, method = {RequestMethod.GET, RequestMethod.HEAD})
    String a() { return ""; }
    @RequestMapping(path = "/c", method = RequestMethod.POST, produces = "text/plain")
    String c() { return ""; }
    @RequestMapping("/any")
    String any() { return ""; }
}
'''
    assert _found("Forms.java", text) == [
        ("GET", "/a", 3, False), ("HEAD", "/a", 3, False), ("GET", "/b", 3, False), ("HEAD", "/b", 3, False),
        ("POST", "/c", 5, False), ("ALL", "/any", 7, False)]


def test_class_level_method_restriction_applies_to_plain_request_mappings():
    text = '''@RestController
@RequestMapping(value = "/hooks", method = RequestMethod.POST)
class Hooks {
    @RequestMapping("/github") void github() {}
    @GetMapping("/status") String status() { return ""; }
}
'''
    assert _found("Hooks.java", text) == [("POST", "/hooks/github", 4, False), ("GET", "/hooks/status", 5, False)]


def test_kotlin_arrays_and_multiline_annotations():
    text = '''package a
@RestController
@RequestMapping(path = ["/v2"])
class Api(private val service: Service) {
    @GetMapping(value = ["/a", "/b"])
    fun a() = service.a()

    @RequestMapping(
        path = arrayOf("/c"),
        method = [RequestMethod.DELETE],
    )
    suspend fun c() {}

    @PostMapping("/d/$name")
    fun d() {}
}
'''
    assert _found("Api.kt", text) == [("GET", "/v2/a", 5, False), ("GET", "/v2/b", 5, False), ("DELETE", "/v2/c", 8, False)]


def test_comments_strings_constants_and_feign_clients_are_not_routes():
    text = '''@RestController
class Api {
    // @GetMapping("/commented")
    /* @PostMapping("/block") */
    String s = "@GetMapping(\\"/string\\")";
    @GetMapping(Paths.REPORT) String report() { return ""; }
    @GetMapping(BASE + "/x") String x() { return ""; }
    @GetMapping("/real") String real() { return ""; }
}
@FeignClient(name = "billing")
interface Billing {
    @GetMapping("/invoices") String invoices();
}
'''
    assert _found("Api.java", text) == [("GET", "/real", 8, False)]


def test_a_constant_class_prefix_marks_its_routes_prefix_unknown():
    text = '''@RestController
@RequestMapping(Api.BASE)
class Things {
    @GetMapping("/things") String things() { return ""; }
}
'''
    assert _found("Things.java", text) == [("GET", "/things", 4, True)]


def test_nested_class_uses_its_own_mapping_and_handler_line_is_the_method():
    text = '''@RequestMapping("/outer")
class Outer {
    @GetMapping("/o")
    String o() { return ""; }
    @RequestMapping("/inner")
    static class Inner {
        @GetMapping("/i") String i() { return ""; }
    }
    @GetMapping("/after") String after() { return ""; }
}
'''
    result = routes("Outer.java", text)
    assert [(r["method"], r["path"], r["line"]) for r in result] == [
        ("GET", "/outer/o", 3), ("GET", "/inner/i", 7), ("GET", "/outer/after", 9)]
    assert [r["handler_line"] for r in result] == [4, 7, 9]
    assert all(r["file"] == "Outer.java" and r["dynamic"] is False for r in result)


def test_jvm_extract_reports_routes_and_a_spring_route_connects_to_a_request():
    assert extract("A.java", '@RestController\nclass A {\n  @GetMapping("/orders") String o() { return ""; }\n}\n')["http_routes"]
    graph = _graph({
        "server/src/main/java/a/Orders.java": 'package a;\n@RestController\n@RequestMapping("/api")\nclass Orders {\n  @GetMapping("/orders/{id}") String one() { return ""; }\n}\n',
        "web/app.ts": 'export async function load(id: string) { return fetch(`/api/orders/${id}`); }\n',
    })
    connection, = graph["http_connections"]
    assert connection["status"] == "inferred"
    assert connection["candidates"][0]["file"] == "server/src/main/java/a/Orders.java"
