"""Render offline map nodes, edges and accessible source links."""
from collections import Counter, defaultdict
from html import escape
import json

from .focus import select_steps


def _diagram(nodes, links, label):
    if not nodes:
        return ""
    # The visible HTML remains navigable if Canvas is unavailable.
    data = escape(json.dumps(links, ensure_ascii=False), quote=True)
    cards = []
    incoming = Counter(dst for _, dst, _ in links)
    outgoing = defaultdict(list)
    for src, dst, _ in links:
        outgoing[src].append(dst)
    pending = [key for key, _, _, _ in nodes if not incoming[key]]
    depths = {}
    for key in pending:
        for target in outgoing[key]:
            depths[target] = max(depths.get(target, 0), depths.get(key, 0) + 1)
            incoming[target] -= 1
            if not incoming[target]:
                pending.append(target)
    columns = defaultdict(list)
    flat = not any(depths.values()) and len(nodes) > 2
    for index, (key, title, subtitle, action) in enumerate(nodes):
        column = index % 3 if flat else depths.get(key, 0)
        columns[column].append(
            f'<article class="diagram-card" data-node="{escape(key, quote=True)}">'
            f'<strong>{escape(title)}</strong><p>{escape(subtitle)}</p>{action}</article>')
    ordered = ['<div class="diagram-column">' + "".join(columns[key]) + "</div>"
               for key in sorted(columns)]
    lanes = []
    for index in range(0, len(ordered), 4):
        items = ordered[index:index + 4]
        lane_class = "diagram-lane diagram-lane-reverse" if (index // 4) % 2 else "diagram-lane"
        lanes.append(f'<div class="{lane_class}">' + "".join(items) + "</div>")
    column_html = "".join(lanes)
    return (f'<div class="diagram-scroll" tabindex="0" aria-label="{escape(label, quote=True)}">'
            f'<div class="diagram" data-links="{data}"><canvas aria-hidden="true"></canvas>'
            f'{column_html}</div></div>')


def system_map(modules, graph, tour=None, anchors=None):
    visible = sorted((m for m in modules or () if m["parsed"]), key=lambda m: (-m["parsed"], m["name"]))[:8]
    overview = tour.get("overview", {}) if tour else {}
    roles = {item["module"]: item for item in overview.get("modules", [])}
    steps = {step["id"]: step for step in tour.get("steps", [])} if tour else {}
    analyzed = sum(bool(m["parsed"]) for m in modules or ())
    if roles:
        visible = [m for m in modules if m["parsed"] and m["name"] in roles][:8] or visible
    names = {m["name"] for m in visible}
    counts = Counter()
    def module(node):
        path = node.removeprefix("file:")
        return path.split("/", 1)[0] if "/" in path else "."
    for edge in graph["edges"]:
        source, target = edge.get("from"), edge.get("to")
        if not source or not target or edge["status"] not in ("verified", "inferred"):
            continue
        a, b = module(source), module(target)
        if a in names and b in names and a != b:
            counts[a, b, edge["status"]] += 1
    for conn in graph.get("http_connections", ()):
        if conn["status"] == "inferred" and len(conn["candidates"]) == 1:
            a = module(conn["request"]["file"])
            b = module(conn["candidates"][0]["file"])
            if a in names and b in names and a != b:
                counts[a, b, "inferred HTTP"] += 1
    nodes = []
    for m in visible:
        name = m["name"]
        action = f'<button type="button" data-select-module="{escape(name, quote=True)}">Browse files</button>'
        role = roles.get(name, {})
        title = role.get("title", "Repository root" if name == "." else name)
        subtitle = role.get("description", "")
        facts = f'{name} · {m["parsed"]} parsed files'
        evidence = ""
        if role:
            step = steps[role["step"]]
            anchor = (anchors or {}).get("file:" + step["path"])
            if anchor:
                evidence = f'<a href="#{anchor}">Source · {step["start_line"]}–{step["end_line"]}</a>'
        nodes.append((name, title, subtitle, f'<code class="diagram-facts">{escape(facts)}</code>{action}{evidence}'))
    links = [(a, b, kind) for a, b, kind in sorted(counts)]
    diagram = _diagram(nodes, links, "Repository dependency diagram")
    if not diagram:
        return ""
    summary = "".join(f'<li>{escape(a)} → {escape(b)} · {escape(kind)} · {count}</li>'
                      for (a, b, kind), count in sorted(counts.items()))
    return ('<section id="architecture"><h2>System map</h2>'
            + (f'<p class="map-note">{escape(overview["summary"])}</p>' if overview.get("summary") else "")
            + '<p class="map-note">Parsed modules and recorded dependencies. Select a card to browse its files.</p>'
            '<p class="map-key"><span>Static imports</span><span>Inferred imports / HTTP</span></p>'
            + diagram + ('<details><summary>Module connections</summary><ul>' + summary + '</ul></details>'
                         if summary else '<p class="map-note">No cross-module connections recorded in this map.</p>')
            + (f'<p class="map-note"><a href="#modules">All modules</a> · Showing {len(visible)} of {analyzed} parsed modules.</p>'
               if analyzed > len(visible) else '') + '</section>')


def feature_map(tour, connections, anchors):
    if not tour or not tour["steps"]:
        return ""
    labels = {}
    for screen in tour.get("screens", ()):
        for item in screen["items"]:
            labels[item["step"]] = (f'{screen["title"]} · {item["label"]}', item["behavior"])
    choices = list(labels) or [s["id"] for s in tour["steps"]]
    if not choices:
        return ""
    views = [select_steps(tour, {key}, 8, connections) for key in choices]
    default = max(range(len(views)), key=lambda index: len(views[index]["steps"]))
    options, panels = [], []
    for index, (key, tour_part) in enumerate(zip(choices, views)):
        option = labels.get(key, (key, ""))[0]
        options.append(f'<option value="{index}"{" selected" if index == default else ""}>{escape(option)}</option>')
        nodes = []
        for step in tour_part["steps"]:
            sid, path = step["id"], step["path"]
            title, behavior = labels.get(sid, (step.get("label", sid.replace("_", " ")), step.get("description", "")))
            anchor = anchors.get("file:" + path)
            start, end = step["start_line"], step["end_line"]
            line_range = str(start) if start == end else f"{start}–{end}"
            source = f'{escape(path.rsplit("/", 1)[-1])}:{line_range}'
            action = f'<a href="#{anchor}" title="{escape(path, quote=True)}"><code>{source}</code></a>' if anchor else f'<code>{source}</code>'
            nodes.append((sid, title, behavior, action))
        links = [(e["from"], e["to"], e["kind"]) for e in tour_part["edges"] + tour_part["http_candidates"]]
        steps = "".join(f'<li>{escape(src)} → {escape(dst)} · {escape(kind.replace("_", " "))}</li>'
                        for src, dst, kind in links)
        panels.append(f'<div data-diagram-view="{index}"{" hidden" if index != default else ""}>'
                      + _diagram(nodes, links, "Feature relationships")
                      + ('<details><summary>Read relationships</summary><ul>' + steps + '</ul></details>' if steps else '')
                      + '</div>')
    return ('<section id="visual-flow"><h2>Feature flow</h2><label for="diagram-choice">Feature</label> '
            '<select id="diagram-choice">' + "".join(options) + '</select>'
            '<p class="map-key"><span>Source-backed tour steps</span><span>Inferred HTTP</span></p>'
            + "".join(panels) + '</section>')
