"""Find datasets under a data folder. A dataset is one file, or one folder of parts or partitions."""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from . import MAX_DATASETS, MAX_FILES, clean_text

FORMATS = {".csv": "csv", ".tsv": "tsv", ".tab": "tsv", ".json": "json", ".jsonl": "jsonl",
           ".ndjson": "jsonl", ".parquet": "parquet", ".parq": "parquet"}
SUCCESS_MARKER = "_SUCCESS"
_PARTITION = re.compile(r"^[^=]+=.*$")
_NUMBERED = re.compile(r"^(.*?)\d+$")
# Content-hash names such as 106c94b0c61384ad, written by idempotent ingesters. The length floor keeps words that
# happen to be hex (cafe, face, added) from grouping.
_HEX_NAME = re.compile(r"^[0-9a-fA-F]{8,}$")


class DataFile(NamedTuple):
    rel: str          # POSIX path relative to the data folder
    path: Path        # absolute path, opened for reading only
    size: int
    mtime_ns: int
    partition: Tuple[Tuple[str, str], ...]   # (key, value) from key=value folders, outermost first


class Dataset(NamedTuple):
    record: dict
    files: List[DataFile]


class _Folder(NamedTuple):
    files: List[DataFile]       # data files directly inside
    folders: List[str]          # subfolder names, sorted
    success: bool               # holds a _SUCCESS marker


def format_of(name: str) -> Optional[str]:
    return FORMATS.get(os.path.splitext(name)[1].lower())


def fingerprint(files: List[DataFile]) -> str:
    lines = sorted(f"{item.rel}\t{item.size}\t{item.mtime_ns}" for item in files)
    return hashlib.sha256("\n".join(lines).encode("utf-8", errors="surrogateescape")).hexdigest()


def restat(files: List[DataFile]) -> str:
    """The fingerprint the same files have now; a missing file counts as size -1."""
    current = []
    for item in files:
        try:
            info = os.stat(item.path, follow_symlinks=False)
            current.append(item._replace(size=info.st_size, mtime_ns=info.st_mtime_ns))
        except OSError:
            current.append(item._replace(size=-1, mtime_ns=-1))
    return fingerprint(current)


def _walk(root: Path, skipped: Dict[str, int]) -> Tuple[Dict[str, _Folder], bool]:
    """Every folder under root by relative path ("" is root). Stops after MAX_FILES data files."""
    folders: Dict[str, _Folder] = {}
    pending = [""]
    count = 0
    while pending:
        rel = pending.pop()
        files, subfolders, success = [], [], False
        try:
            entries = sorted(os.scandir(root / rel) if rel else os.scandir(root), key=lambda entry: entry.name)
        except OSError:
            skipped["unreadable"] = skipped.get("unreadable", 0) + 1
            continue
        for entry in entries:
            name = entry.name
            child = clean_text(f"{rel}/{name}" if rel else name)
            if name == SUCCESS_MARKER:
                success = True
            try:
                if entry.is_symlink():
                    skipped["symlink"] = skipped.get("symlink", 0) + 1
                elif name.startswith((".", "_")):
                    skipped["hidden"] = skipped.get("hidden", 0) + 1
                elif entry.is_dir(follow_symlinks=False):
                    subfolders.append(name)
                elif format_of(name) is None:
                    skipped["unsupported"] = skipped.get("unsupported", 0) + 1
                else:
                    info = entry.stat(follow_symlinks=False)
                    if info.st_size == 0:
                        skipped["empty"] = skipped.get("empty", 0) + 1
                        continue
                    if count >= MAX_FILES:
                        folders[rel] = _Folder(files, subfolders, success)
                        return folders, True
                    count += 1
                    files.append(DataFile(child, Path(entry.path), info.st_size, info.st_mtime_ns, ()))
            except OSError:
                skipped["unreadable"] = skipped.get("unreadable", 0) + 1
        folders[rel] = _Folder(files, subfolders, success)
        pending.extend(f"{rel}/{name}" if rel else name for name in reversed(subfolders))
    return folders, False


