"""Render the static dependency graph without embedding source excerpts."""
import base64
import hashlib
import json
import posixpath
import re
from collections import Counter, defaultdict
from graphlib import CycleError, TopologicalSorter
from html import escape
from string import Template

from .focus import select_steps
from .i18n import translator

KEY_FILE_LIMIT = 8
LAZY_FILE_LIMIT = 500
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|specs?|testdata|fixtures)(/|$)|(^|/)(test_[^/]*|[^/]*_test|[^/]*\.(test|spec))\.[^/.]+$")
_SKIP_REASONS = {
    "unsupported_lang": "Unsupported language",
    "binary": "Binary file",
    "secret": "Possible secret",
    "symlink": "Symbolic link",
    "generated": "Generated file",
    "vendored": "Vendored code",
    "oversized": "Oversized file",
}

_translate = translator("en")


def T(text):
    """Interface text in the language of the report being rendered."""
    return escape(_translate(text))


def _code(text):
    return f"<code>{escape(str(text))}</code>"


def _items(items, empty, cls=""):
    rows = "".join(f"<li>{item}</li>" for item in items)
    attr = f' class="{cls}"' if cls else ""
    return f"<ul{attr}>{rows}</ul>" if rows else (f'<p class="empty">{empty}</p>' if empty else "")


def _file_link(node_id, anchors):
    caption = _code(node_id.removeprefix("file:"))
    if node_id in anchors:
        return f'<a href="#{anchors[node_id]}">{caption}</a>'
    return caption


def _evidence(edge):
    evidence = edge["evidence"]
    return _code(f"{evidence['path']}:{evidence['line']}")


def _status(status):
    status = escape(status)
    return f'<span class="status" data-state="{status}">{status}</span>'


def _relation(edge, other, anchors):
    label = _file_link(other, anchors) if other else _code(edge["spec"])
    table = f" · {_code(edge['table'])}" if "table" in edge and other else ""
    reason = f" · {escape(edge['reason'])}" if "reason" in edge else ""
    return f'{label}{table} · {_status(edge["status"])} · {_evidence(edge)}{reason}'


def _bar(value, largest):
    width = round(100 * value / largest, 1) if largest else 0
    return f'<svg class="bar" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true"><rect width="{width}" height="4"/></svg>'


def _module_of(path):
    return path.split("/", 1)[0] if "/" in path else "."


def _degrees(graph, anchors):
    """Distinct resolved file-to-file dependencies in each direction."""
    pairs = {(edge["from"], edge["to"]) for edge in graph["edges"]
             if edge["from"] in anchors and edge["to"] in anchors and edge["from"] != edge["to"]}
    fan_out, fan_in = Counter(), Counter()
    for source, target in pairs:
        fan_out[source] += 1
        fan_in[target] += 1
    return fan_in, fan_out


