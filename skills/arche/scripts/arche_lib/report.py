"""Render the static dependency graph without embedding source excerpts."""
import base64
import hashlib
import re
from collections import Counter, defaultdict
from graphlib import CycleError, TopologicalSorter
from html import escape
from string import Template

from .focus import select_steps


def _code(text):
    return f"<code>{escape(str(text))}</code>"


def _items(items, empty):
    rows = "".join(f"<li>{item}</li>" for item in items)
    return f"<ul>{rows}</ul>" if rows else f"<p>{empty}</p>"


def _file_link(node_id, anchors):
    caption = _code(node_id.removeprefix("file:"))
    if node_id in anchors:
        return f'<a href="#{anchors[node_id]}">{caption}</a>'
    return caption


def _evidence(edge):
    evidence = edge["evidence"]
    return _code(f"{evidence['path']}:{evidence['line']}")


def _relation(edge, other, anchors):
    status = escape(edge["status"])
    label = _file_link(other, anchors) if other else _code(edge["spec"])
    reason = f" · {escape(edge['reason'])}" if "reason" in edge else ""
    return f'{label} · <span class="status" data-state="{status}">{status}</span> · {_evidence(edge)}{reason}'


def _files(graph, files, anchors):
    incoming, outgoing, symbols, externals = (defaultdict(list) for _ in range(4))
    for edge in graph["edges"]:
        outgoing[edge["from"]].append(_relation(edge, edge["to"], anchors))
        if edge["to"] in anchors:
            incoming[edge["to"]].append(_relation(edge, edge["from"], anchors))
    for node in graph["nodes"]:
        if node["type"] == "symbol":
            path, symbol = node["id"].removeprefix("sym:").rsplit("#", 1)
            symbols[f"file:{path}"].append(f"{_code(symbol)} · {escape(node['kind'])} · line {node['line']:d}")
    for item in graph["externals"]:
        externals[item["from"]].append(f"{_code(item['package'])} · line {item['line']:d}")

    rows = []
    for file in files:
        node_id = file["id"]
        path = escape(node_id.removeprefix("file:"))
        rows.append(
            f'<details id="{anchors[node_id]}" data-file="{path}" data-module="{escape(node_id.removeprefix("file:").split("/", 1)[0] if "/" in node_id.removeprefix("file:") else ".", quote=True)}">'
            f'<summary>{path} <span class="label">· {escape(file["lang"])} · {file["lines"]:d} lines</span></summary>'
            '<div class="detail">'
            f'<h3>Symbols</h3>{_items(symbols[node_id], "No extracted symbols.")}'
            f'<h3>Imports</h3>{_items(outgoing[node_id], "No recorded imports.")}'
            f'<h3>Imported by</h3>{_items(incoming[node_id], "No recorded importers.")}'
            f'<h3>External packages</h3>{_items(externals[node_id], "No recorded external packages.")}'
            '</div></details>'
        )
    return "".join(rows) or "<p>No files parsed by the installed adapters.</p>"


def _modules(modules):
    if not modules:
        return '<p>No module inventory available.</p>'
    return '<div class="modules">' + ''.join(
        f'<button type="button" class="module" data-select-module="{escape(item["name"], quote=True)}">'
        f'<strong>{escape(item["name"])}</strong><span>{item["parsed"]} parsed · {item["excluded"]} excluded</span></button>'
        for item in modules
    ) + '</div>'


def _tour(tour, anchors):
    if not tour or not tour["steps"]:
        return '<p>No tour available for this snapshot.</p>'
    incoming = defaultdict(list)
    for edge in tour["edges"]:
        incoming[edge["to"]].append(edge)
    rows = []
    for index, step in enumerate(tour["steps"], 1):
        relationships = _items(
            (f'{_code(edge["from"])} → {_code(edge["to"])} · {escape(edge["kind"])}' for edge in incoming[step["id"]]),
            'Starting step; no incoming tour relationship.',
        )
        rows.append(
            f'<li class="tour-step"><details><summary><span class="step-number">{index:02d}</span> '
            f'{_code(step["id"])}</summary><div class="detail">'
            f'<p>Evidence · {_file_link("file:" + step["path"], anchors)}:'
            f'{step["start_line"]}–{step["end_line"]}</p>'
            f'<p>SHA-256 · {_code(step["sha256"])}</p><h3>Relationships</h3>{relationships}'
            '</div></details></li>'
        )
    return '<ol class="tour-list">' + ''.join(rows) + '</ol>'


