from arche_lib.langs.ts_routes import routes_and_mounts


def _paths(found):
    return [(route["method"], route["path"], route["line"]) for route in found]


def test_reads_routes_only_from_receivers_bound_to_a_router():
    source = '''const app = new Hono<AppBindings>();
app.get("/benefits", async (context) => context.json([]));
app.post(
  "/usage-events",
  handler,
);
client.get("/not-a-route", options);
// app.get("/commented", handler);
const text = 'app.get("/quoted", handler)';
app.get("/no-handler");
app.get(path, handler);
'''
    routes, mounts, routers = routes_and_mounts("worker.ts", source)
    assert _paths(routes) == [("GET", "/benefits", 2), ("POST", "/usage-events", 3)]
    assert routes[0] == {"file": "worker.ts", "line": 2, "handler_line": 2, "method": "GET",
                         "path": "/benefits", "dynamic": False, "router": "app"}
    assert (mounts, routers) == ([], ["app"])


def test_recognises_express_fastify_and_typed_parameters():
    source = '''const app = express();
const admin = express.Router();
const server = fastify({ logger: true });
export function register(router: Router, other: string) {
  router.delete("/items/:id", remove);
}
app.all("/health", ok);
admin.put("/flags", save);
server.get("/ready", ready);
'''
    routes, _, routers = routes_and_mounts("app.ts", source)
    assert _paths(routes) == [("DELETE", "/items/:id", 5), ("ALL", "/health", 7),
                              ("PUT", "/flags", 8), ("GET", "/ready", 9)]
    assert routers == ["admin", "app", "router", "server"]


def test_applies_prefixes_of_routers_mounted_in_the_same_file():
    source = '''const app = new Hono();
const api = new Hono();
const users = new Hono();
users.get("/:id", show);
users.post("/", create);
api.route("/users", users);
app.route("/v1", api);
'''
    routes, mounts, _ = routes_and_mounts("app.ts", source)
    assert _paths(routes) == [("GET", "/v1/users/:id", 4), ("POST", "/v1/users", 5)]
    assert mounts == []


def test_returns_mounts_of_names_this_file_does_not_bind():
    source = '''const app = express();
app.use("/api", apiRouter);
app.use("/me/*", jwtAuth);
app.use(cors());
'''
    _, mounts, _ = routes_and_mounts("app.ts", source)
    assert mounts == [{"file": "app.ts", "line": 2, "prefix": "/api", "name": "apiRouter"},
                      {"file": "app.ts", "line": 3, "prefix": "/me/*", "name": "jwtAuth"}]


def test_marks_template_paths_dynamic():
    source = 'const app = new Hono();\napp.get(`/items/${kind}`, handler);\n'
    routes, _, _ = routes_and_mounts("app.ts", source)
    assert (routes[0]["path"], routes[0]["dynamic"]) == ("/items/${kind}", True)
