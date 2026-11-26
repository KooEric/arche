"""Deterministic JSON output and checked JSON input."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, List, TextIO

from .errors import IntegrityError


_CHUNKS_PER_WRITE = 4096  # encoder pieces joined per write: few calls, little held at once


def _encoder() -> json.JSONEncoder:
    return json.JSONEncoder(sort_keys=True, indent=2, ensure_ascii=False)


def dumps(data: Any) -> str:
    """Serialize so that equal data always produces identical bytes."""
    return _encoder().encode(data) + "\n"


def write_json(path: Path, data: Any) -> None:
    """Write the bytes `dumps` gives, streamed so a large document is never one string in memory."""
    def write(handle: TextIO) -> None:
        pieces: List[str] = []
        for piece in _encoder().iterencode(data):
            pieces.append(piece)
            if len(pieces) >= _CHUNKS_PER_WRITE:
                handle.write("".join(pieces))
                pieces = []
        pieces.append("\n")
        handle.write("".join(pieces))

    _write_atomic(path, write)


def write_text(path: Path, content: str) -> None:
    _write_atomic(path, lambda handle: handle.write(content))


def _write_atomic(path: Path, write: Callable[[TextIO], Any]) -> None:
    """Write atomically: a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle_fd, temp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="\n") as handle:
            write(handle)
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
    except RecursionError:
        raise IntegrityError(f"invalid JSON in {path}: nested too deeply") from None
    except UnicodeDecodeError as exc:
        raise IntegrityError(f"invalid JSON in {path}: not UTF-8 ({exc.reason} at byte {exc.start})") from None


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