def _screens(tour, anchors):
    if not tour or not tour.get("screens"):
        return ""
    steps = {step["id"]: step for step in tour["steps"]}
    cards = []
    for screen in tour["screens"]:
        items = []
        for item in screen["items"]:
            step = steps[item["step"]]
            source = _file_link("file:" + step["path"], anchors)
            items.append(f'<li><strong>{escape(item["label"])}</strong>'
                         f'<p>{escape(item["behavior"])}</p>'
                         f'{source}:{step["start_line"]}–{step["end_line"]}</li>')
        cards.append(f'<details><summary>{escape(screen["title"])}</summary>'
                     f'<ul>{"".join(items)}</ul></details>')
    return '<section id="screens"><h2>Screens and features</h2>' + "".join(cards) + '</section>'



def _feature_focus(tour, connections, anchors):
    if not tour or not tour.get("steps"):
        return ""
    buttons, panels = [], []
    labels = defaultdict(list)
    for screen in tour.get("screens", []):
        for item in screen["items"]:
            labels[item["step"]].append((screen["title"], item["label"], item["behavior"]))
    for index, step in enumerate(tour["steps"]):
        step_id = step["id"]
        buttons.append(f'<button type="button" data-focus-select="{index}" aria-pressed="{str(index == 0).lower()}">{escape(step_id)}</button>')
        for title, label, behavior in labels[step_id]:
            buttons.append(f'<button type="button" data-focus-select="{index}" aria-pressed="{str(index == 0).lower()}">{escape(title)} · {escape(label)}</button>')
        selected = select_steps(tour, {step_id}, 2, connections)
        cards = []
        for selected_step in selected["steps"]:
            sid = selected_step["id"]
            details = ''.join(f'<p><strong>{escape(title)} · {escape(label)}</strong> — {escape(behavior)}</p>' for title, label, behavior in labels[sid])
            source = _file_link('file:' + selected_step['path'], anchors)
            cards.append(f'<li><strong>{_code(sid)}</strong>{details}<p>Evidence · {source}:{selected_step["start_line"]}–{selected_step["end_line"]}</p></li>')
        relations = []
        for edge in selected["edges"]:
            relations.append(f'{_code(edge["from"])} → <strong>{escape(edge["kind"])}</strong> → {_code(edge["to"])}')
        for edge in selected["http_candidates"]:
            relations.append(f'{_code(edge["from"])} ⇢ <strong>inferred HTTP</strong> {_code(edge["method"])} {_code(edge["path"])} ⇢ {_code(edge["to"])}')
        panels.append(f'<div data-focus-panel="{index}"' + ('' if index == 0 else ' hidden') + '>'
                      + '<h3>Selected steps</h3><ul class="focus-nodes">' + ''.join(cards) + '</ul>'
                      + '<h3>Connections</h3>' + _items(relations, 'No connected tour relationships within two hops.') + '</div>')
    return ('<section id="feature-focus"><h2>Feature focus</h2><p>Select a screen control or tour step to see connected evidence within two relationships. HTTP links are inferred from exact cited request and route or handler lines.</p>'
            + '<div class="focus-controls" aria-label="Select feature or tour step">' + ''.join(buttons) + '</div>' + ''.join(panels) + '</section>')

def _http_section(connections, anchors):
    if not connections:
        return ""
    rows = []
    for item in connections:
        request = item["request"]
        source = _file_link("file:" + request["file"], anchors)
        candidates = []
        for route in item["candidates"]:
            target = _file_link("file:" + route["file"], anchors)
            candidates.append(f"{target} · route line {route['line']:d}, handler line {route['handler_line']:d}")
        host = f" · host {_code(request['host'])}" if request.get("host") else ""
        empty = "" if item["reason"] == "external_host" else "No matching literal route identified."
        rows.append(
            f"<p>{_code(request['method'])} {_code(request['path'])}{host} · {escape(item['status'])}"
            f" ({escape(item['reason'])})</p><p>{source} · request line {request['line']:d}</p>"
            + (_items(candidates, empty) if candidates or empty else "")
        )
    return (
        '<section id="http"><h2>HTTP requests and route handlers</h2>'
        '<p>Candidates share the HTTP method and path shape. A request to a named outside host is listed as external_host. The deployment host of this repository is unknown.</p>'
        + _items(rows, "") + "</section>"
    )


