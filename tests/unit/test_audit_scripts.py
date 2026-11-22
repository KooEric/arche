from arche_lib.audit_scripts import build_script_audit
from test_graph_multilang import _graph

SOURCES = {
    "web/app.ts": 'import { api } from "./api";\nfunction helper() { return 1; }\nfunction unusedHelper() { return 2; }\n'
                  'const label = `${helper()}`;\nconst byName = 3;\nconst key = "byName";\nclass Hidden {}\n'
                  'function later() {}\nexport { later };\nexport const shown = label + key;\n',
    "web/api.ts": "export const api = 1;\n",
    "web/app.test.ts": "function onlyInTest() {}\n",
    "lib/view.dart": "class Screen {}\nclass _Unused {}\nclass _Used {}\nfinal x = _Used();\nvoid main() {}\n",
    "lib/part_owner.dart": "part 'piece.dart';\nclass _SharedWithPart {}\n",
    "go.mod": "module example.com/app\n",
    "svc/a.go": "package svc\nfunc used() {}\nfunc unusedFunc() {}\nfunc Exported() { used() }\nfunc init() {}\n",
    "svc/b.go": "package svc\nfunc crossFile() {}\n",
    "svc/c.go": "package svc\nfunc Call() { crossFile() }\n",
    "svc/a_test.go": "package svc\nfunc TestIt() {}\nfunc helperForTests() {}\n",
}


def test_only_unreferenced_private_declarations_are_candidates():
    findings = build_script_audit(_graph(SOURCES), SOURCES.get)
    assert [(f["path"], f["name"], f["line"]) for f in findings] == [
        ("lib/view.dart", "_Unused", 2), ("svc/a.go", "unusedFunc", 3),
        ("web/app.ts", "unusedHelper", 3), ("web/app.ts", "Hidden", 7)]
    assert {f["rule"] for f in findings} == {"unused-private-symbol"}
