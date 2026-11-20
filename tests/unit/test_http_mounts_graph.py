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


def test_a_file_with_two_routers_keeps_its_unprefixed_paths(make_repo):
    files = {**FILES, "src/routes/users.ts":
             'import { Router } from "express";\nexport const usersRouter = Router();\n'
             'export const adminRouter = Router();\n'
             'usersRouter.get("/:id", (req, res) => res.json({}));\n'}
    request, _ = _connections(make_repo, files)
    assert (request["status"], request["reason"]) == ("unresolved", "dynamic_request_path")
