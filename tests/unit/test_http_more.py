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


def test_a_route_declared_in_a_test_does_not_compete_with_the_real_one():
    graph = _graph({
        "api/src/worker.ts": 'import { Hono } from "hono";\nconst app = new Hono();\napp.get("/items", c => c.json([]));\nexport default app;\n',
        "api/test/limit.test.ts": 'import { Hono } from "hono";\nconst app = new Hono();\napp.get("/items", c => c.text("x"));\nfetch("/items");\n',
        "web/app.ts": 'export const load = () => fetch("/items");\n',
    })
    by_file = {c["request"]["file"]: c for c in graph["http_connections"]}
    assert by_file["web/app.ts"]["status"] == "inferred" and by_file["web/app.ts"]["candidates"][0]["file"] == "api/src/worker.ts"
    assert by_file["api/test/limit.test.ts"]["reason"] == "multiple_handlers"


def test_python_router_included_from_another_file_gets_its_prefix():
    graph = _graph({
        "app/__init__.py": "",
        "app/main.py": "from fastapi import FastAPI\nfrom app.routers import users\nfrom app.routers.orders import router as orders_router\n"
                       "from app.routers.admin import bp\n"
                       "app = FastAPI()\napp.include_router(users.router, prefix='/users')\napp.include_router(orders_router, prefix='/v1')\n"
                       "app.register_blueprint(bp, url_prefix='/admin')\n",
        "app/routers/__init__.py": "",
        "app/routers/users.py": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/{user_id}')\ndef show(user_id): ...\n",
        "app/routers/orders.py": "from fastapi import APIRouter\nrouter = APIRouter(prefix='/orders')\n@router.post('/')\ndef create(): ...\n",
        "app/routers/admin.py": "from flask import Blueprint\nbp = Blueprint('admin', __name__, url_prefix='/x')\n@bp.route('/stats')\ndef stats(): ...\n",
        "app/routers/loose.py": "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/loose')\ndef loose(): ...\n",
    })
    listed = {(r["method"], r["path"], r.get("prefix_unknown", False)) for r in graph["http_routes"]}
    assert ("GET", "/users/{user_id}", False) in listed and ("POST", "/v1/orders", False) in listed
    assert ("GET", "/loose", True) in listed                        # mounted nowhere: prefix unknown
    assert any(path.endswith("/stats") and unknown for _, path, unknown in listed)  # Flask url_prefix replaces the blueprint's own


def test_chi_nested_routes_take_the_enclosing_prefixes():
    text = '''package main
func main() {
	r := chi.NewRouter()
	r.Route("/api", func(r chi.Router) {
		r.Get("/health", ok)
		r.Route("/orders", func(o chi.Router) {
			o.Post("/{id}", create)
		})
	})
	r.Get("/root", ok)
}
func mount(x chi.Router) {
	x.Route("/admin", func(a chi.Router) { a.Get("/stats", stats) })
}
'''
    found = [(r["method"], r["path"], r.get("prefix_unknown", False)) for r in routes("main.go", text)]
    assert found == [("GET", "/api/health", False), ("POST", "/api/orders/{id}", False), ("GET", "/root", False), ("GET", "/admin/stats", True)]


def test_requests_through_a_class_field_and_a_url_constant():
    graph = _graph({
        "web/client.ts": 'import axios from "axios";\nexport const api = axios.create();\n',
        "web/service.ts": 'import axios from "axios";\nimport { api } from "./client";\n'
                          'const PUSH_URL = "https://push.example.com/v1/send";\nconst twice = "/a";\nconst twice = "/b";\n'
                          'class Orders {\n  private http = axios.create();\n  constructor() { this.api = api;\n  }\n'
                          '  list() { return this.http.get("/orders"); }\n  save() { return this.api.post("/orders"); }\n'
                          '  other() { return this.cache.get("/not-a-request"); }\n}\n'
                          'export const push = () => fetch(PUSH_URL, { method: "POST" });\nfetch(twice);\n',
    })
    seen = [(c["request"]["method"], c["request"]["path"], c["request"].get("host")) for c in graph["http_connections"]]
    assert seen == [("GET", "/orders", None), ("POST", "/orders", None), ("POST", "/v1/send", "push.example.com")]