def _below(folders: Dict[str, _Folder], rel: str) -> List[str]:
    """rel and every folder under it that was walked, outer folders first."""
    keys = [rel]
    for name in folders[rel].folders:
        child = f"{rel}/{name}" if rel else name
        if child in folders:
            keys.extend(_below(folders, child))
    return keys


def _formats(folders: Dict[str, _Folder], keys: List[str]) -> set:
    return {format_of(item.rel) for key in keys for item in folders[key].files}


def _is_partitioned(folders: Dict[str, _Folder], rel: str) -> bool:
    folder = folders[rel]
    inner = [key for key in _below(folders, rel) if key != rel]
    if folder.files or not folder.folders or not inner:
        return False
    if not all(_PARTITION.match(key.rsplit("/", 1)[-1]) for key in inner):
        return False
    return len(_formats(folders, inner)) == 1


def _is_parts(folder: _Folder) -> bool:
    if folder.folders or len(folder.files) < 2 or len({format_of(item.rel) for item in folder.files}) != 1:
        return False
    stems = [os.path.splitext(item.rel.rsplit("/", 1)[-1])[0] for item in folder.files]
    if all(stem.startswith("part-") for stem in stems):
        return True
    if all(_HEX_NAME.match(stem) for stem in stems) and len({len(stem) for stem in stems}) == 1:
        return True
    prefixes = {match.group(1) for match in map(_NUMBERED.match, stems) if match}
    return len(prefixes) == 1 and all(_NUMBERED.match(stem) for stem in stems)


def _partition(rel: str, folder_rel: str) -> Tuple[Tuple[str, str], ...]:
    inner = rel[len(folder_rel) + 1:] if folder_rel else rel
    return tuple(tuple(part.split("=", 1)) for part in inner.split("/")[:-1] if "=" in part)


def _folder_dataset(root_id: str, folders: Dict[str, _Folder], rel: str) -> Dataset:
    keys = _below(folders, rel)
    files = sorted((item._replace(partition=_partition(item.rel, rel)) for key in keys for item in folders[key].files),
                   key=lambda item: item.rel)
    partitions: List[str] = []
    for item in files:
        for key, _ in item.partition:
            if key not in partitions:
                partitions.append(key)
    return Dataset(_record(root_id, rel or ".", "folder", format_of(files[0].rel), files, partitions), files)


def _record(root_id: str, rel: str, kind: str, fmt: str, files: List[DataFile], partitions: List[str]) -> dict:
    rel = clean_text(rel)
    return {"id": f"{root_id}:{rel}", "root": root_id, "path": rel, "kind": kind, "format": fmt,
            "files": len(files), "bytes": sum(item.size for item in files), "partitions": partitions,
            "fingerprint": fingerprint(files)}


def _collect(root_id: str, folders: Dict[str, _Folder], rel: str, found: List[Dataset]) -> None:
    folder = folders.get(rel)
    if folder is None:
        return
    whole = _is_partitioned(folders, rel) or _is_parts(folder) or (
        folder.success and len(_formats(folders, _below(folders, rel))) == 1 and folder.files)
    if whole:
        found.append(_folder_dataset(root_id, folders, rel))
        return
    for item in folder.files:
        found.append(Dataset(_record(root_id, item.rel, "file", format_of(item.rel), [item], []), [item]))
    for name in folder.folders:
        _collect(root_id, folders, f"{rel}/{name}" if rel else name, found)


def discover(root: Path, root_id: str) -> Tuple[List[Dataset], Dict[str, int], bool]:
    """(datasets sorted by path, skipped counts by reason, truncated)."""
    skipped: Dict[str, int] = {}
    folders, truncated = _walk(root, skipped)
    found: List[Dataset] = []
    _collect(root_id, folders, "", found)
    found.sort(key=lambda dataset: dataset.record["path"])
    if len(found) > MAX_DATASETS:
        found, truncated = found[:MAX_DATASETS], True
    return found, dict(sorted(skipped.items())), truncated
