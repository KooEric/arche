"""Select and render focused, source-cited tour relationships."""
from collections import defaultdict
from .errors import UsageError


def http_steps(tour, connections):
    """Join unique method/path candidates to *cited lines*, never all steps in a file."""
    def containing(path, line):
        return [s["id"] for s in tour["steps"] if s["path"] == path and s["start_line"] <= line <= s["end_line"]]
    links = []
    for conn in connections:
        if conn["status"] != "inferred" or len(conn["candidates"]) != 1:
            continue
        request, route = conn["request"], conn["candidates"][0]
        for source in containing(request["file"], request["line"]):
            targets = containing(route["file"], route["handler_line"]) or containing(route["file"], route["line"])
            for target in targets:
                links.append({"from": source, "to": target, "kind": "http_candidate", "status": "inferred",
                              "method": request["method"], "path": request["path"]})
    return links


def mermaid(tour):
    def label(text):
        return "".join(c if c.isalnum() or c in " _/.:-" else f"#{ord(c)};" for c in text)
    lines = ["flowchart LR"]
    ids = {}
    for i, step in enumerate(tour["steps"]):
        ids[step["id"]] = f"n{i}"
        text = f"{step['id']} · {step['path']}:{step['start_line']}-{step['end_line']}"
        lines.append(f'  n{i}["{label(text)}"]')
    for edge in tour["edges"]:
        lines.append(f'  {ids[edge["from"]]} -->|{edge["kind"]}| {ids[edge["to"]]}')
    for edge in tour["http_candidates"]:
        lines.append(f'  {ids[edge["from"]]} -. inferred HTTP .-> {ids[edge["to"]]}')
    return "\n".join(lines) + "\n"


def select(tour, query, hops, connections=()):
    query = query.casefold().strip()
    if not query:
        raise UsageError("feature query cannot be empty")
    matches = {s["id"] for s in tour["steps"] if query in s["id"].casefold() or query in s["path"].casefold()}
    for screen in tour.get("screens", []):
        for item in screen["items"]:
            if any(query in value.casefold() for value in (screen["title"], item["label"], item["behavior"])):
                matches.add(item["step"])
    if not matches:
        raise UsageError("feature not found in this evidence tour")
    return select_steps(tour, matches, hops, connections)


def select_steps(tour, matches, hops, connections=()):
    """Expand exact step IDs by relationship hops."""
    matches = set(matches)
    neighbors = defaultdict(set)
    candidates = http_steps(tour, connections)
    for edge in tour["edges"] + candidates:
        neighbors[edge["from"]].add(edge["to"])
        neighbors[edge["to"]].add(edge["from"])
    selected = matches.copy()
    frontier = matches
    for _ in range(hops):
        frontier = {other for key in frontier for other in neighbors[key]} - selected
        selected.update(frontier)
        if not frontier:
            break
    screens = []
    for screen in tour.get("screens", []):
        items = [i for i in screen["items"] if i["step"] in selected]
        if items:
            screens.append(dict(screen, items=items))
    return {"version": 1, "http_candidates": [e for e in candidates if e["from"] in selected and e["to"] in selected],
            "screens": screens, "steps": [s for s in tour["steps"] if s["id"] in selected],
            "edges": [e for e in tour["edges"] if e["from"] in selected and e["to"] in selected]}
