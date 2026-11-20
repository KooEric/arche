"""Deterministic JSON output and checked JSON input."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import IntegrityError


def dumps(data: Any) -> str:
    """Serialize so that equal data always produces identical bytes."""
    return json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, data: Any) -> None:
    write_text(path, dumps(data))


def write_text(path: Path, content: str) -> None:
    """Write atomically: a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(temp_name, path)
    except BaseException:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def read_json(path: Path) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise IntegrityError(f"missing input file: {path}") from None
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"invalid JSON in {path}: {exc}") from None

def read_snapshot(path: Path) -> dict:
    """Read legacy and 1.0 snapshots; leave archived documents intact."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise IntegrityError(f"invalid snapshot in {path.name}")
    version = data.get("schema_version", "0.1")
    if version not in ("0.1", "1.0"):
        raise IntegrityError(f"unsupported schema_version in {path.name}: {version!r}")
    return data


def inventory_digest(inventory: dict) -> str:
    # Keep absolute checkout paths out of the portable snapshot identifier.
    files = sorted(inventory["files"], key=lambda entry: entry["path"])
    return hashlib.sha256(dumps(files).encode("utf-8")).hexdigest()


def check_graph(inventory: dict, graph: dict) -> None:
    if graph["repo"] != {key: inventory["repo"][key] for key in ("name", "commit", "dirty")}:
        raise IntegrityError("inventory and graph snapshots do not match")
    if graph.get("schema_version") == "1.0":
        if graph.get("inventory_sha256") != inventory_digest(inventory):
            raise IntegrityError("inventory and graph evidence hashes do not match")
    else:
        hashes = {entry["path"]: entry.get("sha256") for entry in inventory["files"]}
        for node in graph["nodes"]:
            if node.get("type") == "file" and hashes.get(node["id"][5:]) != node.get("sha256"):
                raise IntegrityError("legacy graph file hash does not match inventory")
