"""Datasets are single files, or whole folders of parts or partitions."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills/arche/scripts"))
from arche_lib.data import discover as module
from arche_lib.data.discover import discover, restat


def _tree(root, files):
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


def _ids(root):
    datasets, skipped, truncated = discover(root, "d0")
    return [d.record["id"] for d in datasets], skipped, truncated


def test_single_files_are_datasets_and_skips_are_counted(tmp_path):
    root = _tree(tmp_path, {"a.csv": "x\n1\n", "s/b.jsonl": "{}\n", "notes.txt": "x", ".hidden.csv": "x\n",
                            "_tmp/c.csv": "x\n", "empty.csv": ""})
    (root / "link.csv").symlink_to(root / "a.csv")

    ids, skipped, truncated = _ids(root)

    assert ids == ["d0:a.csv", "d0:s/b.jsonl"]
    assert skipped == {"empty": 1, "hidden": 2, "symlink": 1, "unsupported": 1}
    assert truncated is False


def test_partition_folders_make_one_dataset_with_their_keys(tmp_path):
    root = _tree(tmp_path, {"acc/ledger/dt=1/region=eu/p.csv": "x\n1\n", "acc/ledger/dt=2/region=us/p.csv": "x\n2\n"})

    datasets, _, _ = discover(root, "d0")

    assert [d.record["id"] for d in datasets] == ["d0:acc/ledger"]
    record = datasets[0].record
    assert (record["kind"], record["format"], record["files"], record["partitions"]) == ("folder", "csv", 2, ["dt", "region"])
    assert datasets[0].files[0].partition == (("dt", "1"), ("region", "eu"))


def test_numbered_parts_and_success_markers_make_folder_datasets(tmp_path):
    root = _tree(tmp_path, {"a/part-0.csv": "x\n", "a/part-1.csv": "x\n", "b/ev_000.jsonl": "{}\n",
                            "b/ev_001.jsonl": "{}\n", "c/_SUCCESS": "", "c/out.parquet": "PAR1",
                            "d/one.csv": "x\n", "d/two.csv": "x\n"})

    ids, _, _ = _ids(root)

    assert ids == ["d0:a", "d0:b", "d0:c", "d0:d/one.csv", "d0:d/two.csv"]


def test_mixed_formats_under_partitions_are_not_grouped(tmp_path):
    root = _tree(tmp_path, {"t/dt=1/a.csv": "x\n", "t/dt=2/a.jsonl": "{}\n"})

    assert _ids(root)[0] == ["d0:t/dt=1/a.csv", "d0:t/dt=2/a.jsonl"]


def test_the_file_limit_truncates(tmp_path, monkeypatch):
    root = _tree(tmp_path, {f"{name}.csv": "x\n" for name in "abcde"})
    monkeypatch.setattr(module, "MAX_FILES", 3)

    ids, _, truncated = _ids(root)

    assert len(ids) == 3 and truncated is True


def test_fingerprint_is_stable_and_changes_with_the_files(tmp_path):
    root = _tree(tmp_path, {"a.csv": "x\n1\n"})
    first = discover(root, "d0")[0][0]
    assert discover(root, "d0")[0][0].record["fingerprint"] == first.record["fingerprint"]

    (root / "a.csv").write_text("x\n1\n2\n", encoding="utf-8")
    os.utime(root / "a.csv", ns=(1, 1))

    assert restat(first.files) != first.record["fingerprint"]


def test_fingerprint_accepts_a_surrogate_escaped_name():
    from pathlib import Path
    from arche_lib.data.discover import DataFile, fingerprint
    item = DataFile(os.fsdecode(b"bad\xff.csv"), Path("/x"), 1, 1, ())

    assert len(fingerprint([item])) == 64
