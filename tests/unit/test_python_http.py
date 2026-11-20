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
