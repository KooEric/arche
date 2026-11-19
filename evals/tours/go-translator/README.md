# Synthetic Go translator branches

This fixture is synthetic. Paths and SHA-256 digests in the tours are relative to `evals/fixtures/go-translator/repo`.

Both cases start at `main`, which selects a nil translator by default and an uppercase translator when `TRANSLATOR_ENABLED=1`, then calls `app.Run`. `run` calls `translate`. With a nil interface, `fallback` returns the input unchanged; with the concrete uppercase translator, `dispatch` invokes its `Translate` method and `uppercase` returns the transformed string. The interface call resolves to this implementation only under the stated configuration; it is not an unconditional static call. These alternatives are separate tours, not simultaneous branches. The fixture models no OS-specific implementation.

Validate the source hashes and ranges with `tests/evals/test_go_translator_tours.py`. Score a branch with `python3 evals/score_tour.py evals/tours/go-translator/absent-case.json evals/tours/go-translator/absent.json` (or replace `absent` with `present`). The scorer checks IDs and edges; source evidence validation is separate. The tests also check a missing step and an erroneous cross-branch call, and Go tests exercise both runtime branches.
