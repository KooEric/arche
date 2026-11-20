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
    assert (mounts, routers) == ([], {"app": {"sub": False}})


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
    assert routers == {"admin": {"sub": True}, "app": {"sub": False},
                       "router": {"sub": True}, "server": {"sub": False}}
    assert [bool(route.get("prefix_unknown")) for route in routes] == [True, False, True, False]


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
    assert mounts == [{"file": "app.ts", "line": 2, "prefix": "/api", "name": "apiRouter",
                       "router": "app"}]


def test_marks_template_paths_dynamic():
    source = 'const app = new Hono();\napp.get(`/items/${kind}`, handler);\n'
    routes, _, _ = routes_and_mounts("app.ts", source)
    assert (routes[0]["path"], routes[0]["dynamic"]) == ("/items/${kind}", True)


def test_a_sub_router_mounted_in_the_same_file_takes_a_known_prefix():
    source = '''const app = express();
const users = express.Router();
const loose = Router();
users.get("/:id", show);
loose.get("/:id", show);
app.use("/users", users);
'''
    routes, _, _ = routes_and_mounts("app.ts", source)
    assert [(r["path"], bool(r.get("prefix_unknown")), r.get("router")) for r in routes] == [
        ("/users/:id", False, "app"), ("/:id", True, "loose")]


def test_a_mount_on_a_locally_mounted_router_names_the_outermost_router():
    source = '''const app = new Hono();
const api = new Hono();
api.route("/users", users);
app.route("/v1", api);
'''
    _, mounts, _ = routes_and_mounts("app.ts", source)
    assert mounts == [{"file": "app.ts", "line": 3, "prefix": "/v1/users", "name": "users",
                       "router": "app"}]


def test_a_router_mounted_twice_in_the_same_file_has_an_unknown_prefix():
    source = '''const app = new Hono();
const users = new Hono();
users.get("/:id", show);
app.route("/a", users);
app.route("/b", users);
'''
    routes, _, _ = routes_and_mounts("app.ts", source)
    assert routes[0]["prefix_unknown"] is True and "router" not in routes[0]


def test_a_hono_app_with_a_chained_base_path_has_an_unknown_prefix():
    source = 'const app = new Hono().basePath("/api");\napp.get("/items", list);\n'
    routes, _, routers = routes_and_mounts("app.ts", source)
    assert routers == {"app": {"sub": True}}
    assert routes[0]["prefix_unknown"] is True


def test_a_long_run_of_type_annotations_is_read_in_linear_time():
    import time
    started = time.perf_counter()
    assert routes_and_mounts("a.ts", "const a:" * 30000)[0] == []
    assert routes_and_mounts("a.ts", "const a=Router<" * 5000)[0] == []
    assert time.perf_counter() - started < 2


def _default(source):
    return [name for name, info in routes_and_mounts("u.ts", source)[2].items() if info.get("default")]


def test_reports_the_router_a_file_exports_as_default():
    assert _default('const router = Router();\nexport default router;\n') == ["router"]
    assert _default('const r = Router();\nexport { r as default };\n') == ["r"]
    assert _default('const r = Router();\nexport default function auth() {}\n') == []
    assert _default('const r = Router();\nexport default other;\n') == []
    assert _default('const r = Router();\nexport default r;\nexport default r;\n') == []