def _file_details(graph, files):
    """Per-file detail as data, for reports that draw a file only when it is opened."""
    index = {file["id"]: position for position, file in enumerate(files)}
    details = [{"s": [], "o": [], "x": []} for _ in files]
    for node in graph["nodes"]:
        if node["type"] == "symbol":
            path, symbol = node["id"].removeprefix("sym:").rsplit("#", 1)
            details[index[f"file:{path}"]]["s"].append([symbol, node["kind"], node["line"]])
    for edge in graph["edges"]:
        target = index.get(edge["to"], -1)
        details[index[edge["from"]]]["o"].append(
            [target, "" if target >= 0 else (edge["to"] or edge.get("spec", "")).removeprefix("file:"), edge["status"],
             edge["evidence"]["line"], edge.get("reason", "")])
    for item in graph["externals"]:
        details[index[item["from"]]]["x"].append([item["package"], item["line"]])
    return json.dumps(details, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _files(graph, files, anchors, fan_in=None, fan_out=None, lazy=False):
    incoming, outgoing, symbols, externals = (defaultdict(list) for _ in range(4))
    if lazy:
        graph = {"edges": [], "nodes": [], "externals": []}
    for edge in graph["edges"]:
        outgoing[edge["from"]].append(_relation(edge, edge["to"], anchors))
        if edge["to"] in anchors:
            incoming[edge["to"]].append(_relation(edge, edge["from"], anchors))
    for node in graph["nodes"]:
        if node["type"] == "symbol":
            path, symbol = node["id"].removeprefix("sym:").rsplit("#", 1)
            symbols[f"file:{path}"].append(
                f'{_code(symbol)} · {escape(node["kind"])} · {T("line")} {node["line"]:d}')
    for item in graph["externals"]:
        externals[item["from"]].append(f'{_code(item["package"])} · {T("line")} {item["line"]:d}')

    fan_in, fan_out = fan_in or Counter(), fan_out or Counter()
    rows = []
    for file in files:
        node_id = file["id"]
        raw = node_id.removeprefix("file:")
        path = escape(raw)
        rows.append(
            f'<details class="file" id="{anchors[node_id]}" data-file="{path}" data-module="{escape(_module_of(raw), quote=True)}">'
            f'<summary><span class="path">{path}</span> <span class="label">· {escape(file["lang"])} · {file["lines"]:d} lines</span>'
            f'<span class="degree" title="{T("Imported by")} / {T("Imports")}">{fan_in[node_id]} {T("in")} · {fan_out[node_id]} {T("out")}</span></summary>'
            + ('</details>' if lazy else
            '<div class="detail">'
            f'<div><h3>{T("Symbols")} <span class="count">{len(symbols[node_id])}</span></h3>{_items(symbols[node_id], T("No extracted symbols."))}</div>'
            f'<div><h3>{T("Imports")} <span class="count">{len(outgoing[node_id])}</span></h3>{_items(outgoing[node_id], T("No recorded imports."))}'
            f'<h3>{T("Imported by")} <span class="count">{len(incoming[node_id])}</span></h3>{_items(incoming[node_id], T("No recorded importers."))}'
            f'<h3>{T("External packages")} <span class="count">{len(externals[node_id])}</span></h3>{_items(externals[node_id], T("No recorded external packages."))}</div>'
            '</div></details>')
        )
    return "".join(rows) or f'<p class="empty">{T("No files parsed by the installed adapters.")}</p>'


def _modules(modules):
    if not modules:
        return f'<p class="empty">{T("No module inventory available.")}</p>'
    largest = max(item["parsed"] + item["excluded"] for item in modules)
    return '<div class="modules">' + ''.join(
        f'<button type="button" class="module" data-select-module="{escape(item["name"], quote=True)}">'
        f'<strong>{escape(item["name"])}</strong><span>{item["parsed"]} {T("parsed")} · {item["excluded"]} {T("excluded")}</span>'
        f'{_bar(item["parsed"], largest)}</button>'
        for item in modules
    ) + '</div>'


def _prose(text):
    return "".join(f"<p>{escape(part.strip())}</p>" for part in re.split(r"\n\s*\n", text) if part.strip())


def _tour(tour, anchors):
    if not tour or not tour["steps"]:
        return f'<p class="empty">{T("No tour available for this snapshot.")}</p>'
    incoming = defaultdict(list)
    for edge in tour["edges"]:
        incoming[edge["to"]].append(edge)
    rows = []
    for index, step in enumerate(tour["steps"], 1):
        relationships = _items(
            (f'{_code(edge["from"])} → {_code(edge["to"])} · {escape(edge["kind"])}' for edge in incoming[step["id"]]),
            T('Starting step; no incoming tour relationship.'),
        )
        title = f'<strong class="step-title">{escape(step["title"])}</strong>' if step.get("title") else ""
        note = f'<div class="prose">{_prose(step["note"])}</div>' if step.get("note") else ""
        rows.append(
            f'<li class="tour-step"><span class="step-number">{index:02d}</span><div class="step-body">'
            f'<p class="step-head">{title} {_code(step["id"])}</p>{note}'
            f'<p class="evidence">{T("Evidence")} · {_file_link("file:" + step["path"], anchors)}:'
            f'{step["start_line"]}–{step["end_line"]}</p>'
            f'<h3>{T("Relationships")}</h3>{relationships}'
            f'<p class="sha">SHA-256 · {_code(step["sha256"])}</p>'
            '</div></li>'
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
                         f'<span class="evidence">{source}:{step["start_line"]}–{step["end_line"]}</span></li>')
        cards.append(f'<details class="screen" open><summary>{escape(screen["title"])} <span class="count">{len(items)}</span></summary>'
                     f'<ul class="controls">{"".join(items)}</ul></details>')
    return "".join(cards)


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
            cards.append(f'<li><strong>{_code(sid)}</strong>{details}<p class="evidence">{T("Evidence")} · {source}:{selected_step["start_line"]}–{selected_step["end_line"]}</p></li>')
        relations = []
        for edge in selected["edges"]:
            relations.append(f'{_code(edge["from"])} → <strong>{escape(edge["kind"])}</strong> → {_code(edge["to"])}')
        for edge in selected["http_candidates"]:
            relations.append(f'{_code(edge["from"])} ⇢ <strong>{T("inferred HTTP")}</strong> {_code(edge["method"])} {_code(edge["path"])} ⇢ {_code(edge["to"])}')
        panels.append(f'<div data-focus-panel="{index}"' + ('' if index == 0 else ' hidden') + '>'
                      + f'<h3>{T("Selected steps")}</h3><ul class="focus-nodes">' + ''.join(cards) + '</ul>'
                      + f'<h3>{T("Connections")}</h3>' + _items(relations, T('No connected tour relationships within two hops.')) + '</div>')
    return ('<section id="feature-focus"><h2>' + T("Feature focus") + '</h2><p class="lead">'
            + T("Select a screen control or tour step to see connected evidence within two relationships. HTTP links are inferred from exact cited request and route or handler lines.") + '</p>'
            + f'<div class="focus-controls" aria-label="{T("Select feature or tour step")}">' + ''.join(buttons) + '</div>' + ''.join(panels) + '</section>')


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
            candidates.append(f"{target} · {T('route line')} {route['line']:d}, {T('handler line')} {route['handler_line']:d}")
        host = f" · {T('host')} {_code(request['host'])}" if request.get("host") else ""
        empty = "" if item["reason"] == "external_host" else T("No matching literal route identified.")
        rows.append(
            f"<p><span class=\"method\">{_code(request['method'])}</span> {_code(request['path'])}{host} · {_status(item['status'])}"
            f" ({escape(item['reason'])})</p><p>{source} · {T('request line')} {request['line']:d}</p>"
            + (_items(candidates, empty) if candidates or empty else "")
        )
    return (
        f'<section id="http"><h2>{T("HTTP requests and route handlers")}</h2>'
        f'<p class="lead">{T("Candidates share the HTTP method and path shape. A request to a named outside host is listed as external_host. The deployment host of this repository is unknown.")}</p>'
        + _items(rows, "", "cards") + "</section>"
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
    duplicate_report = (f'<h3>{T("Repeated static imports")} <span class="count">{len(duplicates)}</span></h3><p class="lead">'
        + T("Distinct locations importing the same target may be redundant; aliases and separate uses can be intentional. Review each location before changing it.") + '</p>'
        + _items(duplicates, T('No repeated verified unconditional imports at distinct locations.')))
    try:
        sorter.prepare()
    except CycleError as exc:
        cycle = exc.args[1]
        return (
            '<p class="callout" data-tone="warn">'
            + T("One observed static import cycle. Runtime impact depends on initialization order and language semantics; review the listed edges.") + "</p>"
            + _items(
                (
                    f"{_file_link(source, anchors)} → {_file_link(target, anchors)} · {_evidence(pairs[source, target])}"
                    for source, target in zip(cycle, cycle[1:])
                ),
                "",
            )
            + duplicate_report
        )
    return f'<p class="callout" data-tone="ok">{T("No cycle found among verified unconditional static imports.")}</p>' + duplicate_report


def _changes(changes, anchors):
    if changes is None:
        return f'<p class="empty">{T("No change comparison available for this snapshot.")}</p>'
    files = changes["files"]
    availability = "" if changes.get("impact_available") else f'<p class="callout" data-tone="warn">{T("Static impact unavailable for this graph snapshot.")}</p>'
    if not files:
        return availability + f'<p class="empty">{T("No changed files in this comparison.")}</p>'
    rows = []
    for item in files:
        status, path = item["status"], item["path"]
        old = f' · {T("previous path")} {_code(item["old_path"])}' if item.get("old_path") else ''
        at_base = changes.get("graph_commit") == changes.get("base")
        absent = status == ("added" if at_base else "deleted")
        empty = T("No static impact candidates identified.")
        if not changes.get("impact_available"):
            empty = T("Impact was not computed.")
        elif absent:
            empty = T("File absent at the graph revision; impact unavailable.")
        impact = _items((_file_link("file:" + target, anchors) for target in item["impact"]), empty)
        if "comparison" in item:
            sections = []
            for side in ("base", "head"):
                data = item["comparison"][side]
                paths = _items((_code(name) for name in data["paths"]), T("No recorded dependents."))
                sections.append(f"<h3>{side.title()} · {escape(data['state'])}</h3>" +
                                (paths if data["state"] == "analyzed" else ""))
            impact = "".join(sections)
        rows.append(f'<li data-change-status="{escape(status, quote=True)}"><p>{T("Change")} · <span class="change" data-kind="{escape(status, quote=True)}">{escape(status)}</span> · {_file_link("file:" + path, anchors)}{old}</p><p class="label">{T("Static impact candidates")}</p>{impact}</li>')
    statuses = sorted({item["status"] for item in files})
    options = ''.join(f'<option value="{escape(status, quote=True)}">{escape(status)}</option>' for status in statuses)
    return (availability + f'<p class="lead">{T("Changes are Git comparison evidence; impact paths are static candidates, not proven runtime effects.")}</p>'
            f'<label for="change-filter">{T("Filter changes by status")}</label> <select id="change-filter"><option value="">{T("All statuses")}</option>' + options + '</select>'
            f'<p id="change-empty" class="empty" role="status" hidden>{T("No changes match this filter.")}</p><ul class="cards">' + ''.join(rows) + '</ul>')


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
        "unused-private-member": "Private Java or Kotlin member mentioned nowhere else in its file",
        "unused-nonpublic-type": "Non-public Java or Kotlin type named nowhere else",
    }
    def row(item):
        location = _file_link("file:" + item["path"], anchors) + ":" + str(item["line"])
        name = " · " + _code(item["name"]) if item.get("name") else ""
        locations = "".join(" · " + _file_link("file:" + loc["path"], anchors) + ":" + str(loc["line"]) + " " + _code(loc["name"]) for loc in item.get("locations", []))
        return T(descriptions[item["rule"]]) + " · " + location + name + locations
    content = (f'<h3>{T("Source-backed candidates")} <span class="count">{len(findings)}</span></h3><p class="lead">'
               + T("Review candidates, not confirmed defects. For Python, public exports are protected by default; explicitly opted-in internal directories are checked against declared Python exports. External consumers cannot be ruled out. Tests, fixtures, generated and vendor paths, package entry points, decorated definitions and referenced names are excluded. Dynamic loading and unscanned code can hide uses. Compare the cited source and callers before editing. For Java and Kotlin, the candidates are private members and non-public top-level types whose name appears nowhere else, including strings and configuration files; annotated declarations are left out, and reflection or generated code can still use them. For TypeScript, JavaScript, Dart and Go, the candidates are declarations that are not exported and whose name appears once in their file, library or package.") + '</p>'
               + _items((row(item) for item in findings), T("No candidates identified within these controls.")))
    if comparison is not None:
        content += f'<h3>{T("Finding changes")}</h3><p class="lead">' + T("Static candidate changes between clean snapshots; line movement alone does not introduce a finding. Renames may appear as resolved and introduced.") + '</p>'
        for status in ("introduced", "resolved", "unchanged"):
            content += '<h4>' + T(status.title()) + '</h4>' + _items((row(item) for item in comparison[status]), T('None.'))
    return content


def _key_files(files, anchors, fan_in, fan_out):
    def card(title, hint, ranked, unit):
        ranked = ranked[:KEY_FILE_LIMIT]
        largest = ranked[0][1] if ranked else 0
        rows = _items(
            (f'<span class="rank-name">{_file_link(node_id, anchors)}</span><span class="rank-value">{value:,} {unit}</span>{_bar(value, largest)}'
             for node_id, value in ranked),
            T("None identified."), "rank")
        return f'<div class="card"><h3>{title}</h3><p class="lead">{hint}</p>{rows}</div>'

    by_path = sorted(file["id"] for file in files)
    entries = sorted(((node_id, fan_out[node_id]) for node_id in by_path
                      if fan_out[node_id] and not fan_in[node_id] and not _TEST_PATH.search(node_id.removeprefix("file:"))),
                     key=lambda pair: -pair[1])
    hubs = sorted(((node_id, fan_in[node_id]) for node_id in by_path if fan_in[node_id]), key=lambda pair: -pair[1])
    largest = sorted(((file["id"], file["lines"]) for file in sorted(files, key=lambda file: file["id"]) if file["lines"]),
                     key=lambda pair: -pair[1])
    return ('<div class="card-grid">'
            + card(T("Entry point candidates"), T("Nothing imports these files, and they import others. Tests are left out."), entries, T("imports"))
            + card(T("Most depended on"), T("Changes here reach the most files."), hubs, T("importers"))
            + card(T("Largest files"), T("Most lines among parsed files."), largest, T("lines"))
            + '</div>')


def _stats(graph, files):
    statuses = Counter(edge["status"] for edge in graph["edges"])
    edges = sum(statuses.values())
    symbols = sum(1 for node in graph["nodes"] if node["type"] == "symbol")
    packages = len({item["package"] for item in graph["externals"]})
    lines = sum(file["lines"] for file in files)

    def tile(label, value, extra=""):
        return f'<div class="stat"><span class="stat-label">{label}</span><strong>{value:,}</strong>{extra}</div>'

    split = ""
    if edges:
        offset, rects = 0.0, []
        for status in ("verified", "inferred", "unresolved"):
            width = round(100 * statuses[status] / edges, 2)
            rects.append(f'<rect class="seg" data-state="{status}" x="{round(offset, 2)}" width="{width}" height="6"/>')
            offset += width
        split = '<svg class="split" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">' + "".join(rects) + '</svg>'
    legend = '<span class="stat-note">' + " · ".join(
        f'{statuses[status]:,} {_status(status)}' for status in ("verified", "inferred", "unresolved")) + '</span>'
    tiles = (tile(T("Parsed files"), len(files)) + tile(T("Lines"), lines) + tile(T("Symbols"), symbols)
             + tile(T("External packages"), packages) + tile(T("Excluded"), len(graph["skipped"]))
             )
    dependencies = f'<div class="stat"><span class="stat-label">{T("Dependencies")}</span><strong>{edges:,}</strong>{split}{legend}</div>'

    by_lang = Counter()
    for file in files:
        by_lang[file["lang"]] += file["lines"]
    languages = ""
    if lines:
        offset, rects, keys = 0.0, [], []
        for index, (lang, count) in enumerate(sorted(by_lang.items(), key=lambda pair: (-pair[1], pair[0]))):
            width = round(100 * count / lines, 2)
            rects.append(f'<rect class="lang-{index % 6}" x="{round(offset, 2)}" width="{width}" height="8"/>')
            keys.append(f'<li><svg class="dot lang-{index % 6}" viewBox="0 0 8 8" aria-hidden="true"><circle cx="4" cy="4" r="4"/></svg>'
                        f'{escape(lang)} <span class="label">{round(100 * count / lines)}%</span></li>')
            offset += width
        languages = (f'<div class="stat"><span class="stat-label">{T("Languages by lines")}</span>'
                     '<svg class="split tall" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true">' + "".join(rects) + '</svg>'
                     '<ul class="legend">' + "".join(keys) + '</ul></div>')
    coverage = " · ".join(
        [f"{len(files)} parsed files", f'{len(graph["skipped"])} excluded']
        + [f"{statuses[status]} {status}" for status in ("verified", "inferred", "unresolved")])
    return f'<div class="stats" title="{escape(coverage, quote=True)}">{tiles}</div><div class="stats pair">{dependencies}{languages}</div>'


def _history(commits):
    rows = []
    for item in commits or ():
        commit = item["commit"]
        subject = f' <span class="subject">{escape(item["subject"])}</span>' if item.get("subject") else ""
        rows.append(f'<code title="{escape(commit, quote=True)}">{escape(commit[:10])}</code>{subject}'
                    f' <time class="label" datetime="{escape(item["date"], quote=True)}">{escape(item["date"][:10])}</time>')
    return _items(rows, T("Git history unavailable for this snapshot."), "history")


def _excluded(skipped):
    if not skipped:
        return f'<p class="empty">{T("No excluded files.")}</p>'
    groups = defaultdict(list)
    for item in skipped:
        groups[item["reason"]].append(item["path"])
    blocks = []
    for reason, paths in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        label = T(_SKIP_REASONS[reason]) if reason in _SKIP_REASONS else escape(reason)
        blocks.append(f'<details class="group"><summary>{label} <code>{escape(reason)}</code> <span class="count">{len(paths)}</span></summary>'
                      + _items((_code(path) for path in paths), "", "columns") + '</details>')
    return f'<p class="lead">{T("Files left out of parsing, grouped by reason.")}</p>' + "".join(blocks)


_UNRESOLVED_REFERENCES = (
    ("dynamic", "Paths built at run time"),
    ("ambiguous", "Paths matching several files"),
    ("not_found", "Paths matching no scanned file"),
)


def _folded_files(node_ids, anchors):
    """File links grouped by parent folder, the largest folder first."""
    if not node_ids:
        return f'<p class="empty">{T("Every SQL file is named by a parsed file.")}</p>'
    folders = defaultdict(list)
    for node_id in node_ids:
        folders[posixpath.dirname(node_id.removeprefix("file:")) or "."].append(node_id)
    return "".join(
        f'<details class="group"><summary>{_code(folder + "/")} <span class="count">{len(members)}</span></summary>'
        + _items((_file_link(node_id, anchors) for node_id in members), "", "columns") + '</details>'
        for folder, members in sorted(folders.items(), key=lambda pair: (-len(pair[1]), pair[0])))


def _file_references(edges, files, anchors):
    """SQL files nothing names, and written paths that were not followed. Empty without SQL or references."""
    sql = [file["id"] for file in files if file["lang"] == "sql"]
    references = [edge for edge in edges if edge.get("kind") == "REFERENCES"]
    if not sql and not references:
        return ""
    named = {edge["to"] for edge in references if edge["to"]}
    unnamed = [node_id for node_id in sql if node_id not in named]
    by_reason = defaultdict(list)
    for edge in references:
        if not edge["to"]:
            by_reason[edge["reason"]].append(f'{_code(edge["spec"])} · {_evidence(edge)}')
    dynamic = len(by_reason["dynamic"])
    warning = ""
    if dynamic:
        counted = T("1 path is built at run time.") if dynamic == 1 else T("{count} paths are built at run time.").replace("{count}", f"{dynamic:,}")
        warning = f'<p class="callout" data-tone="warn">{counted} {T("A file listed here may still be used through one of them.")}</p>'
    body = (f'<h3>{T("SQL files with no static reference")} <span class="count">{len(unnamed)}</span></h3>' + warning
            + _folded_files(unnamed, anchors))
    for reason, title in _UNRESOLVED_REFERENCES:
        if by_reason[reason]:
            body += (f'<details class="group"><summary>{T(title)} <code>{reason}</code> <span class="count">{len(by_reason[reason])}</span></summary>'
                     + _items(by_reason[reason], "", "columns") + '</details>')
    return _section("file-references", T("SQL file references"), body,
                    T("A reference is a SQL file path written as text in a parsed Python file. Paths in configuration files and includes inside SQL are not read, so a file listed here may still be used. Open a SQL file under File dependencies to see what names it. Which operator or task runs it is not read."))


_UNRESOLVED_READS = (
    ("dynamic", "Table names built at run time"),
    ("ambiguous", "Table names matching several tables"),
)


def _external_tables(external, anchors):
    """External tables grouped by schema, each with the files reading it."""
    readers = defaultdict(list)
    for item in external:
        location = _code(item["from"].removeprefix("file:") + ":" + str(item["line"]))
        readers[item["table"]].append(f'{_file_link(item["from"], anchors)} {location}')
    schemas = defaultdict(list)
    for table in sorted(readers):
        schemas[table.rpartition(".")[0]].append(table)
    blocks = []
    for schema, tables in sorted(schemas.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        label = _code(schema) if schema else T("no schema")
        rows = (f'{_code(table)} · ' + ", ".join(readers[table]) for table in tables)
        blocks.append(f'<details class="group"><summary>{label} <span class="count">{len(tables)}</span></summary>'
                      + _items(rows, "") + '</details>')
    return "".join(blocks)


def _tables(graph, anchors):
    """Tables read here and written nowhere scanned, and table names that were not followed."""
    external = graph.get("external_tables", [])
    reads = [edge for edge in graph["edges"] if edge.get("kind") == "READS"]
    if not external and not reads:
        return ""
    by_reason = defaultdict(list)
    for edge in reads:
        if not edge["to"]:
            by_reason[edge["reason"]].append(f'{_code(edge["spec"])} · {_evidence(edge)}')
    count = len({item["table"] for item in external})
    body = (f'<h3>{T("External tables")} <span class="count">{count}</span></h3>'
            + (_external_tables(external, anchors) or f'<p class="empty">{T("Every table read here is written by a scanned SQL file.")}</p>'))
    for reason, title in _UNRESOLVED_READS:
        if by_reason[reason]:
            body += (f'<details class="group"><summary>{T(title)} <code>{reason}</code> <span class="count">{len(by_reason[reason])}</span></summary>'
                     + _items(by_reason[reason], "", "columns") + '</details>')
    return _section("tables", T("Tables"), body,
                    T("A table is external when no scanned SQL file writes it. Only table names written in SQL files are read. SQL inside Python, default schemas and tables inside views are not, so a table listed here may still be made in this repository. Open a SQL file under File dependencies to see which files read what it writes."))


def _graph_data(graph, files, anchors):
    """Compact file-level graph for the interactive map. No source text."""
    index = {file["id"]: position for position, file in enumerate(files)}
    pairs = {}
    for edge in graph["edges"]:
        source, target = index.get(edge["from"]), index.get(edge["to"])
        if source is None or target is None or source == target:
            continue
        entry = pairs.setdefault((source, target), [0, 0])
        entry[0] += 1
        entry[1] |= edge["status"] != "verified"
    data = {
        "files": [[file["id"].removeprefix("file:"), file["lines"]] for file in files],
        "edges": [[source, target, count, inferred] for (source, target), (count, inferred) in sorted(pairs.items())],
    }
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _section(section_id, title, body, lead=""):
    lead = f'<p class="lead">{lead}</p>' if lead else ""
    return f'<section id="{section_id}"><h2>{title}</h2>{lead}{body}</section>'


def render(template, graph, commits, readonly_check, *, modules=None, tour=None, changes=None, findings=None, finding_delta=None, lang="en",
           lazy_file_limit=LAZY_FILE_LIMIT):
    global _translate
    previous, _translate = _translate, translator(lang)
    try:
        return _render(template, graph, commits, readonly_check, modules, tour, changes, findings or [], finding_delta, lang, lazy_file_limit)
    finally:
        _translate = previous


def _render(template, graph, commits, readonly_check, modules, tour, changes, findings, finding_delta, lang, lazy_file_limit):
    repo = graph["repo"]
    files = [node for node in graph["nodes"] if node["type"] == "file"]
    anchors = {file["id"]: f"file-{index}" for index, file in enumerate(files)}
    edges = graph["edges"]
    connections = graph.get("http_connections", [])
    fan_in, fan_out = _degrees(graph, anchors)

    state = {True: "dirty", False: "clean", None: "unknown"}[repo["dirty"]]
    commit = (f'{T("Commit")} <code title="{escape(repo["commit"], quote=True)}">{escape(repo["commit"][:10])}</code>'
              if repo["commit"] else T("No scanned commit"))
    snapshot = (f'<span class="chip">{commit}</span><span class="chip" data-state="{state}">{T("worktree")} {T(state)}</span>'
                f'<span class="chip" data-check="{escape(readonly_check, quote=True)}">{T("Git status comparison")} {_code(readonly_check)}</span>')

    summary = ""
    if tour and (tour.get("title") or tour.get("summary")):
        heading = escape(tour["title"]) if tour.get("title") else T("Summary")
        summary = _section("summary", heading, f'<div class="prose">{_prose(tour.get("summary", ""))}</div>')
    screens = _screens(tour, anchors)
    has_tour = bool(tour and tour.get("steps"))
    # Past this many files the details are sent as data and drawn when a file is opened.
    lazy = len(files) > lazy_file_limit
    file_list = ((f'<noscript><p class="callout" data-tone="warn">{T("This report draws file details with JavaScript. Render again with --full-details to read them without it.")}</p></noscript>' if lazy else "") +
        f'<div class="filter-bar"><label for="search" hidden>{T("Filter by file path")}</label>'
        f'<input type="search" id="search" placeholder="{T("Filter by file path…")}" hidden>'
        f'<p id="module-filter">{T("All modules")}</p><button type="button" id="clear-module" hidden>{T("Show all modules")}</button></div>'
        f'<p id="empty" class="empty" role="status" hidden>{T("No matching files.")}</p>'
        f'<div class="file-list">{_files(graph, files, anchors, fan_in, fan_out, lazy)}</div>')
    dependency_map = (
        f'<div id="map" class="map" hidden><div class="map-bar"><div class="map-crumbs" data-map-crumbs></div>'
        f'<button type="button" data-map-reset>{T("Reset")}</button></div>'
        '<div class="map-canvas" data-map-canvas tabindex="0"></div></div>'
        f'<noscript><p class="empty">{T("The dependency map needs JavaScript.")}</p></noscript>')

    cycle_count = 1 if 'data-tone="warn"' in (audit_cycles := _audit(edges, anchors)) else 0
    sections = [
        ("overview", T("Overview"), None, None),
        ("summary", T("Summary"), None, summary),
        ("key-files", T("Key files"), None, _section("key-files", T("Key files"), _key_files(files, anchors, fan_in, fan_out),
                                                     T("Where to start reading. Ranked from the static import graph."))),
        ("dependency-map", T("Dependency map"), None, _section("dependency-map", T("Dependency map"), dependency_map,
            T("An arrow from A to B means A imports B. Select a folder to open it, or a file to jump to its details.")) if len(files) > 1 else ""),
        ("modules", T("Modules"), len(modules or ()), _section("modules", T("Module map"), _modules(modules),
            T("Select a module to explore its parsed files. Excluded counts include unsupported files."))),
        ("screens", T("Screens and features"), len(tour["screens"]) if screens else None,
         _section("screens", T("Screens and features"), screens, T("Each control is described from source and linked to its evidence.")) if screens else ""),
        ("tour", T("Tour"), len(tour["steps"]) if has_tour else None,
         _section("tour", T("Evidence tour"), _tour(tour, anchors), T("Follow the steps in order. Each one cites its source location.") if has_tour else "")),
        ("feature-focus", T("Feature focus"), None, _feature_focus(tour, connections, anchors)),
        ("http", T("HTTP connections"), len(connections), _http_section(connections, anchors)),
        ("file-references", T("SQL file references"),
         sum(1 for edge in edges if edge.get("kind") == "REFERENCES" and edge["to"]) or None,
         _file_references(edges, files, anchors)),
        ("tables", T("Tables"), len({item["table"] for item in graph.get("external_tables", [])}) or None,
         _tables(graph, anchors)),
        ("files", T("Dependencies"), len(files), _section("files", T("File dependencies"), file_list)),
        ("changes", T("Changes"), len(changes["files"]) if changes else None, _section("changes", T("Changes and impact"), _changes(changes, anchors))),
        ("audit", T("Audit"), (len(findings) + cycle_count) or None,
         _section("audit", T("Audit"), audit_cycles + _audit_findings(findings, anchors, finding_delta))),
        ("history", T("History"), None, _section("history", T("Recent Git history"), _history(commits))),
        ("excluded", T("Excluded files"), len(graph["skipped"]) or None, _section("excluded", T("Excluded files"), _excluded(graph["skipped"]))),
    ]
    nav = "".join(
        f'<a href="#{section_id}">{label}' + (f' <span class="count">{count:,}</span>' if count else "") + '</a>'
        for section_id, label, count, body in sections if body is None or body)
    ui = {"symbols": "Symbols", "imports": "Imports", "importedby": "Imported by", "packages": "External packages", "line": "line",
          "nosymbols": "No extracted symbols.", "noimports": "No recorded imports.", "noimporters": "No recorded importers.",
          "nopackages": "No recorded external packages.",
          "all": "All modules", "module": "Module: ", "close": "Close", "more": "more files", "files": "files",
          "isolated": "No connections", "none": "No resolved imports between these files."}
    return Template(template).substitute(
        csp=_csp(template),
        lang=escape(lang, quote=True),
        repo=escape(repo["name"]),
        report_label=T("Repository report"),
        nav_label=T("Report sections"),
        theme_label=T("Toggle dark mode"),
        theme_text=T("Theme"),
        footer=T("Generated offline by Arche. Static analysis shows candidates, not runtime behavior."),
        nav=nav,
        snapshot=snapshot,
        stats=_stats(graph, files),
        sections="".join(body for _, _, _, body in sections if body),
        ui=" ".join(f'data-t-{key}="{T(text)}"' for key, text in ui.items()),
        graph_data=_graph_data(graph, files, anchors),
        file_details=_file_details(graph, files) if lazy else "null",
    )
