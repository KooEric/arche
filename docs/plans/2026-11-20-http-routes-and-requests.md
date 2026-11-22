# HTTP 라우트·요청 인식 확장 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hono/Express/Fastify, FastAPI/Flask 라우트와 fetch/axios, Dart 헬퍼 경유 요청을 읽어, 변수 경로까지 포함해 요청과 라우트를 연결한다.

**Architecture:** 언어별 추출기가 `http_routes`·`http_requests`·`http_mounts`·`http_routers`를 내고, `graph.py`가 import 엣지를 따라 다른 파일에 마운트된 라우터의 접두사를 붙인 뒤 `httpmatch.connect`로 조각 단위 매칭을 한다. `graph.json`의 `http_connections` 형식은 유지하고 필드만 더한다.

**Tech Stack:** Python 3.9+ 표준 라이브러리, pytest. 기존 `arche_lib.lexer.mask`와 `ast`를 재사용한다.

이 문서는 [근거 지도 설계](../specs/2026-11-20-evidence-map-design.md)의 구현 순서 1번이다. 2번(`map.json`·`map.py`), 3번(뷰어), 4번(워크시트)은 각자 계획 문서를 따로 쓴다. 이 문서의 코드와 테스트는 `main`(b0f88d5)의 복사본에서 실행해 확인했다: 전체 393개 통과, `evals/score.py` 게이트 통과, picca에서 요청 22건 중 14건 연결.

## Global Constraints

- Python 3.9에서 동작해야 한다. `match` 문, 런타임의 `X | Y` 타입 표기, 3.10 이상 전용 API를 쓰지 않는다.
- 표준 라이브러리만 쓴다. `tests/test_stdlib_only.py`가 검사한다.
- 대상 레포는 읽기만 한다. 프로젝트 코드를 실행하지 않는다.
- HTTP 매칭 결과의 상태는 `inferred` 또는 `unresolved`다. `verified`로 올리지 않는다.
- 기존 reason 문자열 `method_and_path`, `multiple_handlers`, `no_literal_handler`, `dynamic_request_path`는 이름을 바꾸지 않는다. 설계 문서의 `no_matching_route`는 쓰지 않고 `no_literal_handler`를 유지한다.
- 커밋 메시지는 이 저장소의 관례대로 영어 한 문장으로 쓰고, 귀속(Co-Authored-By) 줄은 붙이지 않는다.
- 모든 명령은 워크트리 루트 `/Users/koo/dev/Arche/.claude/worktrees/evidence-map`에서 실행한다.

## File Structure

| 파일 | 책임 |
|---|---|
| `skills/arche/scripts/arche_lib/httpmatch.py` (신규) | URL 리터럴에서 경로·호스트 읽기, 경로 조각 비교, 접두사 결합, 요청↔라우트 연결 |
| `skills/arche/scripts/arche_lib/langs/ts_routes.py` (신규) | `app.get("/x", h)` 형태의 라우트와 마운트 |
| `skills/arche/scripts/arche_lib/langs/ts_requests.py` (신규) | `fetch`·axios 요청 |
| `skills/arche/scripts/arche_lib/langs/python_http.py` (신규) | FastAPI·Flask 데코레이터 라우트 |
| `skills/arche/scripts/arche_lib/langs/dart_http.py` (수정) | 헬퍼 경유 요청 추가, 경로 읽기를 `httpmatch`로 이전 |
| `skills/arche/scripts/arche_lib/langs/typescript.py`, `python.py` (수정) | 어댑터 `extract`에 새 추출기 연결 |
| `skills/arche/scripts/arche_lib/graph.py` (수정) | 파일 간 마운트 접두사 적용, `connect` 호출 |
| `evals/fixtures/hono-mobile/` (신규), `evals/score.py` (수정) | HTTP 연결 정답 채점 |

기존 `langs/ts_http.py`(`createRoute` + `.openapi()`)는 그대로 둔다.

---

### Task 1: 경로 매칭 모듈

**Files:**
- Create: `skills/arche/scripts/arche_lib/httpmatch.py`
- Modify: `skills/arche/scripts/arche_lib/graph.py` (`_http_connections`와 import)
- Test: `tests/unit/test_httpmatch.py`

**Interfaces:**
- Consumes: 없음.
- Produces:
  - `request_path(value: str) -> Optional[Tuple[str, bool, Optional[str]]]`: `(path, dynamic, host)`.
  - `request_record(file: str, line: int, method: str, value) -> dict`: 요청 레코드. `host`는 있을 때만 넣는다.
  - `join_path(prefix: str, path: str) -> str`
  - `compare(request: str, route: str) -> Optional[str]`: `"exact"`, `"pattern"`, `None`.
  - `connect(requests: List[dict], routes: List[dict]) -> List[dict]`: `{"request", "candidates", "reason", "status"}` 목록.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_httpmatch.py`:

```python
from arche_lib.httpmatch import compare, connect, join_path, request_path, request_record


def _request(method, path, dynamic=False, **extra):
    return {"file": "client.ts", "line": 1, "method": method, "path": path, "dynamic": dynamic, **extra}


def _route(method, path, line=1):
    return {"file": "server.ts", "line": line, "handler_line": line, "method": method,
            "path": path, "dynamic": False}


def test_request_path_reads_one_leading_interpolation_as_an_unknown_host():
    assert request_path("${Api.baseUrl}/items/$id?page=1") == ("/items/$id", True, None)
    assert request_path("$base/items") == ("/items", False, None)


def test_request_path_keeps_the_host_of_a_literal_absolute_url():
    assert request_path("https://api.example.com/v1/messages#top") == ("/v1/messages", False, "api.example.com")
    assert request_record("a.ts", 3, "POST", ("/v1/messages", False, "api.example.com")) == {
        "file": "a.ts", "line": 3, "method": "POST", "path": "/v1/messages",
        "dynamic": False, "host": "api.example.com"}


def test_request_path_rejects_text_without_a_path():
    assert request_path("$path") is None
    assert request_path("items") is None


def test_compare_treats_every_variable_spelling_as_one_segment():
    for route in ("/items/:id", "/items/{id}", "/items/<int:id>", "/items/*"):
        assert compare("/items/42", route) == "pattern"
        assert compare("/items/${id}", route) == "pattern"
    assert compare("/items/42/", "/items/42") == "exact"
    assert compare("/items", "/items/:id") is None


