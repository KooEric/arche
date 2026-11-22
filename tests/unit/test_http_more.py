from arche_lib.langs.go import routes
from test_graph_multilang import _graph


def test_go_routes_on_known_routers_groups_and_patterns():
    text = '''package main
func main() {
	r := gin.Default()
	r.GET("/health", ok)
	api := r.Group("/api")
	v1 := api.Group("v1")
	v1.POST("/orders/:id", create)
	mux := http.NewServeMux()
	mux.HandleFunc("GET /items/{id}", item)
	http.HandleFunc("/legacy", legacy)
	resp, _ := http.Get("https://example.com/x")
	s := "r.GET(\\"/in-string\\", h)"
}
func register(g *gin.RouterGroup) {
	g.DELETE("/sessions", logout)
}
'''
    found = [(r["method"], r["path"], r["line"], r.get("prefix_unknown", False)) for r in routes("main.go", text)]
    assert found == [("GET", "/health", 4, False), ("POST", "/api/v1/orders/:id", 7, False), ("GET", "/items/{id}", 9, False),
                     ("ALL", "/legacy", 10, False), ("DELETE", "/sessions", 15, True)]


def test_go_route_connects_to_a_typescript_request():
    graph = _graph({
        "go.mod": "module example.com/app\n",
        "server/main.go": 'package main\nfunc main() {\n\tr := gin.Default()\n\tr.GET("/orders", list)\n}\n',
        "web/app.ts": 'export async function load() { return fetch("/orders"); }\n',
    })
    connection = graph["http_connections"][0]
    assert connection["status"] == "inferred" and connection["candidates"][0]["file"] == "server/main.go"


def test_call_on_an_imported_axios_instance_is_a_request_and_other_imports_are_not():
    graph = _graph({
        "web/client.ts": 'import axios from "axios";\nexport const api = axios.create({ baseURL: "/" });\nconst other = axios.create();\nexport default other;\n',
        "web/routes.ts": 'export const router = makeRouter();\n',
        "web/page.ts": 'import { api } from "./client";\nimport fallback from "./client";\nimport { router } from "./routes";\n'
                       'api.get("/orders");\nfallback.post("/events");\nrouter.get("/not-a-request");\nunknown.get("/nope");\n',
    })
    assert [(c["request"]["method"], c["request"]["path"], c["request"]["line"]) for c in graph["http_connections"]] == [
        ("GET", "/orders", 4), ("POST", "/events", 5)]
