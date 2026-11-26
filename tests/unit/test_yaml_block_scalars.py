"""Block scalars (`|`, `>`) are read only when a caller asks for them."""
import re
from pathlib import Path

from arche_lib.langs.yaml_reader import MAX_BLOCK_CHARS, scalars

ROOT = Path(__file__).resolve().parents[2]
# Compose, GitHub Actions and Helm shapes whose old output must survive with block scalars on.
CORPUS = [
    "services:\n  web:\n    build: ./web\n    command: >\n      sh -c 'run ./x.py'\n    volumes: [./a:/a]\n",
    ("jobs:\n  test:\n    steps:\n      - uses: ./.github/actions/setup\n      - run: |\n"
     "          python ci/check.py\n          - not: an item\n      - name: after\n        with:\n"
     "          path: ${{ matrix.dir }}\n"),
    "image:\n  repository: x\nnotes: |-\n  {{ .Values.x }}\n\n  more\nnext: ./kept\n",
    "a: |2\n    keep\nb: >+\n  folded\n\nc: 'it''s'\nd: \"two\n  lines\"\ne: ok\n",
    "run: |\n  context: ./not-a-key\n  - ./not-an-item\nbase: &b ./base\ntagged: !Ref ./tagged\n",
]


def _values(text):
    documents, degraded = scalars(text, block_scalars=True)
    return [(key, value, line) for document in documents for key, value, line in document], degraded


def test_literal_keeps_line_breaks_and_folded_joins_lines():
    text = ("a: |\n"
            "  one\n"
            "  two\n"
            "b: >\n"
            "  one\n"
            "  two\n"
            "\n"
            "  three\n"
            "c: done\n")

    assert _values(text) == ([(("a",), "one\ntwo\n", 1), (("b",), "one two\nthree\n", 4), (("c",), "done", 9)], False)


def test_chomping_indicators_strip_clip_or_keep_the_final_breaks():
    text = "s: |-\n  x\n\nc: |\n  x\n\nk: |+\n  x\n\n\nend: 1\n"

    assert [(key[0], value) for key, value, _ in _values(text)[0]] == [
        ("s", "x"), ("c", "x\n"), ("k", "x\n\n\n"), ("end", "1")]


def test_an_indentation_indicator_sets_the_body_indentation():
    text = "a: |2\n    deeper\n  base\nb: >-1\n  lead\n x\n"

    assert [(key[0], value) for key, value, _ in _values(text)[0]] == [("a", "  deeper\nbase\n"), ("b", " lead x")]


def test_hash_in_the_body_is_text_and_a_shallower_line_ends_the_body():
    text = ("models:\n"
            "  - name: orders\n"
            "    description: >\n"
            "      # not a comment\n"
            "      key: not a key\n"
            "    columns:\n"
            "      - name: id\n")

    assert _values(text)[0] == [
        (("models", 0, "name"), "orders", 2),
        (("models", 0, "description"), "# not a comment key: not a key\n", 3),
        (("models", 0, "columns", 0, "name"), "id", 7),
    ]


def test_a_block_scalar_sequence_item_and_a_tagged_block_are_read():
    text = "items:\n  - |\n    first\n  - second\nrun: !Sub |\n  echo\n"

    assert _values(text)[0] == [(("items", 0), "first\n", 2), (("items", 1), "second", 4), (("run",), "echo\n", 5)]


def test_long_block_scalars_are_cut_and_reported():
    cut = []
    text = "d: |\n" + "".join(f"  line {i:05d} of a long text\n" for i in range(400)) + "e: 1\n"

    documents, _ = scalars(text, block_scalars=True, truncated=cut)

    assert len(documents[0][0][1]) == MAX_BLOCK_CHARS
    assert documents[0][1] == (("e",), "1", 402)
    assert cut == [(("d",), 1)]


def test_template_lines_and_document_markers_keep_their_old_meaning():
    text = "a: >\n  {{ doc('x') }}\n{% if y %}\nb: 1\n---\nc: |\n  z\n"
    documents, degraded = scalars(text, block_scalars=True)

    assert documents == [[(("a",), "{{ doc('x') }}\n", 1), (("b",), "1", 4)], [(("c",), "z\n", 6)]]
    assert degraded is True


def test_turning_block_scalars_on_only_adds_their_values():
    texts = [path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("evals/fixtures/*/repo/**/*.y*ml"))] + CORPUS
    header = re.compile(r"[|>][-+1-9]*$")

    for text in texts:
        lines = text.splitlines()
        off, off_degraded = scalars(text)
        on, on_degraded = scalars(text, block_scalars=True)
        flat_off = [item for document in off for item in document]
        added = [item for document in on for item in document if item not in flat_off]
        assert scalars(text, block_scalars=False) == (off, off_degraded)
        assert on_degraded == off_degraded
        assert [item for document in on for item in document if item not in added] == flat_off
        assert all(header.search(lines[line - 1].rstrip()) for _, _, line in added)