def test_a_request_variable_does_not_match_a_literal_route_segment():
    assert compare("/auth/$provider", "/auth/kakao") is None


def test_join_path_handles_root_routes_and_wildcard_mounts():
    assert join_path("/api", "/") == "/api"
    assert join_path("/api/", "/users") == "/api/users"
    assert join_path("/api/*", "/users") == "/api/users"
    assert join_path("", "/") == "/"


def test_connect_prefers_an_exact_route_over_a_pattern_route():
    connection, = connect([_request("GET", "/items/version")],
                          [_route("GET", "/items/:id", 1), _route("GET", "/items/version", 2)])
    assert (connection["status"], connection["reason"]) == ("inferred", "method_and_path")
    assert [route["line"] for route in connection["candidates"]] == [2]


def test_connect_reports_each_outcome_with_its_reason():
    routes = [_route("GET", "/items/:id"), _route("POST", "/twice", 1), _route("POST", "/twice", 2),
              _route("ALL", "/health")]
    found = connect([
        _request("GET", "/items/${id}", dynamic=True),
        _request("POST", "/twice"),
        _request("DELETE", "/health"),
        _request("GET", "/missing"),
        _request("GET", "/missing/${id}", dynamic=True),
        _request("POST", "/v1/messages", host="api.example.com"),
    ], routes)
    assert [(c["status"], c["reason"], len(c["candidates"])) for c in found] == [
        ("inferred", "method_and_path_pattern", 1),
        ("unresolved", "multiple_handlers", 2),
        ("inferred", "method_and_path", 1),
        ("unresolved", "no_literal_handler", 0),
        ("unresolved", "dynamic_request_path", 0),
        ("unresolved", "external_host", 0),
    ]
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_httpmatch.py -q`
Expected: 수집 단계에서 `ModuleNotFoundError: No module named 'arche_lib.httpmatch'`

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/httpmatch.py`:

