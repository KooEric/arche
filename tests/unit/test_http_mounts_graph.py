from arche_lib.graph import build_graph
from arche_lib.inventory import build_inventory
from arche_lib.sources import make_reader

FILES = {
    "src/app.ts": 'import express from "express";\nimport api from "./routes/index";\n'
                  'const app = express();\napp.use("/api", api);\n',
    "src/routes/index.ts": 'import { Router } from "express";\n'
                           'import { usersRouter as users } from "./users";\n'
                           'const router = Router();\nrouter.use("/users", users);\n'
                           'export default router;\n',
    "src/routes/users.ts": 'import { Router } from "express";\nexport const usersRouter = Router();\n'
                           'usersRouter.get("/:id", (req, res) => res.json({}));\n',
    "src/client.ts": 'export const load = (id: string) => fetch(`/api/users/${id}`);\n'
                     'export const who = () => fetch("https://id.example.com/me");\n',
}


def _connections(make_repo, files):
    inventory = build_inventory(make_repo(files, use_git=False))
    return build_graph(inventory, make_reader(inventory))["http_connections"]


def test_prefixes_follow_imports_through_nested_router_files(make_repo):
    request, outside = _connections(make_repo, FILES)
    assert (request["status"], request["reason"]) == ("inferred", "method_and_path_pattern")
    assert request["candidates"] == [{
        "file": "src/routes/users.ts", "line": 3, "handler_line": 3,
        "method": "GET", "path": "/api/users/:id", "dynamic": False}]
    assert (outside["reason"], outside["request"]["host"]) == ("external_host", "id.example.com")


def _outcomes(connections):
    return [(c["request"]["path"], c["status"], c["reason"]) for c in connections]


def test_only_the_mounted_router_of_a_file_takes_the_prefix(make_repo):
    files = {**FILES, "src/routes/users.ts":
             'import { Router } from "express";\nexport const usersRouter = Router();\n'
             'export const adminRouter = Router();\n'
             'usersRouter.get("/:id", (req, res) => res.json({}));\n'
             'adminRouter.get("/flags/:id", (req, res) => res.json({}));\n',
             "src/client.ts": 'export const load = (id: string) => fetch(`/api/users/${id}`);\n'
                              'export const flags = () => fetch("/flags/1");\n'}
    request, flags = _connections(make_repo, files)
    assert (request["status"], request["reason"]) == ("inferred", "method_and_path_pattern")
    assert request["candidates"][0]["path"] == "/api/users/:id"
    assert "prefix_unknown" not in request["candidates"][0]
    assert (flags["status"], flags["reason"]) == ("unresolved", "route_prefix_unknown")
    assert flags["candidates"][0]["prefix_unknown"] is True


