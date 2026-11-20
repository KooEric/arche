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


def _flags(found):
    return [(r["method"], r["path"], bool(r.get("prefix_unknown"))) for r in found]


def test_routes_on_a_router_this_file_does_not_include_have_an_unknown_prefix():
    source = '''from fastapi import APIRouter
router: APIRouter = APIRouter(prefix="/users")


@router.get("/{uid}")
def show(uid):
    return {}
'''
    found = routes("users.py", source)
    assert _flags(found) == [("GET", "/users/{uid}", True)]
    assert found[0]["prefix_unknown"] is True


def test_an_included_router_takes_the_prefix_of_each_include_once():
    source = '''app = FastAPI()
api = APIRouter()
admin = APIRouter()


@api.get("/items")
def items():
    return []


@admin.get("/flags")
def flags():
    return []


app.include_router(router=api, prefix="/v1")
app.include_router(admin, prefix="/a")
app.include_router(admin, prefix="/b")
'''
    assert _flags(routes("main.py", source)) == [("GET", "/v1/items", False), ("GET", "/flags", True)]
    assert "prefix_unknown" not in routes("main.py", source)[0]


def test_a_blueprint_registered_on_the_app_has_a_known_prefix():
    source = '''app = Flask(__name__)
bp = Blueprint("b", __name__, url_prefix="/b")


@bp.route("/x")
def x():
    return ""


app.register_blueprint(blueprint=bp, url_prefix="/api")
'''
    assert _flags(routes("app.py", source)) == [("GET", "/api/b/x", False)]


def test_route_methods_must_be_a_literal_collection_of_strings():
    source = '''app = Flask(__name__)


@app.route("/a", methods={"POST"})
def a():
    return ""


@app.route("/b", methods=ALLOWED)
def b():
    return ""


@app.route("/c", methods=["GET", verb])
def c():
    return ""


@app.route("/d", methods=("PUT",))
def d():
    return ""
'''
    assert _flags(routes("app.py", source)) == [("POST", "/a", False), ("PUT", "/d", False)]