def _audit(edges, anchors):
    """Surface one cycle in verified, unconditional static imports."""
    pairs = {}
    sorter = TopologicalSorter()
    for edge in edges:
        if edge.get("kind", "IMPORTS") != "IMPORTS" or edge["status"] != "verified" or any(
            edge.get(flag) for flag in ("type_only", "conditional", "dynamic")
        ):
            continue
        source, target = edge["from"], edge["to"]
        pairs.setdefault((source, target), edge)
        sorter.add(target, source)
    duplicates = []
    grouped = defaultdict(list)
    for edge in edges:
        if edge.get("kind", "IMPORTS") == "IMPORTS" and edge["status"] == "verified" and not any(edge.get(flag) for flag in ("type_only", "conditional", "dynamic")):
            grouped[(edge["from"], edge["to"])].append(edge)
    for (source, target), group in sorted(grouped.items()):
        locations = sorted({(e["evidence"]["path"], e["evidence"]["line"]) for e in group})
        if len(locations) > 1:
            duplicates.append(f'{_file_link(source, anchors)} → {_file_link(target, anchors)} · ' + ', '.join(_code(f"{path}:{line}") for path, line in locations))
    duplicate_report = ('<h3>Repeated static imports</h3><p>Distinct locations importing the same target may be redundant; aliases and separate uses can be intentional. Review each location before changing it.</p>'
        + _items(duplicates, 'No repeated verified unconditional imports at distinct locations.'))
    try:
        sorter.prepare()
    except CycleError as exc:
        cycle = exc.args[1]
        return (
            "<p>One observed static import cycle. Runtime impact depends on initialization order and language semantics; review the listed edges.</p>"
            + _items(
                (
                    f"{_file_link(source, anchors)} → {_file_link(target, anchors)} · {_evidence(pairs[source, target])}"
                    for source, target in zip(cycle, cycle[1:])
                ),
                "",
            )
            + duplicate_report
        )
    return "<p>No cycle found among verified unconditional static imports.</p>" + duplicate_report


def _changes(changes, anchors):
    if changes is None:
        return '<p>No change comparison available for this snapshot.</p>'
    files = changes["files"]
    availability = "" if changes.get("impact_available") else "<p>Static impact unavailable for this graph snapshot.</p>"
    if not files:
        return availability + '<p>No changed files in this comparison.</p>'
    rows = []
    for item in files:
        status, path = item["status"], item["path"]
        old = f' · previous path {_code(item["old_path"])}' if item.get("old_path") else ''
        at_base = changes.get("graph_commit") == changes.get("base")
        absent = status == ("added" if at_base else "deleted")
        empty = "No static impact candidates identified."
        if not changes.get("impact_available"):
            empty = "Impact was not computed."
        elif absent:
            empty = "File absent at the graph revision; impact unavailable."
        impact = _items((_file_link("file:" + target, anchors) for target in item["impact"]), empty)
        if "comparison" in item:
            sections = []
            for side in ("base", "head"):
                data = item["comparison"][side]
                paths = _items((_code(name) for name in data["paths"]), "No recorded dependents.")
                sections.append(f"<h3>{side.title()} · {escape(data['state'])}</h3>" +
                                (paths if data["state"] == "analyzed" else ""))
            impact = "".join(sections)
        rows.append(f'<li data-change-status="{escape(status, quote=True)}"><p>Change · {escape(status)} · {_file_link("file:" + path, anchors)}{old}</p><p>Static impact candidates</p>{impact}</li>')
    statuses = sorted({item["status"] for item in files})
    options = ''.join(f'<option value="{escape(status, quote=True)}">{escape(status)}</option>' for status in statuses)
    return (availability + '<p>Changes are Git comparison evidence; impact paths are static candidates, not proven runtime effects.</p>'
            '<label for="change-filter">Filter changes by status</label> <select id="change-filter"><option value="">All statuses</option>' + options + '</select>'
            '<p id="change-empty" role="status" hidden>No changes match this filter.</p><ul>' + ''.join(rows) + '</ul>')