def test_a_fastapi_router_included_from_another_file_is_not_guessed(make_repo):
    files = {
        "main.py": "from fastapi import FastAPI\nimport users\napp = FastAPI()\n"
                   'app.include_router(users.router, prefix="/users")\n',
        "users.py": "from fastapi import APIRouter\nrouter = APIRouter()\n\n\n"
                    '@router.get("/{uid}")\ndef show(uid):\n    return {}\n',
        "web/client.ts": 'export const health = () => fetch("/health");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [("/health", "unresolved", "route_prefix_unknown")]


def test_an_express_router_mounted_through_require_is_not_guessed(make_repo):
    files = {
        "src/app.js": 'const express = require("express");\nconst app = express();\n'
                      'app.use("/users", require("./users"));\n',
        "src/users.js": 'const express = require("express");\nconst router = express.Router();\n'
                        'router.get("/:id", (req, res) => res.json({}));\nmodule.exports = router;\n',
        "src/client.js": 'fetch("/version");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [("/version", "unresolved", "route_prefix_unknown")]


def test_a_mount_with_middleware_before_the_router_is_not_guessed(make_repo):
    files = {
        "src/app.ts": 'import express from "express";\nimport auth from "./auth";\n'
                      'import users from "./users";\nconst app = express();\n'
                      'app.use("/users", auth, users);\n',
        "src/auth.ts": "export default function auth(req, res, next) { next(); }\n",
        "src/users.ts": 'import { Router } from "express";\nconst router = Router();\n'
                        'router.get("/:id", (req, res) => res.json({}));\nexport default router;\n',
        "src/client.ts": 'fetch("/me");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [("/me", "unresolved", "route_prefix_unknown")]


def test_middleware_imported_from_a_router_file_does_not_prefix_its_routes(make_repo):
    files = {
        "src/app.ts": 'import { Hono } from "hono";\nimport { users, requireAdmin } from "./users";\n'
                      'const app = new Hono();\napp.use("/admin/*", requireAdmin);\n'
                      'app.route("/admin", requireAdmin);\n',
        "src/users.ts": 'import { Router } from "express";\nexport const users = Router();\n'
                        'export const requireAdmin = (c, next) => next();\n'
                        'users.get("/:id", (req, res) => res.json({}));\n',
        "src/client.ts": 'fetch("/admin/42");\n',
    }
    found = _connections(make_repo, files)
    assert [c["status"] for c in found] == ["unresolved"]


def test_a_router_mounted_with_two_prefixes_is_not_guessed(make_repo):
    files = {
        "src/app.ts": 'import { Hono } from "hono";\nimport { users } from "./users";\n'
                      'const app = new Hono();\napp.route("/a", users);\n',
        "src/other.ts": 'import { Hono } from "hono";\nimport { users } from "./users";\n'
                        'const other = new Hono();\nother.route("/b", users);\n',
        "src/users.ts": 'import { Hono } from "hono";\nexport const users = new Hono();\n'
                        'users.get("/list", (c) => c.json([]));\n',
        "src/client.ts": 'fetch("/a/list");\nfetch("/list");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [
        ("/a/list", "unresolved", "no_literal_handler"),
        ("/list", "unresolved", "route_prefix_unknown")]


def test_a_default_export_that_is_not_a_router_of_the_file_takes_no_prefix(make_repo):
    files = {
        "src/app.ts": 'import { Hono } from "hono";\nimport api from "./api";\n'
                      'const app = new Hono();\napp.route("/api", api);\n',
        "src/api.ts": 'import { Hono } from "hono";\nconst api = new Hono();\n'
                      'const extra = new Hono();\napi.get("/items", (c) => c.json([]));\n'
                      'export default wrap(api);\n',
        "src/client.ts": 'fetch("/api/items");\nfetch("/items");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [
        ("/api/items", "unresolved", "no_literal_handler"),
        ("/items", "unresolved", "route_prefix_unknown")]


def test_a_default_export_of_a_file_with_one_router_takes_the_prefix(make_repo):
    files = {
        "src/app.ts": 'import { Hono } from "hono";\nimport api from "./api";\n'
                      'const app = new Hono();\napp.route("/api", api);\n',
        "src/api.ts": 'import { Hono } from "hono";\nconst api = new Hono();\n'
                      'api.get("/items", (c) => c.json([]));\nexport default api;\n',
        "src/client.ts": 'fetch("/api/items");\n',
    }
    assert _outcomes(_connections(make_repo, files)) == [("/api/items", "inferred", "method_and_path")]


def test_a_default_import_is_a_router_only_when_the_default_export_is_that_router(make_repo):
    files = {
        "a.ts": 'import express from "express";\nimport auth from "./u";\n'
                'const app = express();\napp.use("/x", auth);\n',
        "u.ts": 'import { Router } from "express";\nconst router = Router();\n'
                'router.get("/list", h);\nexport default function auth(req, res, next) { next(); }\n',
        "client.ts": 'export const load = () => fetch("/x/list");\n',
    }
    (request,) = _connections(make_repo, files)
    assert (request["status"], request["reason"]) == ("unresolved", "no_literal_handler")


def test_a_default_import_of_a_router_exported_as_default_takes_the_prefix(make_repo):
    files = {
        "a.ts": 'import express from "express";\nimport auth from "./u";\n'
                'const app = express();\napp.use("/x", auth);\n',
        "u.ts": 'import { Router } from "express";\nconst router = Router();\n'
                'router.get("/list", h);\nexport { router as default };\n',
        "client.ts": 'export const load = () => fetch("/x/list");\n',
    }
    (request,) = _connections(make_repo, files)
    assert (request["status"], request["reason"]) == ("inferred", "method_and_path")
