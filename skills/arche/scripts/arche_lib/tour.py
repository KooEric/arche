"""Validation for the stdlib tour evidence format."""
from __future__ import annotations

import hashlib
import re
from typing import Callable, Mapping

from .errors import IntegrityError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EDGE_KINDS = frozenset({"calls", "reads", "writes", "artifact_transfer"})


def _bad(message: str) -> None:
    raise IntegrityError(f"invalid tour: {message}")


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _bad(f"{label} must be an object")
    return value


def _required(entry: Mapping[str, object], fields: tuple[str, ...], label: str) -> None:
    missing = [field for field in fields if field not in entry]
    if missing:
        _bad(f"{label} missing {', '.join(missing)}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _bad(f"{label} must be a non-empty string")
    return value


def _line_number(value: object, label: str) -> int:
    # bool is an int subclass, but is not a meaningful line number.
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        _bad(f"{label} must be a positive integer")
    return value


def validate_tour(
    tour: object,
    inventory: object,
    read_path: Callable[[str], object],
) -> None:
    """Validate a tour against an inventory and the current source files.

    Tour ranges are one-based and inclusive.  ``read_path`` is deliberately
    supplied by the caller so the validator can use the same snapshot-aware
    reader as the scan/import pipeline.
    """
    document = _mapping(tour, "tour")
    version = document.get("version")
    if type(version) is not int or version != 1:
        _bad("version must be 1")
    steps_value = document.get("steps")
    edges_value = document.get("edges")
    if not isinstance(steps_value, list):
        _bad("steps must be a list")
    if not isinstance(edges_value, list):
        _bad("edges must be a list")

    inventory_document = _mapping(inventory, "inventory")
    files = inventory_document.get("files")
    if not isinstance(files, list):
        _bad("inventory files must be a list")
    inventory_files: dict[str, Mapping[str, object]] = {}
    for index, raw_file in enumerate(files):
        entry = _mapping(raw_file, f"inventory.files[{index}]")
        path = _string(entry.get("path"), f"inventory.files[{index}].path")
        if path in inventory_files:
            _bad(f"duplicate inventory path: {path}")
        inventory_files[path] = entry

    for key in ("title", "summary"):
        if key in document:
            _string(document[key], key)

    step_ids: set[str] = set()
    for index, raw_step in enumerate(steps_value):
        label = f"steps[{index}]"
        step = _mapping(raw_step, label)
        _required(step, ("id", "path", "start_line", "end_line", "sha256"), label)
        step_id = _string(step["id"], f"{label}.id")
        for key in ("title", "note"):
            if key in step:
                _string(step[key], f"{label}.{key}")
        if step_id in step_ids:
            _bad(f"duplicate step id: {step_id}")
        step_ids.add(step_id)
        path = _string(step["path"], f"{label}.path")
        inventory_entry = inventory_files.get(path)
        if inventory_entry is None:
            _bad(f"unknown step path: {path}")
        if inventory_entry.get("status") == "skipped":
            _bad(f"step path is explicitly skipped in inventory: {path}")
        expected = _string(step["sha256"], f"{label}.sha256")
        if not _SHA256.fullmatch(expected):
            _bad(f"{label}.sha256 must be a SHA256 hex digest")
        inventory_hash = inventory_entry.get("sha256")
        if inventory_hash != expected:
            _bad(f"stale SHA256 for {path}")
        start = _line_number(step["start_line"], f"{label}.start_line")
        end = _line_number(step["end_line"], f"{label}.end_line")
        if start > end:
            _bad(f"{label} has an invalid line range")
        try:
            content = read_path(path)
        except IntegrityError:
            raise
        except Exception as exc:  # readers may expose filesystem errors directly
            raise IntegrityError(f"cannot read tour path {path}: {exc}") from None
        if content is None:
            _bad(f"cannot read tour path: {path}")
        data = content if isinstance(content, bytes) else str(content).encode("utf-8")
        if hashlib.sha256(data).hexdigest() != expected:
            _bad(f"stale SHA256 for {path}")
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        line_count = text.count("\n") + (0 if not text or text.endswith("\n") else 1)
        if end > line_count:
            _bad(f"{label} line range exceeds {path} ({line_count} lines)")

    for index, raw_edge in enumerate(edges_value):
        label = f"edges[{index}]"
        edge = _mapping(raw_edge, label)
        _required(edge, ("from", "to", "kind"), label)
        source = _string(edge["from"], f"{label}.from")
        target = _string(edge["to"], f"{label}.to")
        kind = _string(edge["kind"], f"{label}.kind")
        if source not in step_ids or target not in step_ids:
            _bad(f"{label} references an unknown step")
        if kind not in _EDGE_KINDS:
            _bad(f"{label}.kind is not supported: {kind}")

    screens = document.get("screens", [])
    if not isinstance(screens, list):
        _bad("screens must be a list")
    for screen in screens:
        screen = _mapping(screen, "screen")
        _string(screen.get("title"), "screen.title")
        if not isinstance(screen.get("items"), list) or not screen["items"]:
            _bad("screen.items must be a non-empty list")
        for item in screen["items"]:
            item = _mapping(item, "screen item")
            for key in ("label", "behavior", "step"):
                _string(item.get(key), "screen item." + key)
            if item["step"] not in step_ids:
                _bad("screen item references an unknown step")