def _csp(template):
    allowed = []
    for tag, directive in (("style", "style-src"), ("script", "script-src")):
        content = re.search(f"<{tag}>(.*?)</{tag}>", template, re.S)[1]
        digest = base64.b64encode(hashlib.sha256(content.encode("utf-8")).digest()).decode("ascii")
        allowed.append(f"{directive} 'sha256-{digest}'")
    return "; ".join(["default-src 'none'", "base-uri 'none'", "form-action 'none'", *allowed])


def _audit_findings(findings, anchors, comparison=None):
    descriptions = {
        "unused-private-symbol": "Private symbol with no observed name reference",
        "unused-internal-export": "Opted-in internal export with no observed name reference",
        "duplicate-block": "Identical Python function bodies",
        "isolated-module": "Module without observed import connections",
    }
    def row(item):
        location = _file_link("file:" + item["path"], anchors) + ":" + str(item["line"])
        name = " · " + _code(item["name"]) if item.get("name") else ""
        locations = "".join(" · " + _file_link("file:" + loc["path"], anchors) + ":" + str(loc["line"]) + " " + _code(loc["name"]) for loc in item.get("locations", []))
        return escape(descriptions[item["rule"]]) + " · " + location + name + locations
    content = ('<h3>Source-backed candidates</h3><p>Review candidates, not confirmed defects. Python only; public exports are protected by default; explicitly opted-in internal directories are checked against declared Python exports. External consumers cannot be ruled out. Tests, fixtures, generated and vendor paths, package entry points, decorated definitions and referenced names are excluded. Dynamic loading and unscanned code can hide uses. Compare the cited source and callers before editing.</p>'
               + _items((row(item) for item in findings), "No candidates identified within these controls."))
    if comparison is not None:
        content += '<h3>Finding changes</h3><p>Static candidate changes between clean snapshots; line movement alone does not introduce a finding. Renames may appear as resolved and introduced.</p>'
        for status in ("introduced", "resolved", "unchanged"):
            content += '<h4>' + status.title() + '</h4>' + _items((row(item) for item in comparison[status]), 'None.')
    return content


def render(template, graph, commits, readonly_check, *, modules=None, tour=None, changes=None, findings=None, finding_delta=None):
    repo = graph["repo"]
    commit = _code(repo["commit"]) if repo["commit"] else "No scanned commit"
    state = {True: "dirty", False: "clean", None: "unknown"}[repo["dirty"]]
    files = [node for node in graph["nodes"] if node["type"] == "file"]
    anchors = {file["id"]: f"file-{index}" for index, file in enumerate(files)}
    edges = graph["edges"]
    statuses = Counter(edge["status"] for edge in edges)
    coverage = " · ".join(
        [f"{len(files)} parsed files", f'{len(graph["skipped"])} excluded']
        + [f"{statuses[status]} {status}" for status in ("verified", "inferred", "unresolved")]
    )
    return Template(template).substitute(
        csp=_csp(template),
        repo=escape(repo["name"]),
        snapshot=f"{commit} · worktree {state} · Git status comparison {_code(readonly_check)}",
        coverage=coverage,
        modules=_modules(modules),
        screen_nav='<a href="#screens">Screens and features</a>' if tour and tour.get("screens") else "",
        screens=_screens(tour, anchors),
        feature_focus=_feature_focus(tour, graph.get("http_connections", []), anchors),
        focus_nav='<a href="#feature-focus">Feature focus</a>' if tour and tour.get("steps") else "",
        tour=_tour(tour, anchors),
        files=_files(graph, files, anchors),
        http_nav='<a href="#http">HTTP connections</a>' if graph.get("http_connections") else "",
        http=_http_section(graph.get("http_connections", []), anchors),
        audit=_audit(edges, anchors) + _audit_findings(findings or [], anchors, finding_delta),
        changes=_changes(changes, anchors),
        history=_items(
            (f"{_code(item['commit'])} · {escape(item['date'])}" for item in commits or ()),
            "Git history unavailable for this snapshot.",
        ),
        excluded=_items(
            (f"{_code(item['path'])} · {escape(item['reason'])}" for item in graph["skipped"]),
            "No excluded files.",
        ),
    )