```python
"""Match HTTP requests to routes by method and path shape.

Paths are compared segment by segment. A segment written as `:id`, `{id}`,
`<int:id>`, `*`, `$name` or `${expr}` stands for one variable segment.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit

_VARIABLE = re.compile(r"^(?::\w+\??|\{[^{}]+\}|<[^<>]+>|\*)$")
_INTERPOLATION = re.compile(r"\$\{[^{}]*\}|\$[A-Za-z_]\w*")
_HOST = re.compile(r"^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*)(?=/)")


def request_path(value: str) -> Optional[Tuple[str, bool, Optional[str]]]:
    """Return (path, dynamic, host) for a URL literal, or None when it has no path.

    One leading interpolation is read as an unknown host (`${baseUrl}/items`).
    `host` is set only for a literal absolute URL.
    """
    named = None
    host = _HOST.match(value)
    if host:
        value = value[host.end():]
    elif value.startswith(("http://", "https://")):
        try:
            parts = urlsplit(value)
        except ValueError:
            return None
        named, value = parts.hostname, parts.path or "/"
    elif not value.startswith("/"):
        return None
    value = re.split(r"[?#]", value, maxsplit=1)[0]
    return value, bool(_INTERPOLATION.search(value)), named


def request_record(file: str, line: int, method: str, value: Tuple[str, bool, Optional[str]]) -> dict:
    path, dynamic, host = value
    record = {"file": file, "line": line, "method": method, "path": path, "dynamic": dynamic}
    return {**record, "host": host} if host else record


def join_path(prefix: str, path: str) -> str:
    """Put a mount prefix in front of a route path. `/api/*` mounts at `/api`."""
    prefix = prefix.rstrip("/")
    if prefix.endswith("/*"):
        prefix = prefix[:-2]
    return prefix + ("" if path == "/" else path) or "/"


def segments(path: str) -> Tuple[Optional[str], ...]:
    """Literal segments as text, variable segments as None."""
    parts = [part for part in path.split("/") if part]
    return tuple(None if _VARIABLE.match(part) or _INTERPOLATION.search(part) else part
                 for part in parts)


def compare(request: str, route: str) -> Optional[str]:
    """"exact", "pattern" or None. A request variable only fits a route variable."""
    left, right = segments(request), segments(route)
    if len(left) != len(right):
        return None
    kind = "exact"
    for ours, theirs in zip(left, right):
        if theirs is None:
            kind = "pattern"
        elif ours != theirs:
            return None
    return kind


def connect(requests: List[dict], routes: List[dict]) -> List[dict]:
    """One connection per request. Exact matches hide pattern matches."""
    by_method: Dict[str, List[dict]] = defaultdict(list)
    for route in routes:
        by_method[route["method"]].append(route)
    connections = []
    for request in requests:
        if "host" in request:  # a named outside host is never one of this repository's routes
            connections.append({"request": request, "candidates": [],
                                "reason": "external_host", "status": "unresolved"})
            continue
        found: Dict[str, List[dict]] = {"exact": [], "pattern": []}
        for route in by_method[request["method"]] + by_method["ALL"]:
            kind = compare(request["path"], route["path"])
            if kind:
                found[kind].append(route)
        candidates = found["exact"] or found["pattern"]
        if len(candidates) > 1:
            reason = "multiple_handlers"
        elif found["exact"]:
            reason = "method_and_path"
        elif candidates:
            reason = "method_and_path_pattern"
        else:
            reason = "dynamic_request_path" if request["dynamic"] else "no_literal_handler"
        connections.append({
            "request": request, "candidates": candidates, "reason": reason,
            "status": "inferred" if len(candidates) == 1 else "unresolved",
        })
    return connections
```

- [ ] **Step 4: `graph.py`가 `connect`를 쓰도록 변경**

`skills/arche/scripts/arche_lib/graph.py`의 import 구역에서 `from .jsonio import inventory_digest` 바로 위에 한 줄을 더한다.

```python
from .httpmatch import connect
```

같은 파일의 `_http_connections` 함수 전체를 다음으로 바꾼다.

```python
def _http_connections(extracted: Dict[str, dict]) -> List[dict]:
    requests = [request for result in extracted.values() for request in result.get("http_requests", [])]
    routes = [route for result in extracted.values() for route in result.get("http_routes", [])]
    return connect(requests, routes)
```

- [ ] **Step 5: 통과 확인**

Run: `python3 -m pytest tests/unit/test_httpmatch.py tests/unit/test_mobile_http_graph.py tests/unit/test_graph.py -q`
Expected: 모두 PASS. `test_mobile_http_graph.py`는 수정 없이 통과해야 한다.

- [ ] **Step 6: 커밋**

```bash
git add skills/arche/scripts/arche_lib/httpmatch.py skills/arche/scripts/arche_lib/graph.py tests/unit/test_httpmatch.py
git commit -m "Match HTTP requests to routes by path segments"
```

---

### Task 2: TS/JS 라우터 라우트와 마운트

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/ts_routes.py`
- Modify: `skills/arche/scripts/arche_lib/langs/typescript.py`, `skills/arche/scripts/arche_lib/graph.py`
- Test: `tests/unit/test_ts_routes.py`, `tests/unit/test_http_mounts_graph.py`

**Interfaces:**
- Consumes: `httpmatch.join_path`, `httpmatch.connect`, `arche_lib.lexer.mask`.
- Produces:
  - `routes_and_mounts(path: str, text: str) -> Tuple[List[dict], List[dict], List[str]]`: `(routes, mounts, router 이름)`. 라우트 레코드는 기존 필드에 `"router": <식별자>`가 붙는다. 마운트 레코드는 `{"file", "line", "prefix", "name"}`.
  - 어댑터 결과 키 `http_mounts`, `http_routers`.
  - `graph.json`의 후보 라우트에는 `router` 키가 들어가지 않는다.

`tests/unit/test_http_mounts_graph.py`의 두 테스트는 `fetch` 요청을 쓰므로 Task 3이 끝나야 통과한다. 이 Task에서는 파일만 만들고 Task 3의 Step 4에서 실행한다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_ts_routes.py`:

```python
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
```

`tests/unit/test_http_mounts_graph.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_ts_routes.py -q`
Expected: `ModuleNotFoundError: No module named 'arche_lib.langs.ts_routes'`

- [ ] **Step 3: 추출기 구현**

`skills/arche/scripts/arche_lib/langs/ts_routes.py`:

```python
"""Routes registered on a router object: app.get("/path", handler).

Only receivers this file binds to a known router constructor, or declares as a
parameter of a router type, are read. That keeps HTTP clients such as
`api.get("/x", options)` out of the route list.
"""
from __future__ import annotations

import re

from arche_lib.httpmatch import join_path
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_FACTORY = r"(?:Hono|OpenAPIHono|Router|express|fastify|Fastify)"
_BOUND = re.compile(
    r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*(?::[^=;]+)?=\s*(?:await\s+)?(?:new\s+)?"
    r"(?:express\s*\.\s*)?" + _FACTORY + r"\s*(?:<[^;=()]*>)?\s*\(")
_PARAM = re.compile(
    r"[(,]\s*(" + _IDENT + r")\s*:\s*(?:Hono|OpenAPIHono|Router|Express|Application|FastifyInstance)\b")
_CALL = re.compile(
    r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|options|head|all)\s*\(\s*")
_MOUNT = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(?:route|use)\s*\(\s*")
_TARGET = re.compile(r"\s*,\s*(" + _IDENT + r")\s*[,)]")


def _literal(masked, text, offset):
    """(body, end offset) of the string that opens at `offset`, if any."""
    body = masked.strings.get(offset)
    if body is None:
        return None
    return body, offset + len(body) + 2


def routers(masked) -> set:
    return ({m.group(1) for m in _BOUND.finditer(masked.text)}
            | {m.group(1) for m in _PARAM.finditer(masked.text)})


def routes_and_mounts(path: str, text: str):
    """Return (routes, mounts, router names). Same-file mount prefixes are already applied.

    A mount whose target is not a router of this file is returned for the graph
    step, which follows the import to the file that owns the router.
    """
    masked = mask(text, JS)
    known = routers(masked)
    found = []
    for match in _CALL.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None:
            continue
        route, end = literal
        if not route.startswith("/") or not masked.text[end:].lstrip().startswith(","):
            continue
        line = line_of(masked, match.start())
        found.append({"file": path, "line": line, "handler_line": line,
                      "method": match.group(2).upper(), "path": route,
                      "dynamic": "${" in route, "router": match.group(1)})
    local, mounts = {}, []
    for match in _MOUNT.finditer(masked.text):
        literal = _literal(masked, text, match.end())
        if match.group(1) not in known or literal is None or not literal[0].startswith("/"):
            continue
        target = _TARGET.match(masked.text, literal[1])
        if not target:
            continue
        if target.group(1) in known:
            local[target.group(1)] = (match.group(1), literal[0])
        else:
            mounts.append({"file": path, "line": line_of(masked, match.start()),
                           "prefix": literal[0], "name": target.group(1)})
    for route in found:
        name, seen = route["router"], set()
        while name in local and name not in seen:
            seen.add(name)
            name, prefix = local[name]
            route["path"] = join_path(prefix, route["path"])
    return found, mounts, sorted(known)
```

- [ ] **Step 4: 어댑터 연결**

`skills/arche/scripts/arche_lib/langs/typescript.py`에서 `from .ts_http import routes` 아래에 import를 더하고 `extract`를 바꾼다.

```python
from .ts_http import routes
from .ts_routes import routes_and_mounts
```

```python
def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    router_routes, mounts, routers = routes_and_mounts(path, text)
    result["http_routes"] = routes(path, text) + router_routes
    result["http_mounts"] = mounts
    result["http_routers"] = routers
    return result
```

- [ ] **Step 5: 파일 간 접두사 적용**

`skills/arche/scripts/arche_lib/graph.py`의 import를 `from .httpmatch import connect, join_path`로 바꾼다. Task 1에서 쓴 `_http_connections`를 지우고 그 자리에 다음 두 함수를 넣는다.

```python
def _mounted_routes(extracted: Dict[str, dict], edges: List[dict]) -> List[dict]:
    """All routes, with prefixes from routers mounted in another file applied.

    A mount is followed through the import that brings its name into the file.
    The prefix is applied only when the imported file binds exactly one router,
    so the routes it belongs to are not a guess.
    """
    imported = defaultdict(dict)
    for edge in edges:
        if edge["status"] in _RESOLVED:
            for name in list(edge.get("names", ())) + list(edge.get("aliases", {})):
                imported[edge["from"]].setdefault(name, edge["to"])
    owners = {path: result.get("http_routers", []) for path, result in extracted.items()}
    prefixes: Dict[str, str] = {}
    for _ in range(4):  # nested mounts: app -> api -> users
        for path, result in extracted.items():
            for mount in result.get("http_mounts", []):
                target = imported[_file_id(path)].get(mount["name"], "").removeprefix("file:")
                if len(owners.get(target, ())) == 1:
                    prefixes[target] = join_path(prefixes.get(path, ""), mount["prefix"])
    routes = []
    for path, result in extracted.items():
        for found in result.get("http_routes", []):
            route = {key: value for key, value in found.items() if key != "router"}
            if path in prefixes and "router" in found:
                route["path"] = join_path(prefixes[path], route["path"])
            routes.append(route)
    return routes


def _http_connections(extracted: Dict[str, dict], edges: List[dict]) -> List[dict]:
    requests = [request for result in extracted.values() for request in result.get("http_requests", [])]
    return connect(requests, _mounted_routes(extracted, edges))
```

`build_graph` 안의 호출을 `"http_connections": _http_connections(extracted, edges),`로 바꾼다.

- [ ] **Step 6: 통과 확인**

Run: `python3 -m pytest tests/unit/test_ts_routes.py tests/unit/test_ts_http.py tests/unit/test_mobile_http_graph.py tests/unit/test_graph_multilang.py -q`
Expected: 모두 PASS.

- [ ] **Step 7: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/ts_routes.py skills/arche/scripts/arche_lib/langs/typescript.py skills/arche/scripts/arche_lib/graph.py tests/unit/test_ts_routes.py tests/unit/test_http_mounts_graph.py
git commit -m "Read router-style routes and follow mounts across files"
```

---

### Task 3: TS/JS 요청

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/ts_requests.py`
- Modify: `skills/arche/scripts/arche_lib/langs/typescript.py`
- Test: `tests/unit/test_ts_requests.py`

**Interfaces:**
- Consumes: `httpmatch.request_path`, `httpmatch.request_record`.
- Produces: `requests(path: str, text: str) -> List[dict]`. 어댑터 결과 키 `http_requests`(TS/JS).

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_ts_requests.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_ts_requests.py -q`
Expected: `ModuleNotFoundError: No module named 'arche_lib.langs.ts_requests'`

- [ ] **Step 3: 구현과 어댑터 연결**

`skills/arche/scripts/arche_lib/langs/ts_requests.py`:

```python
"""HTTP requests made with fetch or an axios-style client, with a literal URL."""
from __future__ import annotations

import re

from arche_lib.httpmatch import request_path, request_record
from arche_lib.lexer import JS, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CLIENT = re.compile(r"\b(?:const|let|var)\s+(" + _IDENT + r")\s*=\s*axios\s*\.\s*create\s*\(")
_FETCH = re.compile(r"(?<![\w$.])fetch\s*\(\s*")
_VERB = re.compile(r"(?<![\w$.])(" + _IDENT + r")\s*\.\s*(get|post|put|patch|delete|head|options)\s*(?:<[^;=()]*>)?\s*\(\s*")
_METHOD = re.compile(r"\bmethod\s*:\s*")


def _call_end(text, start):
    depth = 0
    for pos in range(start, len(text)):
        if text[pos] in "([{":
            depth += 1
        elif text[pos] in ")]}":
            depth -= 1
            if depth < 0:
                return pos
    return len(text)


def requests(path: str, text: str) -> list:
    masked = mask(text, JS)
    clients = {"axios"} | {m.group(1) for m in _CLIENT.finditer(masked.text)}
    found = []

    def add(match, method, url_at):
        body = masked.strings.get(url_at)
        value = request_path(body) if body is not None else None
        if value is not None:
            found.append(request_record(path, line_of(masked, match.start()), method, value))

    for match in _FETCH.finditer(masked.text):
        method = "GET"
        option = _METHOD.search(masked.text, match.end(), _call_end(masked.text, match.end()))
        if option and option.end() in masked.strings:
            method = masked.strings[option.end()].upper()
        add(match, method, match.end())
    for match in _VERB.finditer(masked.text):
        if match.group(1) in clients:
            add(match, match.group(2).upper(), match.end())
    return sorted(found, key=lambda item: item["line"])
```

`skills/arche/scripts/arche_lib/langs/typescript.py` 전체는 다음과 같아진다.

```python
"""TypeScript and JavaScript adapter."""
from __future__ import annotations

from .ts_extract import extract as extract_imports
from .ts_http import routes
from .ts_requests import requests
from .ts_routes import routes_and_mounts
from .ts_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    router_routes, mounts, routers = routes_and_mounts(path, text)
    result["http_routes"] = routes(path, text) + router_routes
    result["http_mounts"] = mounts
    result["http_routers"] = routers
    result["http_requests"] = requests(path, text)
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_ts_requests.py tests/unit/test_http_mounts_graph.py -q`
Expected: 모두 PASS. Task 2에서 만든 마운트 테스트가 여기서 처음 통과한다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/ts_requests.py skills/arche/scripts/arche_lib/langs/typescript.py tests/unit/test_ts_requests.py
git commit -m "Read fetch and axios requests with literal URLs"
```

---

### Task 4: Python 라우트

**Files:**
- Create: `skills/arche/scripts/arche_lib/langs/python_http.py`
- Modify: `skills/arche/scripts/arche_lib/langs/python.py`
- Test: `tests/unit/test_python_http.py`

**Interfaces:**
- Consumes: `httpmatch.join_path`.
- Produces: `routes(path: str, text: str) -> List[dict]`. 어댑터 결과 키 `http_routes`(Python). 다른 파일의 `include_router`·`register_blueprint` 접두사는 이번 범위에서 읽지 않는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_python_http.py`:

```python
from arche_lib.langs.python_http import routes


def test_reads_fastapi_decorators_with_router_and_include_prefixes():
    source = '''from fastapi import APIRouter, FastAPI

app = FastAPI()
router = APIRouter(prefix="/items")


@router.get("/{item_id}")
async def read_item(item_id: int):
    return {}


@app.post("/login")
def login():
    return {}


app.include_router(router, prefix="/api")
'''
    assert routes("main.py", source) == [
        {"file": "main.py", "line": 7, "handler_line": 8, "method": "GET",
         "path": "/api/items/{item_id}", "dynamic": False},
        {"file": "main.py", "line": 12, "handler_line": 13, "method": "POST",
         "path": "/login", "dynamic": False},
    ]


def test_reads_flask_route_methods_and_blueprint_prefix():
    source = '''bp = Blueprint("orders", __name__, url_prefix="/orders")


@bp.route("/<int:order_id>", methods=["GET", "DELETE"])
def order(order_id):
    return ""


@bp.route("/")
def index():
    return ""
'''
    assert [(r["method"], r["path"], r["line"]) for r in routes("orders.py", source)] == [
        ("DELETE", "/orders/<int:order_id>", 4), ("GET", "/orders/<int:order_id>", 4),
        ("GET", "/orders", 9)]


def test_ignores_decorators_on_names_the_file_does_not_bind_to_a_framework():
    source = '''@cache.get("/looks-like-a-route")
def cached():
    return 1
'''
    assert routes("cache.py", source) == []


def test_a_file_that_does_not_parse_has_no_routes():
    assert routes("broken.py", "def (:\n") == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_python_http.py -q`
Expected: `ModuleNotFoundError: No module named 'arche_lib.langs.python_http'`

- [ ] **Step 3: 구현과 어댑터 연결**

`skills/arche/scripts/arche_lib/langs/python_http.py`:

```python
"""FastAPI and Flask routes read from decorators, with same-file prefixes."""
from __future__ import annotations

import ast

from ..httpmatch import join_path

_FACTORIES = {"FastAPI", "APIRouter", "Flask", "Blueprint"}
_VERBS = {"get", "post", "put", "patch", "delete", "options", "head"}
_PREFIX_KEYS = ("prefix", "url_prefix")


def _text(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _keyword(call, names):
    return next((_text(k.value) for k in call.keywords if k.arg in names), None)


def _callee(call):
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)


def routes(path: str, text: str) -> list:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return []
    prefixes = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and _callee(node.value) in _FACTORIES):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    prefixes[target.id] = _keyword(node.value, _PREFIX_KEYS) or ""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and _callee(node) in ("include_router", "register_blueprint")
                and node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in prefixes):
            prefixes[node.args[0].id] = (_keyword(node, _PREFIX_KEYS) or "") + prefixes[node.args[0].id]
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                    and isinstance(decorator.func.value, ast.Name)
                    and decorator.func.value.id in prefixes and decorator.args):
                continue
            verb, route = decorator.func.attr, _text(decorator.args[0])
            if route is None or not route.startswith("/"):
                continue
            if verb in _VERBS:
                methods = [verb.upper()]
            elif verb == "route":
                listed = next((k.value for k in decorator.keywords if k.arg == "methods"), None)
                methods = ([_text(item).upper() for item in listed.elts if _text(item)]
                           if isinstance(listed, (ast.List, ast.Tuple)) else ["GET"])
            else:
                continue
            full = join_path(prefixes[decorator.func.value.id], route)
            for method in methods:
                found.append({"file": path, "line": decorator.lineno, "handler_line": node.lineno,
                              "method": method, "path": full, "dynamic": False})
    return sorted(found, key=lambda item: (item["line"], item["method"]))
```

`skills/arche/scripts/arche_lib/langs/python.py` 전체는 다음과 같아진다.

```python
"""Python adapter: the three functions every language adapter provides."""
from __future__ import annotations

from .python_extract import extract as extract_imports
from .python_http import routes
from .python_resolve import Context, build_context, resolve

__all__ = ["Context", "build_context", "extract", "resolve"]


def extract(path: str, text: str) -> dict:
    result = extract_imports(path, text)
    result["http_routes"] = routes(path, text)
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_python_http.py tests/unit/test_python_extract.py tests/unit/test_graph.py -q`
Expected: 모두 PASS.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/python_http.py skills/arche/scripts/arche_lib/langs/python.py tests/unit/test_python_http.py
git commit -m "Read FastAPI and Flask routes from decorators"
```

---

### Task 5: Dart 헬퍼 경유 요청

**Files:**
- Modify: `skills/arche/scripts/arche_lib/langs/dart_http.py`
- Test: `tests/unit/test_dart_http_helpers.py`

**Interfaces:**
- Consumes: `httpmatch.request_path`, `httpmatch.request_record`.
- Produces: `requests(path, text)`의 결과에 헬퍼 호출 지점이 더해지고 줄 번호 순으로 정렬된다. 파일 안의 `_path` 함수는 없어진다.

주의할 점 하나. `requests()` 안의 `code`는 문자열 본문과 따옴표가 공백으로 지워진 사본이다. 호출 지점의 인자 문자열을 찾을 때는 원본 `text`에서 공백을 건너뛰어 따옴표 위치를 구해야 `masked.strings`의 키와 맞는다.

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/unit/test_dart_http_helpers.py`:

```python
from arche_lib.langs.dart_http import requests

CLIENT = r"""
class ApiClient {
  Future<List<Item>> items() async {
    final data = await _getJson('/items', {}, authenticated: true);
    return parse(data);
  }

  Future<Item> item(String id) async {
    final data = await _getJson(
      '/items/$id/detail',
      {},
      authenticated: true,
    );
    return Item.fromJson(data);
  }

  Future<void> save(Map<String, dynamic> body) => _postJson('/items', body);

  Future<Map<String, dynamic>> _getJson(
    String path,
    Map<String, String> params, {
    required bool authenticated,
  }) async {
    final uri = Uri.parse(
      '${ApiConstants.baseUrl}$path',
    ).replace(queryParameters: params);
    return _send(() => _client.get(uri, headers: _headers));
  }

  Future<Map<String, dynamic>> _postJson(String path, Map<String, dynamic> body) async {
    final uri = Uri.parse('${ApiConstants.baseUrl}$path');
    return _send(() => _client.post(uri, body: jsonEncode(body)));
  }

  String _label(String path) {
    return 'path: $path';
  }
}
"""


def test_reads_calls_to_helpers_that_forward_their_path_argument():
    assert [(r["method"], r["path"], r["line"], r["dynamic"]) for r in requests("client.dart", CLIENT)] == [
        ("GET", "/items", 4, False),
        ("GET", "/items/$id/detail", 9, True),
        ("POST", "/items", 17, False),
    ]


def test_a_helper_that_makes_two_kinds_of_request_is_not_guessed():
    source = r"""
class Api {
  Future<void> run() async {
    await _send('/items');
  }

  Future<void> _send(String path) async {
    final uri = Uri.parse('${base}$path');
    if (cached) {
      await _client.get(uri);
    } else {
      await _client.post(uri);
    }
  }
}
"""
    assert requests("client.dart", source) == []
```

- [ ] **Step 2: 실패 확인**

Run: `python3 -m pytest tests/unit/test_dart_http_helpers.py -q`
Expected: 첫 테스트가 `assert [] == [('GET', '/items', 4, False), ...]`로 FAIL. 둘째 테스트는 PASS.

- [ ] **Step 3: 구현**

`skills/arche/scripts/arche_lib/langs/dart_http.py` 전체를 다음으로 바꾼다.

```python
"""Conservative extraction of Dart HTTP calls with literal Uri.parse paths.

This recognises local URI bindings and direct Uri.parse arguments, not arbitrary
Dart evaluation. Unknown or reassigned expressions are intentionally omitted.
"""
from __future__ import annotations

import re
from ..httpmatch import request_path, request_record
from ..lexer import DART, line_of, mask

_IDENT = r"[A-Za-z_$][\w$]*"
_CALL = re.compile(r"\b(?:" + _IDENT + r"\s*\.\s*)+(get|post|put|patch|delete|head|options)\s*\(", re.I)
_ASSIGN = re.compile(r"\b(" + _IDENT + r")\s*=(?!=|>)")
_URI = re.compile(r"\s*Uri\s*\.\s*parse\s*\(")
_METHOD = re.compile(r"\)\s*(?:async\s*\*?|sync\s*\*)?\s*$")
_CONTROL = re.compile(r"\b(?:if|for|while|switch|catch|assert)\s*\([^{}]*\)\s*$")


def requests(path: str, text: str) -> list:
    """Return file, HTTP-call line, method, path and dynamic for known URIs."""
    masked = mask(text, DART)
    code = list(masked.text)
    # The shared lexer exposes interpolation expressions as code. They are
    # still part of strings for this extractor, never independent calls.
    for start, body in masked.strings.items():
        quote_len = 3 if text[start:start + 3] in ("'''", '"""') else 1
        end = min(len(text), start + quote_len + len(body) + quote_len)
        for i in range(start, end):
            if code[i] != '\n':
                code[i] = ' '
    code = ''.join(code)

    def uri_at(offset):
        match = _URI.match(code, offset)
        if not match:
            return None
        quote = match.end()
        while quote < len(text) and text[quote].isspace():
            quote += 1
        if text[quote:quote + 1] == 'r' and quote + 1 in masked.strings:
            quote += 1
        if quote not in masked.strings:
            return None
        quote_len = 3 if text[quote:quote + 3] in ("'''", '"""') else 1
        end = quote + quote_len + len(masked.strings[quote]) + quote_len
        if not re.match(r'\s*,?\s*\)', code[end:]):
            return None
        return request_path(masked.strings[quote])

    events = [(m.start(), 'assign', m) for m in _ASSIGN.finditer(code)]
    events += [(m.start(), 'call', m) for m in _CALL.finditer(code)]
    events += [(i, char, None) for i, char in enumerate(code) if char in '{}']
    events.sort(key=lambda event: event[0])
    scopes = [({}, False)]
    result = []
    for pos, kind, match in events:
        if kind == '{':
            prefix = code[max(0, pos - 300):pos]
            prefix = re.split(r'[;{}]', prefix)[-1]
            is_method = bool(_METHOD.search(prefix) and not _CONTROL.search(prefix))
            scopes.append(({}, scopes[-1][1] or is_method))
        elif kind == '}':
            if len(scopes) > 1:
                scopes.pop()
        elif not scopes[-1][1]:
            continue
        elif kind == 'assign':
            name = match.group(1)
            value = uri_at(match.end())
            # Declarations shadow outer bindings; reassignment updates the
            # nearest existing binding so stale values are not reused.
            before = code[max(0, pos - 100):pos]
            declaration = bool(re.search(r'\b(?:var|final|const|late|Uri)\s+(?:Uri\s+)?$', before))
            target = scopes[-1][0]
            if not declaration:
                target = next((scope for scope, _ in reversed(scopes) if name in scope), target)
            target[name] = value
        else:
            start = match.end()
            value = uri_at(start)
            if value is None:
                arg = re.match(r'\s*(' + _IDENT + r')\s*(?=,|\))', code[start:])
                if arg:
                    value = next((scope[arg.group(1)] for scope, _ in reversed(scopes)
                                  if arg.group(1) in scope), None)
            if value is not None:
                result.append(request_record(path, line_of(masked, match.start(1)),
                                             match.group(1).upper(), value))
    return sorted(result + _helper_requests(path, text, masked, code), key=lambda item: item['line'])


_HELPER = re.compile(r"\b(" + _IDENT + r")\s*\(\s*String\s+(" + _IDENT + r")\s*[,)]")
_FORWARDED = re.compile(r"^(?:\$\{[^{}]+\}|\$[A-Za-z_]\w*)\$\{?(" + _IDENT + r")\}?$")


def _body(code: str, start: int):
    """(open, close) offsets of the first brace block after `start`."""
    opening = code.find('{', code.find(')', start))
    depth = 0
    for pos in range(opening, len(code)) if opening != -1 else ():
        if code[pos] == '{':
            depth += 1
        elif code[pos] == '}':
            depth -= 1
            if not depth:
                return opening, pos
    return None


def _helper_requests(path: str, text: str, masked, code: str) -> list:
    """Calls to a same-file helper that appends its first String argument to a host.

    `_getJson('/items')` is a GET of /items when `_getJson(String path, ...)`
    builds `Uri.parse('${base}$path')` and makes exactly one kind of client call.
    """
    helpers = {}
    for match in _HELPER.finditer(code):
        block = _body(code, match.end() - 1)
        if block is None or code[match.end() - 1:block[0]].count('(') > code[match.end() - 1:block[0]].count(')'):
            continue
        forwards = any(found and found.group(1) == match.group(2)
                       for offset, body in masked.strings.items() if block[0] < offset < block[1]
                       for found in [_FORWARDED.match(body)])
        methods = {call.group(1).upper() for call in _CALL.finditer(code, block[0], block[1])}
        if forwards and len(methods) == 1:
            helpers[match.group(1)] = (methods.pop(), match.start())
    result = []
    for name, (method, declared) in helpers.items():
        for call in re.finditer(r"(?<![\w$.])" + re.escape(name) + r"\s*\(", code):
            quote = call.end()  # string bodies are blank in `code`; find the quote in the source
            while quote < len(text) and text[quote].isspace():
                quote += 1
            body = masked.strings.get(quote)
            value = request_path(body) if call.start() != declared and body is not None else None
            if value is not None:
                result.append(request_record(path, line_of(masked, call.start()), method, value))
    return result
```

- [ ] **Step 4: 통과 확인**

Run: `python3 -m pytest tests/unit/test_dart_http_helpers.py tests/unit/test_dart_http.py tests/unit/test_mobile_http_graph.py -q`
Expected: 모두 PASS. 기존 `test_dart_http.py`는 수정 없이 통과해야 한다.

- [ ] **Step 5: 커밋**

```bash
git add skills/arche/scripts/arche_lib/langs/dart_http.py tests/unit/test_dart_http_helpers.py
git commit -m "Read Dart requests made through same-file path helpers"
```

---

### Task 6: 픽스처, 채점, 리포트 문구, 현장 확인

**Files:**
- Create: `evals/fixtures/hono-mobile/repo/mobile/lib/api_client.dart`, `.../mobile/lib/config.dart`, `.../worker/src/index.ts`, `.../worker/src/routes/orders.ts`, `.../worker/src/session.ts`, `evals/fixtures/hono-mobile/expected_graph.json`
- Modify: `evals/score.py`, `tests/evals/test_fixtures.py:6`, `skills/arche/scripts/arche_lib/report.py:171-172`, `README.md:53`, `docs/release-notes.md`

**Interfaces:**
- Consumes: Task 1~5 전체.
- Produces: `expected_graph.json`의 선택 키 `http`. 항목은 `{"file", "line", "method", "path", "reason", "routes": ["<파일>:<줄>"]}`이고, 키가 없는 픽스처는 HTTP 연결이 없어야 통과한다.

- [ ] **Step 1: 픽스처 소스 작성**

`evals/fixtures/hono-mobile/repo/mobile/lib/api_client.dart`:

```dart
import 'package:http/http.dart' as http;

import 'config.dart';

class ApiClient {
  ApiClient(this._client);

  final http.Client _client;

  Future<http.Response> version() async {
    final uri = Uri.parse('${Config.baseUrl}/catalog/version');
    return _client.get(uri);
  }

  Future<http.Response> orders() => _getJson('/orders');

  Future<http.Response> order(String id) => _getJson('/orders/$id');

  Future<http.Response> login(String provider) => _postJson('/auth/$provider', {});

  Future<http.Response> track() => _postJson('/events', {});

  Future<http.Response> _getJson(String path) async {
    final uri = Uri.parse('${Config.baseUrl}$path');
    return _client.get(uri);
  }

  Future<http.Response> _postJson(String path, Map<String, dynamic> body) async {
    final uri = Uri.parse('${Config.baseUrl}$path');
    return _client.post(uri, body: body.toString());
  }
}
```

`evals/fixtures/hono-mobile/repo/mobile/lib/config.dart`:

```dart
class Config {
  static const baseUrl = String.fromEnvironment('API_BASE_URL');
}
```

`evals/fixtures/hono-mobile/repo/worker/src/index.ts`:

```ts
import { Hono } from "hono";

import { orders } from "./routes/orders";
import { verifyToken } from "./session";

const app = new Hono();

app.get("/catalog/version", (context) => context.json({ version: 1 }));
app.post("/auth/kakao", (context) => context.json({}));
app.post("/auth/google", (context) => context.json({}));
app.use("/orders/*", verifyToken);
app.route("/orders", orders);

export default app;
```

`evals/fixtures/hono-mobile/repo/worker/src/routes/orders.ts`:

```ts
import { Hono } from "hono";

export const orders = new Hono();

orders.get("/", (context) => context.json([]));
orders.get("/:id", (context) => context.json({ id: context.req.param("id") }));
```

`evals/fixtures/hono-mobile/repo/worker/src/session.ts`:

```ts
export const verifyToken = async (context: any, next: () => Promise<void>) => {
  const response = await fetch("https://id.example.com/oauth/tokeninfo", {
    method: "POST",
    body: context.req.header("authorization"),
  });
  if (response.ok) await next();
};
```

- [ ] **Step 2: 정답 파일 작성**

정답은 소스를 읽고 손으로 정한 것이다. 요청 여섯 건이 각각 정적 일치, 마운트 접두사 일치, 변수 경로 일치, 변수 요청 미해결, 라우트 없음, 외부 호스트를 하나씩 대표한다.

`evals/fixtures/hono-mobile/expected_graph.json`:

```json
{
  "analyzed": [
    "mobile/lib/api_client.dart",
    "mobile/lib/config.dart",
    "worker/src/index.ts",
    "worker/src/routes/orders.ts",
    "worker/src/session.ts"
  ],
  "skipped": [],
  "imports": [
    {
      "from": "mobile/lib/api_client.dart",
      "to": "mobile/lib/config.dart"
    },
    {
      "from": "worker/src/index.ts",
      "to": "worker/src/routes/orders.ts"
    },
    {
      "from": "worker/src/index.ts",
      "to": "worker/src/session.ts"
    }
  ],
  "inferred": [],
  "unresolved": [],
  "externals": [
    {
      "from": "mobile/lib/api_client.dart",
      "package": "package:http"
    },
    {
      "from": "worker/src/index.ts",
      "package": "hono"
    },
    {
      "from": "worker/src/routes/orders.ts",
      "package": "hono"
    }
  ],
  "symbols": [
    "sym:mobile/lib/api_client.dart#ApiClient",
    "sym:mobile/lib/config.dart#Config",
    "sym:worker/src/index.ts#app",
    "sym:worker/src/index.ts#default",
    "sym:worker/src/routes/orders.ts#orders",
    "sym:worker/src/session.ts#verifyToken"
  ],
  "http": [
    {
      "file": "mobile/lib/api_client.dart",
      "line": 12,
      "method": "GET",
      "path": "/catalog/version",
      "reason": "method_and_path",
      "routes": [
        "worker/src/index.ts:8"
      ]
    },
    {
      "file": "mobile/lib/api_client.dart",
      "line": 15,
      "method": "GET",
      "path": "/orders",
      "reason": "method_and_path",
      "routes": [
        "worker/src/routes/orders.ts:5"
      ]
    },
    {
      "file": "mobile/lib/api_client.dart",
      "line": 17,
      "method": "GET",
      "path": "/orders/$id",
      "reason": "method_and_path_pattern",
      "routes": [
        "worker/src/routes/orders.ts:6"
      ]
    },
    {
      "file": "mobile/lib/api_client.dart",
      "line": 19,
      "method": "POST",
      "path": "/auth/$provider",
      "reason": "dynamic_request_path",
      "routes": []
    },
    {
      "file": "mobile/lib/api_client.dart",
      "line": 21,
      "method": "POST",
      "path": "/events",
      "reason": "no_literal_handler",
      "routes": []
    },
    {
      "file": "worker/src/session.ts",
      "line": 2,
      "method": "POST",
      "path": "/oauth/tokeninfo",
      "reason": "external_host",
      "routes": []
    }
  ]
}
```

- [ ] **Step 3: 채점에 HTTP 집합 추가**

`evals/score.py`에서 세 군데를 바꾼다.

```python
EXACT_SETS = ("analyzed", "skipped", "inferred", "unresolved", "externals", "symbols", "http")
```

`observed_sets`의 반환 딕셔너리 끝에 `http`를 더하고 그 아래에 함수를 넣는다.

```python
        "symbols": {n["id"] for n in graph["nodes"] if n["type"] == "symbol"},
        "http": {_http_key(c) for c in graph.get("http_connections", [])},
    }


def _http_key(connection):
    """(request file, line, method, path, reason, sorted route file:line targets)."""
    request = connection["request"]
    targets = tuple(sorted("%s:%d" % (r["file"], r["line"]) for r in connection["candidates"]))
    return (request["file"], request["line"], request["method"], request["path"],
            connection["reason"], targets)
```

`expected_sets`의 반환 딕셔너리 끝에 다음을 더한다.

```python
        "symbols": set(expected["symbols"]),
        "http": {(h["file"], h["line"], h["method"], h["path"], h["reason"], tuple(sorted(h["routes"])))
                 for h in expected.get("http", [])},
    }
```

`tests/evals/test_fixtures.py`의 6번째 줄을 바꾼다.

```python
FIXTURES = {"py-basic", "py-dynamic", "secrets", "ts-basic", "ts-alias", "ts-dynamic", "dart-basic", "hono-mobile"}
```

- [ ] **Step 4: 채점 확인**

Run: `python3 evals/score.py`
Expected: `MISMATCH` 줄이 없고 마지막 줄이 `overall precision 1.000 recall 0.969 (gates 0.98 / 0.95)`.

- [ ] **Step 5: 리포트와 문서 문구**

`skills/arche/scripts/arche_lib/report.py`의 `_http_section`에서 제목과 안내문을 바꾼다.

```python
        '<section id="http"><h2>HTTP requests and route handlers</h2>'
        '<p>Candidates share the HTTP method and path shape. A request to a named outside host is listed as external_host. The deployment host of this repository is unknown.</p>'
```

`README.md` 53번째 줄의 두 군데를 바꾼다. "합성 레포 7개"는 "합성 레포 8개"로, "Dart의 HTTP 요청과 Worker의 OpenAPI 라우트가 발견되면"은 "Dart·TypeScript·JavaScript의 HTTP 요청과 Hono·Express·Fastify·FastAPI·Flask 라우트가 발견되면"으로 고친다.

`docs/release-notes.md`의 목록 맨 위에 한 줄을 더한다.

```markdown
- HTTP 연결 후보가 `app.get("/path", …)` 형태의 라우트, FastAPI·Flask 데코레이터, `fetch`·axios 요청, Dart 헬퍼 경유 요청을 읽습니다. `:id` 같은 변수 경로도 맞추고, 외부 호스트로 가는 요청은 `external_host`로 구분합니다.
```

- [ ] **Step 6: 전체 테스트**

Run: `python3 -m pytest -q`
Expected: `393 passed`. 리포트 제목을 검사하는 기존 테스트가 실패하면 그 테스트의 기대 문자열을 새 제목으로 고친다.

- [ ] **Step 7: 현장 확인**

picca는 읽기만 한다. 출력은 레포 밖 임시 디렉터리에 둔다.

```bash
out="$(mktemp -d)"
python3 -B skills/arche/scripts/scan.py /Users/koo/dev/picca --out "$out"
python3 -B skills/arche/scripts/imports.py --out "$out"
python3 -B skills/arche/scripts/render.py --out "$out"
python3 -c 'import collections, json, sys; found = json.load(open(sys.argv[1] + "/graph.json"))["http_connections"]; print(len(found), sorted(collections.Counter((c["status"], c["reason"]) for c in found).items()))' "$out"
```

Expected(2026-11-20의 picca 기준이며, 이후 커밋에 따라 수는 달라질 수 있다): 총 22건. `inferred/method_and_path` 13, `inferred/method_and_path_pattern` 1, `unresolved/external_host` 7, `unresolved/dynamic_request_path` 1. 미해결 한 건은 `POST /auth/$provider`이고, 라우트가 `/auth/kakao` 등 리터럴 셋이라 추측하지 않는 것이 맞다. 호스트가 없는 정적 요청은 모두 라우트 하나에 연결되어야 한다.

- [ ] **Step 8: 커밋**

```bash
git add evals/fixtures/hono-mobile evals/score.py tests/evals/test_fixtures.py skills/arche/scripts/arche_lib/report.py README.md docs/release-notes.md
git commit -m "Score HTTP connections on a Hono and Flutter fixture"
```

---

## 이 계획에서 다루지 않는 것

- 설계 문서의 "라우트로 보이지만 읽지 못한 줄" 집계는 `brief.py`의 출력이므로 2번 계획에서 구현한다.
- Python의 파일 간 접두사, Go 라우터, NestJS 데코레이터, Next.js 파일 기반 라우트는 실제 대상 레포에서 필요해질 때 픽스처와 함께 더한다.
- 요청의 `host` 필드는 2번 계획에서 외부 서비스 부품(`hosts`)과 연결하는 데 쓴다.
